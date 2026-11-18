"""建行理财产品公告数据源。

建行官网的「产品存续期公告」系统提供公开接口，无需登录：
  1. 按产品名称检索公告  -> TXCODE=NLCQ88（纯 JSON，可直接请求）
  2. 下载某条公告正文    -> TXCODE=NLCQ89（返回 PDF，需要先建立 tran 会话）

净值公告 PDF 里逐日列出：净值日期 / 份额净值 / 七日年化收益率 / 每万份收益。
这是官方披露口径，比抓网页更稳、更全。
"""
import io
import json
import re
import time
from datetime import date, datetime, timedelta

import requests
from pypdf import PdfReader

BASE = "https://ccb.com/tran/WCCMainPlatV5"
LIST_PAGE = "https://ccb.com/chn/home/finance/index/cpgg/cpcxqgg/index.shtml"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")
COMMON = {
    "CCB_IBSVersion": "V5",
    "SERVLET_NAME": "WCCMainPlatV5",
    "isAjaxRequest": "true",
}

NOTICE_RATE = "05"   # 一级公告：净值公告


class NoticeError(RuntimeError):
    pass


def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": UA, "Referer": LIST_PAGE})
    s.get(LIST_PAGE, timeout=20)
    # 先打一次理财公司列表接口，服务端才会下发 tranCCBIBS1 会话
    s.get(BASE, params={**COMMON, "TXCODE": "NLDQ71",
                        "FndCo_Agnc_Sale_InsID": "005",
                        "PAGE_JUMP": "1", "REC_IN_PAGE": "50"}, timeout=20)
    return s


def search_products(keyword: str, days: int = 10) -> list[dict]:
    """按产品名称关键字检索，返回去重后的产品（每个产品一条最新净值公告）。"""
    keyword = (keyword or "").strip()
    if not keyword:
        return []

    s = _session()
    end = datetime.now()
    start = end - timedelta(days=max(days, 1))

    items: list[dict] = []
    page = 1
    while page <= 6:
        params = {
            **COMMON, "TXCODE": "NLCQ88", "Mnplt_Tp_Ind": "1",
            "PAGE_JUMP": str(page), "REC_IN_PAGE": "50",
            "IntAr_StDt": start.strftime("%Y%m%d"),
            "IntAr_CODt": end.strftime("%Y%m%d"),
            "Fnd_Nm": keyword,
            "Prim_CL_ID": NOTICE_RATE,
        }
        resp = s.get(BASE, params=params, timeout=25)
        resp.raise_for_status()
        try:
            data = resp.json()
        except ValueError:
            break
        batch = data.get("PD_Inf_Grp") or []
        if not batch:
            break
        items.extend(batch)
        try:
            total = int(data.get("TOTAL_REC") or 0)
        except ValueError:
            total = 0
        if len(items) >= total or len(batch) < 50:
            break
        page += 1
        time.sleep(0.4)

    # 每个产品只保留最新一条公告
    newest: dict[str, dict] = {}
    for it in items:
        code = (it.get("IvsmPd_ECD") or "").strip()
        if not code:
            continue
        cur = newest.get(code)
        if cur is None or str(it.get("Inf_Dt") or "") > str(cur.get("Inf_Dt") or ""):
            newest[code] = it

    out = [
        {
            "code": code,
            "name": (it.get("Fnd_Nm") or it.get("Inf_Ttl_Nm") or "").strip(),
            "date": _iso(it.get("Inf_Dt")),
            "token": it.get("Itt_Fcn_Dsc"),
        }
        for code, it in newest.items()
    ]
    out.sort(key=lambda x: (_period(x["name"]), x["name"]))
    return out


def _period(name: str) -> int:
    m = re.search(r"第(\d+)期", name)
    return int(m.group(1)) if m else 999


def _iso(raw) -> str:
    raw = str(raw or "").strip()
    if len(raw) >= 8 and raw[:8].isdigit():
        return f"{raw[0:4]}-{raw[4:6]}-{raw[6:8]}"
    return ""


def fetch_pdf(token: str) -> bytes:
    """下载公告正文 PDF。"""
    if not token:
        raise NoticeError("公告缺少编号")
    s = _session()
    resp = s.get(BASE, params={**COMMON, "TXCODE": "NLCQ89", "Itt_Fcn_Dsc": token}, timeout=30)
    resp.raise_for_status()
    if not resp.content.startswith(b"%PDF"):
        raise NoticeError("公告正文不是 PDF，可能需要在建行官网重新查询")
    return resp.content


LINE_DATE = re.compile(r"^\s*(\d{4})年(\d{1,2})月(\d{1,2})日(.*)$")


def parse_pdf(pdf: bytes) -> list[dict]:
    """解析净值公告 PDF，返回 [{date, rate(百分比), wanfen}]。"""
    try:
        reader = PdfReader(io.BytesIO(pdf))
    except Exception as exc:  # noqa: BLE001
        raise NoticeError(f"PDF 解析失败：{exc}") from exc

    text = "\n".join((p.extract_text() or "") for p in reader.pages)
    rows: list[dict] = []

    for line in text.splitlines():
        m = LINE_DATE.match(line)
        if not m:
            continue
        y, mo, d = (int(x) for x in m.groups()[:3])
        try:
            day = date(y, mo, d).isoformat()
        except ValueError:
            continue
        tail = m.group(4)

        pct = re.search(r"(\d+(?:\.\d+)?)\s*%", tail)
        if not pct:
            continue
        rate = float(pct.group(1))

        wanfen = None
        after = tail[pct.end():].strip()
        num = re.search(r"(\d+(?:\.\d+)?)", after)
        if num:
            wanfen = float(num.group(1))

        rows.append({"date": day, "rate": rate, "wanfen": wanfen})

    # 同一日期可能重复出现，保留最后一条
    uniq = {r["date"]: r for r in rows}
    return sorted(uniq.values(), key=lambda r: r["date"])


def preview(pdf: bytes) -> dict:
    rows = parse_pdf(pdf)
    head = re.findall(r"建信理财[^\n]{0,60}", (PdfReader(io.BytesIO(pdf)).pages[0].extract_text() or ""))
    return {"rows": rows, "title": head[0].strip() if head else ""}
