"""业务服务层：串联抓取、存储、计算。供 Web 路由与定时任务共用。"""
import logging
import re
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

import ccb
import psbc
import spdb
import config
import db
import calculator

logger = logging.getLogger("service")


def detect_bank(product_code, product_name=""):
    """识别产品来源银行：ccb=建行, psbc=邮储, spdb=浦发/浦银理财。

    优先查已存 products 表；否则按代码/名称规则判断：
      建行建信理财代码形如 JXJXBGS2023060204 / JXJXBGS2024122728
      邮储中邮理财代码形如 2301TL0010 / 2401LG0001（4位数字+字母开头）
      浦银理财销售份额代码为 10 位纯数字（如 2301269143）
    """
    known = db.get_product_bank(product_code)
    if known:
        return known
    code = (product_code or "").upper()
    name = product_name or ""
    if re.match(r"^\d{4}[A-Z]", code):
        return "psbc"
    if code.startswith("JX"):
        return "ccb"
    if re.fullmatch(r"\d{10}", code) or any(k in name for k in ("浦银理财", "天添利", "现金宝")):
        return "spdb"
    # 按名称兜底
    if "邮" in name or "天乐宝" in name:
        return "psbc"
    return "ccb"


BANK_MODULES = {"ccb": ccb, "psbc": psbc, "spdb": spdb}


def ccb_display_annual(product_code, fallback=None):
    """建行展示年化：按官网近 7 日每万份收益折算，与建行 App 口径一致。

    七日年化(%) = 近7日万份收益之和 ÷ 7 × 365 ÷ 100 = 万份均值 × 3.65

    建行官网公告栏直接公布的"七日年化"常比按万份收益折算值高约 0.01 个百分点，
    而建行 App 展示的是折算值。这里统一展示口径；收益计算始终用每万份收益，不受影响。
    折算失败（缺万份收益数据）时回退到官网公布值 fallback。
    """
    try:
        rows = db.get_yields(product_code)
        vals = [float(r["per_10k"]) for r in rows if r.get("per_10k") is not None][-7:]
        if not vals:
            return fallback
        return round(sum(vals) / len(vals) * 3.65, 4)
    except Exception:
        return fallback

BANK_LABELS = {"ccb": "建行", "psbc": "邮储", "spdb": "浦发"}


def next_settle_date(invest_date):
    """入账日 = 买入日 + 1 天（T 日买入 → T+1 入账 → T+2 起息）。"""
    return calculator.add_days(invest_date, 1)


def search_products(keyword, bank=None):
    """搜索产品，合并建行+邮储+浦发结果，每项带 bank 字段。

    bank: 传入 'ccb' / 'psbc' / 'spdb' 时只搜该银行（前端银行标签页切换用）。
    """
    results = []
    errors = []
    banks = [bank] if bank in BANK_MODULES else list(BANK_MODULES.keys())
    for b in banks:
        mod = BANK_MODULES[b]
        try:
            prods, _, _ = mod.search_products(keyword)
            for p in prods:
                p = dict(p)
                p["bank"] = b
                # 统一字段名，兼容前端
                p.setdefault("latest_date", p.get("nav_date", ""))
                results.append(p)
        except Exception as e:
            logger.warning(f"{b} 搜索失败: {e}")
            errors.append(f"{b}: {e}")
    return results, errors


def refresh_product(product_code, product_name, deep=False, progress=None):
    """刷新某产品净值数据。

    deep=True:  回溯抓取历史公告（首次建仓用），覆盖尽可能多的日期
    deep=False: 只抓最新一期公告（每日更新用）
    返回本次入库的记录条数。
    """
    keyword = product_name or product_code
    bank = detect_bank(product_code, product_name)
    mod = BANK_MODULES.get(bank, ccb)
    try:
        if deep:
            records = mod.fetch_history(keyword, max_months=3, progress=progress)
        else:
            records, meta = mod.fetch_latest_notice(keyword)
    except Exception as e:
        logger.error(f"[{bank}] 抓取产品 {product_name}({product_code}) 失败: {e}")
        raise

    db.upsert_product(product_code, product_name, bank=bank)
    pub = datetime.now().strftime("%Y-%m-%d")
    n = db.save_yields(product_code, records, pub_date=pub)
    logger.info(f"产品 {product_name} 入库 {n} 条净值数据（共解析 {len(records)} 条）")
    return n


def allocate_expenses(holdings=None, withdrawals=None, additions=None):
    """把支出分摊到各笔持仓，返回 {holding_id: [{date, amount}]}。

    分摊口径（按支出记录自带的范围）：
      1) 指定产品（holding_id）：全部从该只产品扣减（邮储 / 浦发口径）
      2) 指定银行（bank）：该银行全部已入账产品「平均平分」（建行口径）
      3) 都没指定（历史记录）：全部持仓按本金占比分摊（旧口径）

    平分/占比都让最后一笔吸收四舍五入尾差，保证分摊之和严格等于支出金额。
    """
    holdings = holdings if holdings is not None else db.list_holdings()
    withdrawals = withdrawals if withdrawals is not None else db.list_withdrawals()
    additions = additions if additions is not None else db.list_additions()
    today = datetime.now().strftime("%Y-%m-%d")
    by_id = {h["id"]: h for h in holdings}
    banks = {h["id"]: detect_bank(h["product_code"], h["product_name"]) for h in holdings}

    def in_account(h):
        """本金已入账（未入账的新买入没有市值可扣）"""
        return not (h.get("settle_date") and h["settle_date"] > today)

    adds_by_holding = {}
    for a in additions:
        if a["settle_date"] <= today:   # 未入账的追加本金不参与分摊
            adds_by_holding[a["holding_id"]] = adds_by_holding.get(a["holding_id"], 0) + float(a["amount"])
    weights = {}
    for h in holdings:
        weights[h["id"]] = (float(h["principal"]) + adds_by_holding.get(h["id"], 0)) if in_account(h) else 0.0
    total_weight = sum(weights.values()) or 1
    active = [h for h in holdings if weights[h["id"]] > 0]

    result = {h["id"]: [] for h in holdings}
    for w in withdrawals:
        amt = float(w["amount"])
        date = w["expense_date"]

        # 1) 单只产品扣减
        hid = w.get("holding_id")
        if hid and hid in by_id:
            # 未入账的持仓不允许扣减，落到该产品所属银行平均分摊
            if in_account(by_id[hid]):
                result[hid].append({"date": date, "amount": round(amt, 2)})
                continue

        # 2) 指定银行 → 该银行产品平均平分
        bank = w.get("bank")
        if bank:
            targets = [h for h in holdings if banks[h["id"]] == bank and in_account(h)]
            if not targets:
                targets = [h for h in holdings if banks[h["id"]] == bank]
            if targets:
                base = round(amt / len(targets), 2)
                allocated = 0.0
                for i, h in enumerate(targets):
                    share = round(amt - allocated, 2) if i == len(targets) - 1 else base
                    allocated += share
                    if share > 0:
                        result[h["id"]].append({"date": date, "amount": share})
                continue

        # 3) 历史记录：按本金占比分摊
        allocated = 0.0
        for i, h in enumerate(active):
            if i == len(active) - 1:
                share = round(amt - allocated, 2)   # 末笔吸收尾差
            else:
                share = round(amt * weights[h["id"]] / total_weight, 2)
                allocated += share
            if share > 0:
                result[h["id"]].append({"date": date, "amount": share})
    return result


def rebuild_holding(holding_id, expenses=None):
    """根据当前净值数据，重算某笔持仓的全部收益快照。"""
    h = db.get_holding(holding_id)
    if not h:
        return None
    yields = db.get_yields(h["product_code"])
    if expenses is None:
        expenses = allocate_expenses().get(holding_id, [])
    additions = db.list_additions(holding_id)
    injections = [{"date": a["settle_date"], "amount": a["amount"]} for a in additions]
    snaps, summary = calculator.compute_snapshots(
        h["principal"], h["buy_date"], yields, expenses,
        settle_date=h.get("settle_date"), injections=injections)
    db.replace_snapshots(holding_id, snaps)
    return summary


def import_historical(product_code, product_name, market_value, historical_profit, note="", buy_date=None):
    """历史导入：录入一笔"很久前已买入"的持仓。

    market_value:      当前市值 M（用户 App 上看到的金额，已含历史收益）
    historical_profit: 历史收益 H（市值中已包含的累计收益）
    buy_date:          起算日，默认今天。本金按 M 记录、视为已在账（settle_date=None），
                       从起算日起按每日净值滚动产生"新增收益"。
                       单笔历史收益 H 存到该持仓，汇总时累计收益 = H + 新增收益。

    会计恒等式（任一时点）：
        当前市值 = M + 新增收益
        本金     = M - H
        累计收益 = H + 新增收益
        本金 + 累计收益 = (M-H) + (H+新增) = M + 新增 = 当前市值  ✓
    """
    from datetime import datetime as _dt
    buy_date = buy_date or _dt.now().strftime("%Y-%m-%d")
    # 起算日按今天：只抓最新一期净值用于展示年化，不回溯历史（历史收益已由 H 直接给定）
    try:
        refresh_product(product_code, product_name, deep=False)
    except Exception as e:
        # 抓取失败不阻断导入：市值与历史收益由用户给定，后续每日更新会补齐净值
        logger.warning(f"历史导入 {product_name} 抓最新净值失败（不影响导入）: {e}")
    hid = db.add_holding(product_code, product_name, float(market_value), buy_date,
                         note, settle_date=None, historical_profit=float(historical_profit))
    if hid is None:
        return None
    rebuild_holding(hid)
    return hid


def rebuild_all():
    """支出 / 追加本金变动后重算所有持仓。返回按 id 索引的 summary。"""
    holdings = db.list_holdings()
    additions = db.list_additions()
    alloc = allocate_expenses(holdings, additions=additions)
    out = {}
    for h in holdings:
        out[h["id"]] = rebuild_holding(h["id"], alloc.get(h["id"], []))
    return out


def summarize_holding(holding, historical_profit=0.0, total_expense=0.0, additions=None):
    """获取某笔持仓的最新汇总（读快照 + 入账状态，不重算）。

    historical_profit: 按起算本金占比分摊到该笔的历史收益基准。
    total_expense:     按本金占比分摊到该笔的本金支出。
    additions:         该笔的追加本金记录（capital_additions）。

    入账规则（T 日买入 → T+1 入账 → T+2 起息）：
      · 未到入账日：不计入市值 / 本金，作为 pending_capital 展示「待入账」
      · 入账当日：计入市值，当日不计息（status=settled）
      · 次日起：参与滚动计息（status=earning）

    展示口径（收益保留，只减本金）：
      真实本金 = 初始本金 + 已入账追加本金 - 分摊历史收益 - 分摊支出
      累计收益 = 分摊历史收益 + 监控后滚动收益
      当前市值 = 已入账本金合计 - 支出 + 滚动收益
    """
    today = datetime.now().strftime("%Y-%m-%d")
    adds = additions or []
    start_value = float(holding["principal"])
    settle = holding.get("settle_date")
    initial_settled = (not settle) or settle <= today
    settled_adds = [a for a in adds if a["settle_date"] <= today]
    pending_adds = [a for a in adds if a["settle_date"] > today]
    settled_adds_total = round(sum(float(a["amount"]) for a in settled_adds), 2)
    pending_capital = round((0.0 if initial_settled else start_value)
                            + sum(float(a["amount"]) for a in pending_adds), 2)

    snaps = [s for s in db.get_snapshots(holding["id"]) if s["nav_date"] <= today]
    last_date = snaps[-1]["nav_date"] if snaps else None
    # 已入账但晚于末次净值快照的追加本金：净值数据没跟上时也要计入市值
    late_adds = round(sum(float(a["amount"]) for a in settled_adds
                          if last_date is None or a["settle_date"] > last_date), 2)

    rolling = snaps[-1]["cum_profit"] if snaps else 0.0
    expense_in = round(sum((s.get("expense") or 0) for s in snaps), 2)
    pending_expense = round(total_expense - expense_in, 2)
    historical_profit = round(historical_profit, 2)
    cum = round(historical_profit + rolling, 2)
    base_principal = start_value if initial_settled else 0.0
    real_principal = round(base_principal + settled_adds_total - historical_profit - total_expense, 2)

    if not initial_settled:
        current_value = 0.0
    elif snaps:
        current_value = round(snaps[-1]["balance"] + late_adds - max(pending_expense, 0), 2)
    else:
        current_value = round(base_principal + settled_adds_total - total_expense, 2)

    # 状态：待入账 / 已入账待起息 / 计息中
    if not initial_settled:
        status, status_label = "pending", "待入账"
    elif settle and settle == today:
        status, status_label = "settled", "已入账 · 待起息"
    else:
        status, status_label = "earning", "计息中"

    first_profit_date = calculator.add_days(settle, 1) if settle else holding["buy_date"]

    latest_annual = snaps[-1]["annual_rate"] if snaps else None
    latest_per10k = snaps[-1]["per_10k"] if snaps else None
    latest_date = snaps[-1]["nav_date"] if snaps else None
    if latest_annual is None or latest_date is None:
        ly = db.latest_yield(holding["product_code"])
        if ly:
            latest_annual = ly["annual_rate"] if latest_annual is None else latest_annual
            latest_per10k = ly["per_10k"] if latest_per10k is None else latest_per10k
            latest_date = latest_date or ly["nav_date"]
    # 建行：展示口径对齐 App —— 用近7日万份收益折算年化（不影响收益计算，收益始终按万份收益逐日计息）
    if detect_bank(holding["product_code"], holding.get("product_name", "")) == "ccb":
        latest_annual = ccb_display_annual(holding["product_code"], latest_annual)

    # 追加本金明细（含各自入账状态，用于列表/明细展示）
    add_rows = []
    for a in adds:
        add_rows.append({
            "id": a["id"],
            "amount": round(float(a["amount"]), 2),
            "invest_date": a["invest_date"],
            "settle_date": a["settle_date"],
            "note": a.get("note") or "",
            "status": "pending" if a["settle_date"] > today else (
                "settled" if a["settle_date"] == today else "earning"),
        })

    return {
        "principal": real_principal,
        "start_value": round(start_value, 2),
        "initial_settled": initial_settled,
        "settle_date": settle,
        "first_profit_date": first_profit_date,
        "additions_total": settled_adds_total,
        "pending_capital": pending_capital,
        "additions": add_rows,
        "historical_profit": historical_profit,
        "total_expense": round(total_expense, 2),
        "rolling_profit": round(rolling, 4),
        "current_value": current_value,
        "cum_profit": cum,
        "profit_rate": round(cum / real_principal * 100, 4) if real_principal else 0,
        "status": status,
        "status_label": status_label,
        "days": len(snaps),
        "latest_date": latest_date,
        "latest_annual": latest_annual,
        "latest_per10k": latest_per10k,
    }


def last_balance(snaps):
    return snaps[-1]["balance"]


def dashboard_data():
    """构造首页需要的所有数据。"""
    s = db.get_settings()
    hist_ccb = float(s.get("historical_profit") or 0)        # 建行 App 历史收益基准
    hist_psbc = float(s.get("psbc_historical_profit") or 0)  # 邮储 App 历史收益基准
    hist_spdb = float(s.get("spdb_historical_profit") or 0)  # 浦发 App 历史收益基准
    holdings = db.list_holdings()
    withdrawals = db.list_withdrawals()
    additions = db.list_additions()
    today = datetime.now().strftime("%Y-%m-%d")
    adds_by_holding = {}
    for a in additions:
        adds_by_holding.setdefault(a["holding_id"], []).append(a)

    def has_own_hist(h):
        """该笔是否自带历史收益基准（历史导入产品，单笔指定，不走全局分摊）。"""
        return h.get("historical_profit") is not None

    def hist_weight(h):
        """历史收益分摊权重：只算已在账的本金（未入账的新买入不计权重）。

        自带历史收益的持仓不参与全局基准分摊，避免与其单笔历史收益重复计算。
        """
        if has_own_hist(h):
            return 0.0
        if h.get("settle_date") and h["settle_date"] > today:
            return 0.0
        return float(h["principal"]) + sum(float(a["amount"]) for a in adds_by_holding.get(h["id"], [])
                                           if a["settle_date"] <= today)

    # 历史收益按「银行分组」分别分摊：建行基准只在建行持仓内分，邮储基准只在邮储持仓内分，
    # 浦发基准只在浦发持仓内分，避免跨行按市值错误分配。支出则是全局整体，按全部起算本金占比分摊。
    # 若某行已有持仓自带历史收益（历史导入），该行待分摊基准先扣掉这些单笔值，避免重复计入。
    hist_by_bank = {"ccb": hist_ccb, "psbc": hist_psbc, "spdb": hist_spdb}
    bank_total = {}
    for h in holdings:
        b = detect_bank(h["product_code"], h["product_name"])
        bank_total[b] = bank_total.get(b, 0) + hist_weight(h)
    for b in hist_by_bank:
        own_sum = sum(float(h["historical_profit"]) for h in holdings
                      if has_own_hist(h) and detect_bank(h["product_code"], h["product_name"]) == b)
        hist_by_bank[b] = max(0.0, hist_by_bank[b] - own_sum)

    total_withdraw = sum(w["amount"] for w in withdrawals)
    expense_alloc = allocate_expenses(holdings, withdrawals, additions)

    items = []
    summaries = []
    for h in holdings:
        bank = detect_bank(h["product_code"], h["product_name"])
        adds = adds_by_holding.get(h["id"], [])
        if has_own_hist(h):
            # 历史导入产品：用该笔自带的历史收益，不参与全局分摊
            share = float(h["historical_profit"])
        else:
            w = hist_weight(h)
            grp_total = bank_total.get(bank, 0) or 1
            share = hist_by_bank.get(bank, 0) * w / grp_total if w > 0 else 0.0
        exp_share = round(sum(e["amount"] for e in expense_alloc.get(h["id"], [])), 2)
        summary = summarize_holding(h, historical_profit=share, total_expense=exp_share, additions=adds)
        summary["bank"] = bank
        summaries.append(summary)
        items.append({**h, **summary})

    portfolio = calculator.portfolio_summary(summaries)

    # 分银行小计：市值 / 收益 / 加权年化，便于和各行 App 对照
    bank_summary = {}
    bank_labels = {"ccb": "建行", "psbc": "邮储", "spdb": "浦发"}
    for bank in ("ccb", "psbc", "spdb"):
        subset = [x for x in items if x["bank"] == bank]
        if not subset:
            continue
        value = round(sum(x["current_value"] for x in subset), 2)
        profit = round(sum(x["cum_profit"] for x in subset), 2)
        weighted = 0.0
        base = 0.0
        annuals = []  # 持有产品的最新官网年化，用于简单平均
        dates = []    # 持有产品的最新净值日期，用于卡片展示
        for x in subset:
            ann = x.get("latest_annual")
            if ann is not None:
                try:
                    annuals.append(float(ann))
                except (TypeError, ValueError):
                    pass
            if ann is not None and x["current_value"] > 0:
                weighted += float(ann) * x["current_value"]
                base += x["current_value"]
            d = x.get("latest_date")
            if d:
                dates.append(d)
        bank_summary[bank] = {
            "label": bank_labels[bank],
            "count": len(subset),
            "value": value,
            "principal": round(value - profit, 2),
            "profit": profit,
            "annual": round(weighted / base, 4) if base else None,       # 市值加权年化
            "avg_annual": round(sum(annuals) / len(annuals), 4) if annuals else None,  # 持有产品年化简单平均
            "avg_date": max(dates) if dates else None,                    # 该行年化对应的最新净值日期
            "pending": round(sum(x.get("pending_capital", 0) for x in subset), 2),
        }
    portfolio["bank_summary"] = bank_summary

    # historical_profit 用各笔实际分摊之和（录入邮储前只含建行，录入后两组都含）
    portfolio["historical_ccb"] = round(hist_ccb, 2)
    portfolio["historical_psbc"] = round(hist_psbc, 2)
    portfolio["historical_spdb"] = round(hist_spdb, 2)
    portfolio["rolling_profit"] = round(sum(x.get("rolling_profit", 0) for x in summaries), 2)
    portfolio["total_expense"] = round(total_withdraw, 2)
    portfolio["withdrawal_count"] = len(withdrawals)

    # 支出记录补充分摊范围文案：建行按该行产品平均分摊，邮储/浦发按指定单只产品扣减
    name_by_id = {h["id"]: h["product_name"] for h in holdings}
    bank_count = {b: sum(1 for h in holdings if detect_bank(h["product_code"], h["product_name"]) == b)
                  for b in ("ccb", "psbc", "spdb")}
    enriched = []
    for w in withdrawals:
        w = dict(w)
        b = w.get("bank")
        w["bank_label"] = bank_labels.get(b, "")
        if w.get("holding_id"):
            w["scope_label"] = f"{bank_labels.get(b, '')} · {name_by_id.get(w['holding_id'], '已删除的产品')}"
        elif b:
            w["scope_label"] = f"{bank_labels.get(b, '')} · {bank_count.get(b, 0)} 只产品平均分摊"
        else:
            w["scope_label"] = "全部持仓按本金占比分摊"
        enriched.append(w)
    withdrawals = enriched
    portfolio["app_ref_date"] = s.get("app_ref_date", "")
    portfolio["app_yesterday_profit"] = s.get("app_yesterday_profit", "")
    portfolio["app_overall_annual"] = s.get("app_overall_annual", "")
    for prefix, bank in (("", "ccb"), ("psbc_", "psbc"), ("spdb_", "spdb")):
        portfolio[f"{prefix}app_yesterday_profit"] = s.get(f"{prefix}app_yesterday_profit", "")
        portfolio[f"{prefix}app_overall_annual"] = s.get(f"{prefix}app_overall_annual", "")
    return {"holdings": items, "portfolio": portfolio, "withdrawals": withdrawals,
            "additions": additions}


def daily_update():
    """每日定时任务：刷新所有持仓产品 → 重算快照。

    返回 (成功数, 失败列表, 详情)
    """
    holdings = db.list_holdings(active_only=True)
    # 按产品去重，避免同一产品多笔持仓重复抓取
    products = {}
    for h in holdings:
        products[h["product_code"]] = h["product_name"]

    ok, fails, details = 0, [], []
    for code, name in products.items():
        try:
            refresh_product(code, name, deep=False)
            ok += 1
            details.append(f"✓ {name}：净值数据已更新")
        except Exception as e:
            fails.append(f"{name}: {e}")
            details.append(f"✗ {name}：{e}")

    # 重算所有持仓
    for h in holdings:
        rebuild_holding(h["id"])

    return ok, fails, details


def low_annual_products(data=None, threshold=None):
    """筛出七日年化低于预警阈值的持仓（不含待入账、不含无年化数据的产品）。

    threshold 单位为 %，缺省取 settings 中的 alert_annual，再退到 config 默认值。
    """
    import config
    data = data or dashboard_data()
    if threshold is None:
        s = db.get_settings()
        try:
            threshold = float(s.get("alert_annual") or config.DEFAULT_ALERT_ANNUAL)
        except (TypeError, ValueError):
            threshold = config.DEFAULT_ALERT_ANNUAL
    threshold = float(threshold)

    low = []
    for h in data.get("holdings", []):
        if h.get("status") == "pending":
            continue
        annual = h.get("latest_annual")
        if annual is None:
            continue
        try:
            annual = float(annual)
        except (TypeError, ValueError):
            continue
        if annual < threshold:
            low.append({
                "id": h.get("id"),
                "product_code": h.get("product_code"),
                "product_name": h.get("product_name"),
                "bank": h.get("bank"),
                "bank_label": BANK_LABELS.get(h.get("bank"), "建行"),
                "latest_annual": annual,
                "gap": round(threshold - annual, 4),
                "principal": h.get("principal"),
                "current_value": h.get("current_value"),
                "cum_profit": h.get("cum_profit"),
            })
    low.sort(key=lambda x: x["latest_annual"])
    return low, threshold

# ---------------- 网络连通性诊断 ----------------
DIAG_TARGETS = [
    ("建行", "https://ccb.com/chn/home/finance/index/cpgg/cpcxqgg/index.shtml"),
    ("邮储", "https://s.psbc.com/portal/PsbcService/finquick/"),
    ("浦发", "https://www.spdb-wm.com/financialProducts/"),
]


DIAG_TIMEOUT = 12       # 单次探测超时（秒）
DIAG_RETRIES = 3         # 单家站点最多探测次数（失败才重试）
DIAG_RETRY_GAP = 0.6     # 重试间隔（秒）


def _probe_once(name, url):
    """单次探测一家站点。"""
    item = {"name": name, "url": url, "ok": False, "http_code": "", "seconds": 0,
            "ip": "", "error": "", "attempts": 0, "recovered": False}
    cmd = ["curl", "-sSL", "--max-time", str(DIAG_TIMEOUT), "-A", config.UA,
           "-o", "/dev/null", "-e", url,
           "-w", "%{http_code}|%{time_total}|%{remote_ip}", url]
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=DIAG_TIMEOUT + 5)
        raw = r.stdout.decode("utf-8", errors="ignore").strip()
        parts = raw.split("|")
        if len(parts) >= 3:
            item["http_code"], t, item["ip"] = parts[0], parts[1], parts[2]
            try:
                item["seconds"] = round(float(t), 3)
            except ValueError:
                pass
        item["error"] = r.stderr.decode("utf-8", errors="ignore").strip()[:200]
        # 银行站点偶发返回非 200，但 2xx/3xx 且能连通也算站点可达
        code = item["http_code"]
        item["ok"] = code.startswith("2") or code.startswith("3")
        if not item["ok"] and not item["error"] and code in ("", "000"):
            item["error"] = "连接超时或无响应"
    except subprocess.TimeoutExpired:
        item["error"] = f"探测超时（>{DIAG_TIMEOUT}s）"
    except Exception as e:
        item["error"] = str(e)[:200]
    return item


def _diag_one(name, url):
    """探测单家站点：失败自动重试，成功即返回。"""
    best = None
    for attempt in range(1, DIAG_RETRIES + 1):
        cur = _probe_once(name, url)
        cur["attempts"] = attempt
        # 成功优先；都失败时保留最后一次（错误信息与耗时最新）
        if best is None or cur["ok"] or not best["ok"]:
            best = cur
        if cur["ok"]:
            best["recovered"] = attempt > 1   # 重试后才通，标记为曾抖动
            return best
        if attempt < DIAG_RETRIES:
            time.sleep(DIAG_RETRY_GAP)
    return best


def network_diag():
    """并发探测三家银行站点，失败自动重试，降低偶发抖动误报。

    返回每家的 HTTP 状态、耗时、出口 IP、探测次数与是否曾抖动。
    """
    with ThreadPoolExecutor(max_workers=len(DIAG_TARGETS)) as pool:
        futures = [pool.submit(_diag_one, name, url) for name, url in DIAG_TARGETS]
        return [f.result() for f in futures]
