"""建行理财净值公告抓取模块。

数据源机制（已实测验证）：
  1. 建行站点用旧版 TLS 重协商 + 自签证书链，Python requests/urllib3 在 OpenSSL3 下不稳定，
     因此传输层统一用 curl 子进程（稳定可靠），cookie jar 维持会话。
  2. 同一会话先访问公告列表页建立 cookie；
  3. TXCODE=NLCQ88 按产品名搜索，拿每条公告的 Itt_Fcn_Dsc（与会话绑定、有时效）；
  4. 同会话立即请求 TXCODE=NLCQ89 + Itt_Fcn_Dsc，返回公告 PDF 二进制；
  5. pdfplumber 解析 PDF，提取「净值日期 / 七日年化 / 每万份收益」。
"""
import io
import os
import re
import subprocess
import tempfile
import logging
from datetime import date, timedelta

import pdfplumber

import config

logger = logging.getLogger("ccb")


# ---------------- curl 传输层 ----------------
class CCBSession:
    """用 curl 子进程维持的会话，cookie jar 保存会话状态。"""

    def __init__(self):
        fd, self.jar = tempfile.mkstemp(prefix="ccbjar_")
        os.close(fd)
        try:
            os.unlink(self.jar)  # 让 curl 从空 jar 开始
        except OSError:
            pass

    def _run(self, args, binary=False):
        cmd = ["curl", "-sSL", "--max-time", "30", "-A", config.UA,
               "-b", self.jar, "-c", self.jar,
               "-H", "Accept-Language: zh-CN,zh;q=0.9"]
        cmd += args
        try:
            r = subprocess.run(cmd, capture_output=True, timeout=60)
        except subprocess.TimeoutExpired:
            raise RuntimeError("建行站点请求超时")
        if r.returncode != 0:
            err = r.stderr.decode("utf-8", errors="ignore").strip()
            raise RuntimeError(f"建行站点请求失败(curl exit {r.returncode}): {err[:180]}")
        return r.stdout if binary else r.stdout.decode("utf-8", errors="ignore")

    def open_list_page(self):
        return self._run([config.CCB_LIST_PAGE])

    def search(self, keyword, page=1, page_size=20, start_date=None, end_date=None):
        args = [
            "-H", "X-Requested-With: XMLHttpRequest",
            "-e", config.CCB_LIST_PAGE,
            "--get",
            "--data-urlencode", "CCB_IBSVersion=V5",
            "--data-urlencode", "SERVLET_NAME=WCCMainPlatV5",
            "--data-urlencode", "isAjaxRequest=true",
            "--data-urlencode", "TXCODE=NLCQ88",
            "--data-urlencode", "Mnplt_Tp_Ind=1",
            "--data-urlencode", "Prim_CL_ID=05",
            "--data-urlencode", f"PAGE_JUMP={page}",
            "--data-urlencode", f"REC_IN_PAGE={page_size}",
            "--data-urlencode", f"Fnd_Nm={keyword or ''}",
        ]
        if start_date:
            args += ["--data-urlencode", f"IntAr_StDt={start_date}"]
        if end_date:
            args += ["--data-urlencode", f"IntAr_CODt={end_date}"]
        args.append("https://ccb.com/tran/WCCMainPlatV5")
        import json
        txt = self._run(args)
        try:
            return json.loads(txt)
        except Exception:
            raise RuntimeError(f"搜索接口返回异常: {txt[:200]}")

    def detail(self, dsc):
        url = config.CCB_DETAIL_URL + dsc
        return self._run(["-e", config.CCB_LIST_PAGE, url], binary=True)

    def close(self):
        try:
            if os.path.exists(self.jar):
                os.unlink(self.jar)
        except OSError:
            pass


def _new_session():
    s = CCBSession()
    s.open_list_page()
    return s


# ---------------- 解析 ----------------
def _parse_pdf(pdf_bytes):
    records = []
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        text = "\n".join((p.extract_text() or "") for p in pdf.pages)
    text = text.replace("\u3000", " ")
    lines = [re.sub(r"\s+", " ", ln).strip() for ln in text.splitlines()]
    date_re = re.compile(r"(\d{4})年(\d{1,2})月(\d{1,2})日")
    pct_re = re.compile(r"(\d+(?:\.\d+)?)\s*%")
    for ln in lines:
        m = date_re.search(ln)
        if not m:
            continue
        y, mo, d = m.group(1), int(m.group(2)), int(m.group(3))
        nav_date = f"{y}-{mo:02d}-{d:02d}"
        pct = pct_re.search(ln)
        if not pct:
            continue
        annual = float(pct.group(1))
        tail = ln[pct.end():].strip()
        per10k = None
        pm = re.match(r"^(-?\d+(?:\.\d+)?)", tail)
        if pm:
            per10k = float(pm.group(1))
        records.append({"nav_date": nav_date, "annual_rate": annual, "per_10k": per10k})
    seen = {}
    for r in records:
        seen[r["nav_date"]] = r
    return sorted(seen.values(), key=lambda x: x["nav_date"])


# ---------------- 对外接口 ----------------
def search_products(keyword, page=1, page_size=20):
    """按产品名搜索，返回按产品代码去重的列表（供前端展示选择）。"""
    s = _new_session()
    try:
        data = s.search(keyword, page, page_size)
    finally:
        s.close()
    items = data.get("PD_Inf_Grp") or []
    products = {}
    for it in items:
        code = it.get("IvsmPd_ECD") or ""
        name = it.get("Fnd_Nm") or it.get("Inf_Ttl_Nm", "")
        if not code:
            continue
        clean = re.sub(r"[（(]代销[^）)]*[）)]", "", name).strip()
        if code not in products:
            products[code] = {
                "product_code": code,
                "product_name": clean,
                "latest_title": it.get("Inf_Ttl_Nm", ""),
                "latest_date": it.get("Inf_Dt", ""),
            }
    return list(products.values()), data.get("TOTAL_REC", len(items)), data.get("TOTAL_PAGE", 1)


def fetch_latest_notice(keyword_or_code):
    """一体化抓取最新公告：同一会话搜索→详情→解析。返回 (records, meta)。"""
    s = _new_session()
    try:
        data = s.search(keyword_or_code, 1, 10)
        items = data.get("PD_Inf_Grp") or []
        notice = next((it for it in items if it.get("IvsmPd_ECD") == keyword_or_code), None)
        if notice is None and items:
            notice = items[0]
        if not notice:
            raise RuntimeError(f"未找到产品公告: {keyword_or_code}")
        dsc = notice.get("Itt_Fcn_Dsc", "")
        if not dsc:
            raise RuntimeError("公告缺少 Itt_Fcn_Dsc")
        pdf = s.detail(dsc)
        if not pdf.startswith(b"%PDF"):
            raise RuntimeError(f"详情未返回PDF (len={len(pdf)})")
        records = _parse_pdf(pdf)
        if not records:
            raise RuntimeError("PDF未解析出净值数据")
        meta = {
            "product_code": notice.get("IvsmPd_ECD") or keyword_or_code,
            "title": notice.get("Inf_Ttl_Nm", ""),
            "pub_date": notice.get("Inf_Dt", ""),
            "count": len(records),
            "latest_date": records[-1]["nav_date"],
        }
        return records, meta
    finally:
        s.close()


def fetch_by_code(product_code):
    return fetch_latest_notice(product_code)


def fetch_history(keyword_or_code, max_months=2, progress=None):
    """回溯抓取历史公告，合并去重。每期公告含约10天数据，翻页累积。"""
    s = _new_session()
    all_records = {}
    try:
        cutoff = (date.today() - timedelta(days=max_months * 30)).strftime("%Y%m%d")
        page = 1
        max_pages = 60
        while page <= max_pages:
            data = s.search(keyword_or_code, page, 20)
            items = data.get("PD_Inf_Grp") or []
            if not items:
                break
            for it in items:
                pub = it.get("Inf_Dt", "")
                if pub and pub < cutoff:
                    continue
                dsc = it.get("Itt_Fcn_Dsc", "")
                if not dsc:
                    continue
                try:
                    pdf = s.detail(dsc)
                    if pdf.startswith(b"%PDF"):
                        for r in _parse_pdf(pdf):
                            all_records[r["nav_date"]] = r
                except Exception:
                    continue
            if progress:
                progress(f"已抓取第 {page} 页公告，累积 {len(all_records)} 天数据")
            total_page = int(data.get("TOTAL_PAGE") or 1)
            if page >= total_page:
                break
            page += 1
    finally:
        s.close()
    return sorted(all_records.values(), key=lambda x: x["nav_date"])


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print("== 搜索 ==")
    prods, total, pages = search_products("建信宝按日开放式产品第4期")
    for p in prods[:5]:
        print(p)
    print("总数", total, "页数", pages)
    print("== 最新公告 ==")
    recs, meta = fetch_latest_notice("建信宝按日开放式产品第4期")
    print(meta)
    for r in recs[:3]:
        print(r)
