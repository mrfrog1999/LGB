"""浦银理财（浦发银行）净值抓取模块。

数据源（2026-11 实测）：
  https://www.spdb-wm.com/financialProducts/  产品信息查询页
  POST https://www.spdb-wm.com/api/search     JSON 接口

  chlid=1007  全量母子产品列表（约 1.5 万条，产品搜索在本地做）
  chlid=1008  份额净值序列，searchword 形如 (subShareCode='2301269143')
              字段：netValueDate=20261116, yieldSevenDate=1.442802（%，已是百分数）
                    netRevPer10000=0.391（每万份收益）, export_date=公告日

  注意：用户持有的三只均为「理财宝」销售份额，产品代码要用 saleCode（10 位子类代码），
        不能用母产品 prodCode，否则取不到对应份额的七日年化。
"""
import json
import subprocess
import threading
import time
import logging
from datetime import datetime, timedelta

import config

logger = logging.getLogger("spdb")

_PRODUCTS_CACHE = {"at": 0, "data": []}
_CACHE_LOCK = threading.Lock()
CACHE_SECONDS = 600


def _api(chlid, searchword="", size=20, page=1, timeout=120):
    """调用 spdb-wm 搜索接口，返回 content 列表与 totalElements。"""
    body = {
        "chlid": chlid,
        "cutsize": 150,
        "dynexpr": [],
        "dynidx": 1,
        "extopt": [],
        "orderby": "",
        "page": page,
        "size": size,
        "searchword": searchword or "",
    }
    cmd = [
        "curl", "-sS", "--max-time", str(timeout), "-A", config.UA,
        "-H", "Content-Type: application/json",
        "-H", "Origin: https://www.spdb-wm.com",
        "-e", "https://www.spdb-wm.com/financialProducts/",
        "-X", "POST", config.SPDB_SEARCH_URL,
        "-d", json.dumps(body, ensure_ascii=False),
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=timeout + 10)
        if r.returncode != 0:
            err = r.stderr.decode("utf-8", errors="ignore").strip()
            raise RuntimeError(f"浦银理财站点请求失败(curl exit {r.returncode}): {err[:180]}")
    except subprocess.TimeoutExpired:
        raise RuntimeError("浦银理财站点请求超时")
    text = r.stdout.decode("utf-8", errors="ignore")
    try:
        res = json.loads(text)
    except Exception:
        raise RuntimeError(f"浦银理财接口返回异常: {text[:150]}")
    if res.get("code") != 20000 or not isinstance(res.get("data"), dict):
        raise RuntimeError(f"浦银理财接口错误: {res.get('message') or text[:120]}")
    data = res["data"]
    return data.get("content") or [], int(data.get("totalElements") or 0)


def _all_products():
    """全量产品列表，带 10 分钟内存缓存（原始数据约 5MB）。"""
    with _CACHE_LOCK:
        if _PRODUCTS_CACHE["data"] and time.time() - _PRODUCTS_CACHE["at"] < CACHE_SECONDS:
            return _PRODUCTS_CACHE["data"]
        items, total = _api(1007, size=99999)
        if not items:
            raise RuntimeError(f"浦银理财产品列表为空（接口总数 {total}）")
        _PRODUCTS_CACHE["data"] = items
        _PRODUCTS_CACHE["at"] = time.time()
        logger.info(f"浦银理财产品列表已加载 {len(items)} 条")
        return items


def _norm_date(s):
    """20261116 -> 2026-11-16"""
    s = str(s or "").strip()
    if len(s) == 8 and s.isdigit():
        return f"{s[:4]}-{s[4:6]}-{s[6:]}"
    return ""


def _to_float(v):
    if v in (None, ""):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _product_item(r):
    return {
        "product_code": r.get("saleCode") or r.get("prodCode") or "",
        "product_name": r.get("saleName") or r.get("prodName") or "",
        "share_code": r.get("saleCode") or "",
        "mother_code": r.get("prodCode") or "",
        "share_type": r.get("subFlag") or "",
        "regist_code": r.get("registCode") or "",
        "nav_date": r.get("export_date") or "",
        "latest_date": r.get("export_date") or "",
    }


def _rank(item, kw):
    """匹配度排序：理财宝优先，其次是精确名称 / 母产品名称。"""
    name = item["product_name"]
    score = 0
    if "理财宝" in name:
        score -= 100
    if name == kw:
        score -= 200
    elif name.startswith(kw):
        score -= 50
    return score, len(name), name


# ---------------- 对外接口 ----------------
def search_products(keyword, page=1, page_size=20):
    """按名称/代码搜索浦银理财产品，返回 (列表, 总数, 页数)。"""
    kw = (keyword or "").strip()
    items = _all_products()
    if kw:
        low = kw.lower()
        hits = [r for r in items if low in (
            (r.get("saleName") or "") + " " + (r.get("prodName") or "") + " "
            + (r.get("saleCode") or "") + " " + (r.get("prodCode") or "")
        ).lower()]
    else:
        hits = items
    hits = sorted(hits, key=lambda r: _rank(_product_item(r), kw))
    products = [_product_item(r) for r in hits if r.get("saleCode") or r.get("prodCode")]
    total = len(products)
    start = (page - 1) * page_size
    return products[start:start + page_size], total, max(1, (total + page_size - 1) // page_size)


def get_product(keyword_or_code):
    """精确解析产品：代码优先按销售份额代码，名称优先「理财宝」份额。"""
    key = (keyword_or_code or "").strip()
    items = _all_products()
    if key.isdigit() and len(key) in (10, 16):
        # 优先「可取值」的销售份额代码：saleCode 命中且为 S 类子份额
        share = [r for r in items if (r.get("saleCode") or "") == key and r.get("subFlag") == "S"]
        if share:
            return _product_item(share[0])
        # 其次母产品代码：在子份额中优先理财宝
        mothers = [r for r in items if (r.get("prodCode") or "") == key and r.get("subFlag") == "S"]
        if mothers:
            mothers.sort(key=lambda r: _rank(_product_item(r), key))
            return _product_item(mothers[0])
        # 最后退回母产品自身（通常无每日净值，仅供展示）
        hit = next((r for r in items if (r.get("saleCode") or "") == key), None)
        if hit:
            return _product_item(hit)
    exact = [r for r in items if (r.get("saleName") or "") == key]
    if exact:
        exact.sort(key=lambda r: _rank(_product_item(r), key))
        return _product_item(exact[0])
    products, _, _ = search_products(key, page_size=50)
    return products[0] if products else None


def _parse_nav(item):
    return {
        "nav_date": _norm_date(item.get("netValueDate")),
        "annual_rate": _to_float(item.get("yieldSevenDate")),
        "per_10k": _to_float(item.get("netRevPer10000")),
        "pub_date": item.get("export_date") or "",
    }


def fetch_latest_notice(keyword_or_code):
    """取最新一期净值，返回 (records, meta)。"""
    prod = get_product(keyword_or_code)
    if not prod or not prod["share_code"]:
        raise RuntimeError(f"未找到浦银理财产品: {keyword_or_code}")
    code = prod["share_code"]
    items, _ = _api(1008, f"(subShareCode='{code}')", size=10)
    records = []
    for it in items:
        r = _parse_nav(it)
        if r["nav_date"] and (r["annual_rate"] is not None or r["per_10k"] is not None):
            records.append(r)
    if not records:
        raise RuntimeError(f"产品 {prod['product_name']}({code}) 暂无净值数据")
    records.sort(key=lambda x: x["nav_date"])
    meta = {
        "product_code": code,
        "title": prod["product_name"],
        "pub_date": records[-1].get("pub_date") or "",
        "count": len(records),
        "latest_date": records[-1]["nav_date"],
    }
    return [records[-1]], meta


def fetch_history(keyword_or_code, max_months=3, progress=None):
    """回溯近 N 个月每日净值（现金管理类每天一条）。"""
    prod = get_product(keyword_or_code)
    if not prod or not prod["share_code"]:
        raise RuntimeError(f"未找到浦银理财产品: {keyword_or_code}")
    code = prod["share_code"]
    items, total = _api(1008, f"(subShareCode='{code}')", size=999)
    if progress:
        progress(f"浦银理财返回 {total} 条净值记录")
    cutoff = (datetime.now() - timedelta(days=int(max_months * 31))).strftime("%Y-%m-%d")
    seen = {}
    for it in items:
        r = _parse_nav(it)
        if r["nav_date"] >= cutoff and (r["annual_rate"] is not None or r["per_10k"] is not None):
            seen[r["nav_date"]] = {"nav_date": r["nav_date"],
                                   "annual_rate": r["annual_rate"],
                                   "per_10k": r["per_10k"]}
    records = sorted(seen.values(), key=lambda x: x["nav_date"])
    if progress:
        progress(f"浦银理财 {prod['product_name']} 解析 {len(records)} 天净值")
    return records


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print("== 搜索 ==")
    for p in search_products("天添利现金宝183号")[0][:6]:
        print(p["product_code"], p["product_name"], p["share_type"])
    print("== 理财宝份额解析 ==")
    for n in ("天添利现金宝183号（理财宝）", "天添利现金宝26号（理财宝）", "天添利现金宝35号（理财宝）"):
        print(n, "->", get_product(n)["product_code"])
    print("== 最新 ==")
    for c in ("2301269143", "2301260695", "2301260696"):
        print(c, fetch_latest_notice(c))
    print("== 历史 ==")
    h = fetch_history("2301269143", progress=lambda m: print(m))
    print("天数", len(h), h[-2:])
