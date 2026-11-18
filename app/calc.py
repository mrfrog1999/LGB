"""滚动收益计算。

口径说明：建行披露的“七日年化收益率”是过去 7 个自然日的净收益折算成年化的结果。
本程序把它当作「当日的日收益率」使用：日收益 = 当前余额 × 年化 / 365，
并把当日收益滚入余额（复利）。这是估算，不等同于产品实际计息结果。
"""
from datetime import date, datetime, timedelta
from typing import Iterable


def _to_date(s: str) -> date:
    if isinstance(s, date):
        return s
    s = s.strip().replace("/", "-")
    if len(s) == 8 and s.isdigit():
        return datetime.strptime(s, "%Y%m%d").date()
    return datetime.strptime(s[:10], "%Y-%m-%d").date()


DAYS_PER_YEAR = 365


def compute_series(
    principal: float,
    start_date: str,
    rates: Iterable[dict],
    end_date: str | None = None,
) -> dict:
    """按日滚动复利计算收益。

    rates: [{date: 'YYYY-MM-DD', rate: 1.08(单位 %)}]，无需排序
    返回 daily 明细、汇总、以及按最新收益率的外推预估。
    """
    principal = max(0.0, float(principal or 0))
    start = _to_date(start_date)
    end = _to_date(end_date) if end_date else None

    rows = sorted(
        ({"d": _to_date(r["date"]), "rate": float(r["rate"])} for r in rates),
        key=lambda x: x["d"],
    )
    if not rows:
        return {
            "principal": principal, "balance": principal, "interest": 0.0,
            "days": 0, "annualized": 0.0, "daily": [], "latest_rate": None,
            "start_date": start.isoformat(), "end_date": None, "avg_rate": None,
            "forecast": None,
        }

    # 建立“日期 -> 收益率”映射，遇到缺失日用最近一个已知值向后填充
    rate_map: dict[date, float] = {}
    last_known: float | None = None
    for r in rows:
        last_known = r["rate"]
        rate_map[r["d"]] = r["rate"]

    first_data_day = rows[0]["d"]
    last_data_day = end or max(rows[-1]["d"], first_data_day)
    if start < first_data_day:
        start = first_data_day

    balance = principal
    daily: list[dict] = []
    day = start
    cum_interest = 0.0
    rate_sum = 0.0
    filled_days = 0

    while day <= last_data_day:
        if day in rate_map:
            rate = rate_map[day]
            last_known = rate
        else:
            # 无公告数据的日期（周末/节假日常见）：沿用最近一次披露值
            rate = last_known if last_known is not None else rows[0]["rate"]
            filled_days += 1
        interest = balance * rate / 100.0 / DAYS_PER_YEAR
        balance += interest
        cum_interest += interest
        rate_sum += rate
        daily.append({
            "date": day.isoformat(),
            "rate": round(rate, 4),
            "daily_interest": round(interest, 2),
            "balance": round(balance, 2),
            "cum_interest": round(cum_interest, 2),
            "filled": day not in rate_map,
        })
        day += timedelta(days=1)

    days = len(daily)
    avg_rate = rate_sum / days if days else 0.0
    annualized = 0.0
    if principal > 0 and days > 0:
        growth = balance / principal
        annualized = (growth ** (DAYS_PER_YEAR / days) - 1) * 100

    latest = rows[-1]["rate"]
    forecast_30 = balance * (latest / 100.0 / DAYS_PER_YEAR) * 30
    forecast_365 = balance * (latest / 100.0 / DAYS_PER_YEAR) * DAYS_PER_YEAR

    return {
        "principal": round(principal, 2),
        "balance": round(balance, 2),
        "interest": round(cum_interest, 2),
        "days": days,
        "annualized": round(annualized, 4),
        "avg_rate": round(avg_rate, 4),
        "latest_rate": round(latest, 4),
        "start_date": start.isoformat(),
        "end_date": last_data_day.isoformat(),
        "filled_days": filled_days,
        "daily": daily,
        "forecast": {
            "rate": round(latest, 4),
            "month": round(forecast_30, 2),
            "year": round(forecast_365, 2),
        },
    }
