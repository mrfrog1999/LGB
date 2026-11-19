"""邮储银行理财净值抓取模块。

数据源机制（已实测验证）：
  1. 邮储站点同样存在旧版 TLS/证书链兼容问题，Python requests 直连报 SSLError，
     因此传输层统一用 curl 子进程（稳定可靠）。
  2. 产品搜索 + 最新净值：JSONP 接口
       https://s.psbc.com/portal/PsbcService/finquick/?finkeyword={关键词}
     返回 empty({...})，字段：
       SECODE=产品代码, FPNAME=名称, SEVEN_ANNUAL_YIELD=七日年化(小数,需×100),
       WF_EARN=每万份收益, OPEN_DATE=净值日(yyyy.mm.dd), docpuburl=详情页链接
  3. 历史净值：产品详情页列出该产品的最新「业绩公告」静态 HTML 链接，
     公告内是表格（净值日期 / 七日年化 / 每万份净收益），解析即可。
"""
import json
import re
import subprocess
import logging
from urllib.parse import urljoin

import config

logger = logging.getLogger("psbc")

FINQUICK_URL = "https://s.psbc.com/portal/PsbcService/finquick/"
SEARCH_REFERER = "https://www.psbc.com/cn/common/search/"


def _curl(url, params=None, referer=SEARCH_REFERER):
    """用 curl 请求，返回文本。"""
    cmd = ["curl", "-sSL", "--max-time", "30", "-A", config.UA,
           "-e", referer, "-H", "Accept-Language: zh-CN,zh;q=0.9"]
    if params:
        cmd += ["--get"]
        for k, v in params.items():
            cmd += ["--data-urlencode", f"{k}={v}"]
    cmd.append(url)
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=60)
    except subprocess.TimeoutExpired:
        raise RuntimeError("邮储站点请求超时")
    if r.returncode != 0:
        err = r.stderr.decode("utf-8", errors="ignore").strip()
        raise RuntimeError(f"邮储站点请求失败(curl exit {r.returncode}): {err[:180]}")
    return r.stdout.decode("utf-8", errors="ignore")


def _jsonp(text):
    """解析 empty({...}) 形式的 JSONP。"""
    t = text.strip()
    m = re.search(r"\((.*)\)\s*$", t, re.S)
    if m:
        t = m.group(1)
    try:
        return json.loads(t)
    except Exception:
        raise RuntimeError(f"邮储接口返回异常: {text[:150]}")


def _norm_date(s):
    """2026.11.16 / 2026-11-16 -> 2026-11-16"""
    if not s:
        return ""
    m = re.search(r"(\d{4})[.\-/](\d{1,2})[.\-/](\d{1,2})", str(s))
    return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}" if m else ""


def _product_from_fin(item):
    """把 finquick 返回项转成统一产品字典。"""
    annual = item.get("SEVEN_ANNUAL_YIELD")
    try:
        annual = round(float(annual) * 100, 4) if annual not in (None, "") else None
    except (TypeError, ValueError):
        annual = None
    per10k = item.get("WF_EARN")
    try:
        per10k = float(per10k) if per10k not in (None, "") else None
    except (TypeError, ValueError):
        per10k = None
    return {
        "product_code": item.get("SECODE", ""),
        "product_name": item.get("FPNAME", ""),
        "annual_rate": annual,
        "per_10k": per10k,
        "nav_date": _norm_date(item.get("OPEN_DATE")),
        "detail_url": item.get("docpuburl", ""),
        "risk_level": item.get("RISKLEVEL", ""),
    }


# ---------------- 对外接口 ----------------
def search_products(keyword, page=1, page_size=20):
    """按产品名/代码搜索邮储在售及存续理财产品。返回 (列表, 总数, 页数)。"""
    data = _jsonp(_curl(FINQUICK_URL, {"finkeyword": keyword or ""}))
    items = data.get("resultList") or []
    products = [_product_from_fin(it) for it in items if it.get("SECODE")]
    return products, int(data.get("totalCount") or len(products)), int(data.get("pageCount") or 1)


def get_product(keyword_or_code):
    """精确取一个产品（优先代码匹配），返回统一字典或 None。"""
    products, _, _ = search_products(keyword_or_code)
    if not products:
        return None
    hit = next((p for p in products if p["product_code"] == keyword_or_code), None)
    return hit or products[0]


def fetch_latest_notice(keyword_or_code):
    """取产品最新一期净值（finquick 已含最新七日年化/万份收益/净值日）。

    返回 (records, meta)，records 元素与建行一致：{nav_date, annual_rate, per_10k}。
    """
    prod = get_product(keyword_or_code)
    if not prod:
        raise RuntimeError(f"未找到邮储产品: {keyword_or_code}")
    if not prod["nav_date"]:
        raise RuntimeError(f"产品 {prod['product_name']} 无净值日期")
    rec = {
        "nav_date": prod["nav_date"],
        "annual_rate": prod["annual_rate"],
        "per_10k": prod["per_10k"],
    }
    meta = {
        "product_code": prod["product_code"],
        "title": prod["product_name"],
        "pub_date": prod["nav_date"],
        "count": 1,
        "latest_date": prod["nav_date"],
    }
    return [rec], meta


def _parse_gonggao(html):
    """解析业绩公告静态 HTML 表格中的每日净值数据。"""
    records = []
    rows = re.findall(r"<tr[^>]*>(.*?)</tr>", html, re.S | re.I)
    for row in rows:
        cells = re.findall(r"<td[^>]*>(.*?)</td>", row, re.S | re.I)
        if len(cells) < 3:
            continue
        texts = [re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", c)).strip() for c in cells]
        nav_date = ""
        annual = None
        per10k = None
        for t in texts:
            d = _norm_date(t)
            if t == d and not nav_date:
                nav_date = d
            pm = re.search(r"(-?\d+(?:\.\d+)?)\s*%", t)
            if pm and annual is None:
                annual = float(pm.group(1))
            if re.fullmatch(r"\d+\.\d{3,4}", t) and per10k is None:
                per10k = float(t)
        if nav_date and (annual is not None or per10k is not None):
            records.append({"nav_date": nav_date, "annual_rate": annual, "per_10k": per10k})
    seen = {}
    for r in records:
        seen[r["nav_date"]] = r
    return sorted(seen.values(), key=lambda x: x["nav_date"])


def fetch_history(keyword_or_code, max_months=2, progress=None):
    """回溯历史公告：从产品详情页找业绩公告链接并解析表格。"""
    prod = get_product(keyword_or_code)
    if not prod or not prod["detail_url"]:
        raise RuntimeError(f"未找到邮储产品详情页: {keyword_or_code}")
    detail_html = _curl(prod["detail_url"], referer="https://www.psbc.com/cn/grfw/tzlc/lc/")
    links = re.findall(r'href="([^"]*ywgg/jzgg/[^"]+\.html)"', detail_html)
    all_records = {}
    for i, link in enumerate(links[:3]):
        url = urljoin(prod["detail_url"], link)
        try:
            html = _curl(url, referer=prod["detail_url"])
            for r in _parse_gonggao(html):
                all_records[r["nav_date"]] = r
        except Exception as e:
            logger.warning(f"解析业绩公告失败 {url}: {e}")
        if progress:
            progress(f"已解析第 {i+1} 条公告，累积 {len(all_records)} 天数据")
    for r in fetch_latest_notice(keyword_or_code)[0]:
        all_records[r["nav_date"]] = r
    return sorted(all_records.values(), key=lambda x: x["nav_date"])


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print("== 搜索 ==")
    prods, total, pages = search_products("天乐宝10号")
    for p in prods[:5]:
        print(p)
    print("== 最新 ==")
    recs, meta = fetch_latest_notice("2301TL0010")
    print(meta, recs)
    print("== 历史 ==")
    hist = fetch_history("2301TL0010", progress=lambda m: print(m))
    print("天数:", len(hist), hist[:2], hist[-2:] if hist else [])
