"""收益计算：按每万份收益逐日复利（滚动）。

规则（与产品说明书一致，产品本身按日分红转份额）：
    当日收益 = 当日计息本金 / 10000 × 每万份收益
    余额     = 当日计息本金 + 当日收益 + 当日入账本金
    累计收益 = 余额 - 已入账本金合计 + 已扣支出

资金流水三条规则：
1) 本金支出：支出当日先从余额扣减，之后按新余额计息；「收益保留，只减本金」。
2) 新买入 / 追加本金：T 日买入 → **T+1 入账**（计入市值，当日不计息）→ **T+2 起息**。
   入账当日按「入账前余额」计息，当天收益为 0，次日开始参与滚动。
3) 历史导入持仓（settle_date 为空）：本金视为已在账，从 buy_date 起照常计息。

若某日缺少「每万份收益」但有「七日年化」，可由上层用 年化/365 折算兜底数据后再传入。
"""
from datetime import datetime, timedelta


def add_days(date_str, n):
    """YYYY-MM-DD 加减天数。"""
    return (datetime.strptime(date_str, "%Y-%m-%d") + timedelta(days=n)).strftime("%Y-%m-%d")


def compute_snapshots(start_value, buy_date, yields, expenses=None, settle_date=None, injections=None):
    """计算某笔持仓的滚动收益明细（支持本金支出 / 追加本金入账延迟）。

    start_value: 初始本金（元）；历史持仓即录入时的参考市值
    buy_date:    买入/记录日期 YYYY-MM-DD
    yields:      [{nav_date, annual_rate, per_10k}]，按日期升序
    expenses:    [{date, amount}]，该笔分摊到的本金支出，可为 None
    settle_date: 初始本金入账日；None = 本金已在账（历史导入，从 buy_date 起计息）
                 有值时：入账当日不计息，次日起计息（T+1 入账 / T+2 起息）
    injections:  [{date, amount}] 追加本金，date 为入账日（同样当日不计息、次日起计息）

    会计恒等式（任一时点）：
        市值 = 已入账本金合计 - 已扣支出 + 累计收益
        累计收益 = 市值 - 已入账本金合计 + 已扣支出
    未入账的资金不计入市值，单独作为 pending_capital 返回。
    """
    expenses = sorted(expenses or [], key=lambda e: e["date"])
    injections = sorted([i for i in (injections or []) if float(i.get("amount") or 0) > 0],
                        key=lambda x: x["date"])
    start = settle_date or buy_date
    rows = [y for y in yields if y["nav_date"] >= start and y.get("per_10k") is not None]
    rows.sort(key=lambda x: x["nav_date"])

    balance = 0.0 if settle_date else float(start_value)
    principal_in = 0.0 if settle_date else float(start_value)   # 已在账本金合计
    initial_pending = float(start_value) if settle_date else 0.0
    spent = 0.0            # 累计已扣支出
    exp_i = 0
    inj_i = 0
    snapshots = []

    for y in rows:
        nav_date = y["nav_date"]

        # 1) 支出：当日扣减，之后按新余额计息
        day_expense = 0.0
        while exp_i < len(expenses) and expenses[exp_i]["date"] <= nav_date:
            amt = float(expenses[exp_i]["amount"])
            day_expense += amt
            spent += amt
            exp_i += 1
        balance -= day_expense

        # 2) 入账日早于今日的本金：早已在账，今天照常计息
        if initial_pending and settle_date < nav_date:
            balance += initial_pending
            principal_in += initial_pending
            initial_pending = 0.0
        while inj_i < len(injections) and injections[inj_i]["date"] < nav_date:
            amt = float(injections[inj_i]["amount"])
            balance += amt
            principal_in += amt
            inj_i += 1

        # 3) 计息（按当日计息本金）
        per10k = float(y["per_10k"])
        daily = round(balance / 10000.0 * per10k, 4)
        balance = round(balance + daily, 4)

        # 4) 当日入账的本金：计入余额但不产生当日收益，次日起参与滚动
        day_injection = 0.0
        if initial_pending and settle_date == nav_date:
            day_injection += initial_pending
            balance += initial_pending
            principal_in += initial_pending
            initial_pending = 0.0
        while inj_i < len(injections) and injections[inj_i]["date"] <= nav_date:
            amt = float(injections[inj_i]["amount"])
            day_injection += amt
            balance += amt
            principal_in += amt
            inj_i += 1

        cum = round(balance - principal_in + spent, 4)
        snapshots.append({
            "nav_date": nav_date,
            "daily_profit": daily,
            "expense": round(day_expense, 2),
            "injection": round(day_injection, 2),
            "balance": balance,
            "cum_profit": cum,
            "annual_rate": y.get("annual_rate"),
            "per_10k": per10k,
        })

    # 晚于最后一个净值日的支出：作用在最后余额上（等下一净值日体现）
    pending_expense = sum(float(e["amount"]) for e in expenses[exp_i:])
    pending_initial = initial_pending
    pending_injections = sum(float(injections[i]["amount"]) for i in range(inj_i, len(injections)))
    pending_capital = round(pending_initial + pending_injections, 2)

    final_balance = round(balance - pending_expense, 4)
    total_spent = round(spent + pending_expense, 2)

    if snapshots:
        last = snapshots[-1]
        summary = {
            "start_value": round(float(start_value), 2),
            "settle_date": settle_date,
            "principal_in": round(principal_in, 2),
            "pending_capital": pending_capital,
            "total_expense": total_spent,
            "current_value": final_balance,
            "cum_profit": round(last["cum_profit"], 2),
            "profit_rate": round(last["cum_profit"] / (principal_in - total_spent) * 100, 4)
                           if (principal_in - total_spent) > 0 else 0,
            "days": len(snapshots),
            "latest_date": last["nav_date"],
            "latest_annual": last["annual_rate"],
            "latest_per10k": last["per_10k"],
        }
    else:
        summary = {
            "start_value": round(float(start_value), 2),
            "settle_date": settle_date,
            "principal_in": round(principal_in, 2),
            "pending_capital": pending_capital,
            "total_expense": total_spent,
            "current_value": final_balance,
            "cum_profit": 0.0,
            "profit_rate": 0.0,
            "days": 0,
            "latest_date": None,
            "latest_annual": None,
            "latest_per10k": None,
        }
    return snapshots, summary


def portfolio_summary(holdings_summary, historical_profit=0.0):
    """汇总多笔持仓。

    historical_profit: 开始监控前已累计的历史收益基准（来自各行 App）。
      会计恒等式：当前市值 = 真实本金 + 累计收益
      这里的 holdings_summary 各项 principal 是「真实本金」，
      当前市值包含历史收益，因此真实本金 = 市值 - 累计收益。
    """
    total_value = sum(s["current_value"] for s in holdings_summary)
    total_profit = round(sum(s.get("cum_profit", 0) for s in holdings_summary), 2)
    total_principal = round(total_value - total_profit, 2)             # 真实本金（已扣支出）
    rate = round(total_profit / total_principal * 100, 4) if total_principal else 0
    total_expense = round(sum(s.get("total_expense", 0) for s in holdings_summary), 2)
    total_rolling = round(sum(s.get("rolling_profit", 0) for s in holdings_summary), 2)
    total_historical = round(sum(s.get("historical_profit", 0) for s in holdings_summary), 2)
    total_pending = round(sum(s.get("pending_capital", 0) for s in holdings_summary), 2)
    total_added = round(sum(s.get("additions_total", 0) for s in holdings_summary), 2)

    # 整体七日年化：按当前市值加权平均（只统计有年化数据的持仓）
    weighted = 0.0
    weight_base = 0.0
    for s in holdings_summary:
        annual = s.get("latest_annual")
        value = s.get("current_value", 0) or 0
        if annual is not None and value > 0:
            weighted += annual * value
            weight_base += value
    overall_annual = round(weighted / weight_base, 4) if weight_base else None

    return {
        "total_principal": round(total_principal, 2),
        "total_value": round(total_value, 2),
        "total_profit": total_profit,
        "profit_rate": rate,
        "overall_annual": overall_annual,
        "historical_profit": total_historical,
        "rolling_profit": total_rolling,
        "total_expense": total_expense,
        "pending_capital": total_pending,
        "additions_total": total_added,
        "count": len(holdings_summary),
    }
