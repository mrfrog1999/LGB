"""业务编排：产品搜索、抓取入库、收益测算、汇总、邮件。"""
from __future__ import annotations

import re
import time
from datetime import date, datetime, timedelta

from . import calc, db, mailer, notice
from .config import settings


class ServiceError(RuntimeError):
    pass


# ---------------- 初始化 ----------------

def bootstrap() -> None:
    """首次启动时自动订阅一个产品，避免空白页。"""
    if db.list_products(only_subscribed=True):
        return
    if not settings.BOOTSTRAP_CODE:
        return
    db.upsert_product(
        settings.BOOTSTRAP_CODE,
        settings.BOOTSTRAP_NAME or settings.BOOTSTRAP_CODE,
        settings.DEFAULT_PRINCIPAL,
        settings.DEFAULT_START_DATE or None,
    )
    db.add_log("info", f"首次启动，自动订阅 {settings.BOOTSTRAP_NAME}")


# ---------------- 产品搜索 ----------------

PERIOD_RE = re.compile(r"第(\d+)期")


def series_keyword(name: str, code: str = "") -> str:
    """从产品全名里剥离期次，得到用于检索的系列关键词。"""
    name = (name or "").strip()
    m = PERIOD_RE.search(name)
    if m:
        head = name[:m.start()].strip()
        head = re.sub(r"[（(].*?[）)]", "", head).strip()
        if len(head) >= 2:
            return head
    head = re.sub(r"[（(].*?[）)]", "", name).strip()
    return head[:12] or "建信理财"


def search_products(query: str) -> list[dict]:
    keyword = (query or "").strip() or settings.SEARCH_KEYWORD
    try:
        items = notice.search_products(keyword, days=10)
    except Exception as exc:  # noqa: BLE001
        db.add_log("error", f"检索失败：{exc}")
        raise ServiceError(f"检索失败：{exc}") from exc

    existing = {p["code"]: p for p in db.list_products(only_subscribed=False)}
    out = []
    for it in items:
        rec = existing.get(it["code"])
        out.append({
            "code": it["code"],
            "name": it["name"],
            "date": it["date"],
            "subscribed": bool(rec and rec["subscribed"]),
            "principal": rec["principal"] if rec else None,
            "start_date": rec["start_date"] if rec else None,
        })
    db.add_log("info", f"检索「{keyword}」命中 {len(out)} 个产品")
    return out


# ---------------- 抓取 ----------------

def _token_for(code: str, name: str) -> str | None:
    keyword = series_keyword(name, code)
    for attempt in range(settings.FETCH_RETRIES):
        try:
            items = notice.search_products(keyword, days=10)
        except Exception as exc:  # noqa: BLE001
            db.add_log("warn", f"检索 {keyword} 第{attempt + 1}次失败：{exc}")
            time.sleep(1.5)
            continue
        for it in items:
            if it["code"] == code:
                return it["token"]
        if items:
            break
    return None


def sync_product(code: str) -> dict:
    p = db.get_product(code)
    if not p:
        raise ServiceError("产品不存在")

    token = _token_for(code, p["name"])
    if not token:
        raise ServiceError(f"未找到 {p['name']} 的最新净值公告")

    last_err = None
    for attempt in range(settings.FETCH_RETRIES):
        try:
            pdf = notice.fetch_pdf(token)
            rows = notice.parse_pdf(pdf)
            if not rows:
                raise ServiceError("公告里没有解析到收益率数据")
            added = db.upsert_rates(code, rows)
            latest = db.latest_rate(code)
            db.add_log("info", f"{p['name']} 同步成功，{added} 条，最新 {latest['date']} {latest['rate']}%")
            return {"code": code, "name": p["name"], "rows": added, "latest": latest}
        except Exception as exc:  # noqa: BLE001
            last_err = exc
            time.sleep(1.5)
    db.add_log("error", f"{p['name']} 同步失败：{last_err}")
    raise ServiceError(f"同步失败：{last_err}")


def sync_all() -> list[dict]:
    results = []
    for p in db.list_products(only_subscribed=True):
        try:
            results.append(sync_product(p["code"]))
        except Exception as exc:  # noqa: BLE001
            results.append({"code": p["code"], "name": p["name"], "error": str(exc)})
    return results


# ---------------- 测算 ----------------

def build_calc(code: str, principal: float | None = None,
               start_date: str | None = None, with_daily: bool = True) -> dict:
    p = db.get_product(code)
    if not p:
        raise ServiceError("产品不存在")

    rates = db.rates_of(code)
    if not rates:
        raise ServiceError("该产品还没有收益率数据，请先同步")

    principal = settings.DEFAULT_PRINCIPAL if principal is None else float(principal)
    if principal < 0:
        principal = 0.0

    start = (start_date or p["start_date"] or "").strip() or rates[0]["date"]
    try:
        result = calc.compute_series(principal, start, rates)
    except ValueError:
        result = calc.compute_series(principal, rates[0]["date"], rates)

    if not with_daily:
        result.pop("daily", None)

    latest = db.latest_rate(code)
    result.update({
        "code": code,
        "name": p["name"],
        "latest": latest,
        "data_from": rates[0]["date"],
        "data_to": rates[-1]["date"],
        "data_days": len(rates),
        "principal": result.get("principal", principal),
    })
    return result


def overview() -> dict:
    products = []
    total_principal = 0.0
    total_balance = 0.0
    for p in db.list_products(only_subscribed=True):
        item = {
            "code": p["code"],
            "name": p["name"],
            "principal": p["principal"],
            "start_date": p["start_date"],
            "latest": db.latest_rate(p["code"]),
            "has_data": db.rate_count(p["code"]) > 0,
        }
        if item["has_data"]:
            try:
                c = build_calc(p["code"], p["principal"], p["start_date"], with_daily=False)
                item["calc"] = c
                total_principal += c["principal"]
                total_balance += c["balance"]
            except Exception as exc:  # noqa: BLE001
                item["error"] = str(exc)
        products.append(item)

    total_interest = total_balance - total_principal
    weighted = 0.0
    days = 0
    for it in products:
        c = it.get("calc")
        if c and c.get("days"):
            weighted += c.get("annualized", 0) * c["principal"]
            days = max(days, c["days"])
    if total_principal > 0 and days > 0:
        weighted = ((total_balance / total_principal) ** (365 / days) - 1) * 100

    return {
        "products": products,
        "total": {
            "principal": round(total_principal, 2),
            "balance": round(total_balance, 2),
            "interest": round(total_interest, 2),
            "annualized": round(weighted, 4),
            "count": len(products),
        },
        "mail_ready": settings.mail_ready,
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


# ---------------- 邮件 ----------------

def send_daily_mail(test: bool = False) -> dict:
    if not settings.recipients:
        raise ServiceError("未配置收件人 MAIL_TO")
    data = overview()
    if not data["products"]:
        raise ServiceError("还没有订阅任何产品")
    html = mailer.build_daily_html(data, test=test, now=datetime.now())
    subject = f"{settings.MAIL_SUBJECT_PREFIX} {datetime.now():%m月%d日}"
    if test:
        subject = f"[测试] {subject}"
    mailer.send(subject, html)
    db.add_log("info", f"邮件已发送至 {', '.join(settings.recipients)}")
    return {"ok": True, "to": settings.recipients, "products": len(data["products"])}
