"""龙钱包收益监控 - Flask Web 服务 + 后台定时任务。"""
import logging
from datetime import datetime

from functools import wraps

from flask import Flask, render_template, request, jsonify, redirect, url_for, session

import config
import db
import service
import mailer
from apscheduler.schedulers.background import BackgroundScheduler

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")
logger = logging.getLogger("app")

app = Flask(__name__)
app.secret_key = config.SECRET_KEY


# ---------------- 登录认证与权限 ----------------
# 未登录可访问的路径（登录页、登录接口、图标等）
PUBLIC_PATHS = {"/login", "/api/auth/login", "/favicon.ico"}


def current_user():
    """从 session 读取当前登录用户（公开信息，不含密码哈希）。"""
    uid = session.get("uid")
    if not uid:
        return None
    return db.get_user(uid)


def require_login_page():
    """页面路由用：未登录跳转到登录页。"""
    if current_user() is None:
        return redirect(url_for("login_page", next=request.path))
    return None


@app.before_request
def auth_guard():
    """全局访问守卫：未登录拦截 + 只读成员禁止修改。"""
    path = request.path
    if path in PUBLIC_PATHS or path.startswith("/static/"):
        return None
    user = current_user()
    if user is None:
        # 未登录：API 返回 401，页面跳转登录页
        if path.startswith("/api/"):
            return jsonify({"ok": False, "error": "未登录或登录已过期", "need_login": True}), 401
        return redirect(url_for("login_page", next=path))
    # 已登录用户可自行操作的接口（改自己密码、退出）——不受只读限制
    if path in ("/api/auth/password", "/api/auth/logout"):
        return None
    # 其余只写接口（非 GET/HEAD/OPTIONS）仅管理员可用
    if path.startswith("/api/") and request.method not in ("GET", "HEAD", "OPTIONS"):
        if user["role"] != "admin":
            return jsonify({"ok": False, "error": "当前账号为只读成员，没有修改权限"}), 403
    # 导出备份含邮箱授权码/密码哈希等敏感信息，仅管理员可导出
    if path == "/api/data/export" and user["role"] != "admin":
        return jsonify({"ok": False, "error": "只有管理员可以导出备份"}), 403
    return None


@app.context_processor
def inject_user():
    """向所有模板注入当前登录用户信息，供导航与按钮显隐使用。"""
    u = current_user()
    return {
        "current_user": u,
        "is_admin": bool(u and u["role"] == "admin"),
        "sharecode_fallback": config.SHARECODE_FALLBACK,
        "app_version": config.APP_VERSION,
    }


# ---------------- 后台定时任务 ----------------
scheduler = BackgroundScheduler(timezone="Asia/Shanghai")


def scheduled_job():
    """每日定时：更新数据 + 发送邮件。"""
    logger.info("=== 执行每日定时任务 ===")
    try:
        ok, fails, details = service.daily_update()
        settings = db.get_settings()
        if settings.get("push_enabled") == "1":
            data = service.dashboard_data()
            success, msg = mailer.send_report(data, details)
            logger.info(f"邮件推送: {msg}")
            if success:
                db.set_setting("last_push_date", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        else:
            logger.info("邮件推送已关闭，仅更新数据")

        # 年化预警：抓完数据后检查是否有产品跌破阈值
        if db.get_settings().get("alert_enabled", "1") == "1":
            low, threshold = service.low_annual_products(service.dashboard_data())
            if low:
                msg2 = mailer.send_alert(service.dashboard_data(), low, threshold)[1]
                logger.info(f"年化预警: {len(low)} 只产品低于 {threshold:.2f}% -> {msg2}")
            else:
                logger.info("年化预警: 无产品低于阈值")
    except Exception as e:
        logger.error(f"定时任务异常: {e}")


def afternoon_fetch_job():
    """下午定时：仅抓取更新数据 + 重算快照，不发邮件。"""
    logger.info("=== 执行下午补充抓取任务 ===")
    try:
        ok, fails, details = service.daily_update()
        logger.info(f"下午抓取完成: 成功 {ok} 只" + (f"，失败 {len(fails)} 只: {fails}" if fails else ""))
    except Exception as e:
        logger.error(f"下午抓取任务异常: {e}")


def setup_scheduler():
    s = db.get_settings()
    hour = int(s.get("push_hour") or config.DEFAULT_PUSH_HOUR)
    minute = int(s.get("push_minute") or config.DEFAULT_PUSH_MINUTE)
    scheduler.remove_all_jobs()
    # 主任务：每天指定时间更新数据 + 邮件推送
    scheduler.add_job(scheduled_job, "cron", hour=hour, minute=minute, id="daily_push", replace_existing=True)
    # 补充任务：每天下午抓取更新数据（不发邮件）
    scheduler.add_job(
        afternoon_fetch_job, "cron",
        hour=config.SECOND_FETCH_HOUR, minute=config.SECOND_FETCH_MINUTE,
        id="afternoon_fetch", replace_existing=True,
    )
    logger.info(
        f"定时任务已设置: 主任务每天 {hour:02d}:{minute:02d}，"
        f"下午抓取每天 {config.SECOND_FETCH_HOUR:02d}:{config.SECOND_FETCH_MINUTE:02d}"
    )


# ---------------- 页面路由 ----------------
@app.route("/login")
def login_page():
    # 已登录用户直接回首页
    if current_user() is not None:
        return redirect(url_for("index"))
    return render_template("login.html")


@app.route("/logout")
def logout_page():
    session.clear()
    return redirect(url_for("login_page"))


@app.route("/")
def index():
    data = service.dashboard_data()
    s = db.get_settings()
    product_count = len(db.list_products())
    low, alert_threshold = service.low_annual_products(data)
    return render_template("index.html", data=data, settings=s,
                           product_count=product_count,
                           last_push=s.get("last_push_date", ""),
                           data_sources=data_sources(),
                           alert_enabled=s.get("alert_enabled", "1") == "1",
                           alert_annual=alert_threshold,
                           low_products=low)


def data_sources():
    """页面底部展示的数据来源（链接统一取自 config，避免模板里写死）"""
    return [
        {
            "bank": "ccb", "label": "中国建设银行 · 产品存续期公告",
            "desc": "净值公告 PDF，解析七日年化与每万份收益",
            "url": config.CCB_LIST_PAGE,
        },
        {
            "bank": "psbc", "label": "中国邮政储蓄银行 · 理财超市",
            "desc": "业绩公告 / 理财净值查询（finquick 接口）",
            "url": config.PSBC_SITE_URL,
        },
        {
            "bank": "spdb", "label": "浦银理财 · 产品信息查询",
            "desc": "销售份额净值与七日年化（官网 JSON 接口）",
            "url": config.SPDB_SITE_URL,
        },
    ]


@app.route("/settings")
def settings_page():
    s = db.get_settings()
    return render_template("settings.html", settings=s)


# ---------------- API：登录认证 ----------------
@app.route("/api/auth/login", methods=["POST"])
def api_login():
    body = request.get_json(silent=True) or {}
    username = (body.get("username") or "").strip()
    password = body.get("password") or ""
    user = db.verify_user(username, password)
    if not user:
        logger.warning(f"登录失败：用户名或密码错误（{username}）")
        return jsonify({"ok": False, "error": "用户名或密码不正确"})
    session.clear()
    session["uid"] = user["id"]
    db.touch_login(user["id"])
    logger.info(f"登录成功：{username}（{user['role']}）")
    return jsonify({"ok": True, "user": user})


@app.route("/api/auth/logout", methods=["POST"])
def api_logout():
    session.clear()
    return jsonify({"ok": True})


@app.route("/api/auth/me")
def api_me():
    u = current_user()
    if not u:
        return jsonify({"ok": False, "need_login": True}), 401
    return jsonify({"ok": True, "user": u})


# ---------------- API：账号与权限管理（仅管理员） ----------------
@app.route("/api/users")
def api_users():
    return jsonify({"ok": True, "users": db.list_users(), "admin_count": db.admin_count()})


@app.route("/api/users", methods=["POST"])
def api_create_user():
    body = request.get_json(silent=True) or {}
    uid, err = db.create_user(
        body.get("username"), body.get("password"),
        body.get("role") or "member", body.get("display_name"),
    )
    if err:
        return jsonify({"ok": False, "error": err})
    logger.info(f"新增账号：{body.get('username')}（{body.get('role')}）")
    return jsonify({"ok": True, "id": uid, "message": "账号已创建"})


@app.route("/api/users/<int:uid>", methods=["POST"])
def api_update_user(uid):
    body = request.get_json(silent=True) or {}
    # 传空字符串表示不改密码；username/role/display_name 只在字段存在时更新
    kwargs = {}
    if "username" in body:
        kwargs["username"] = body["username"]
    if "role" in body:
        kwargs["role"] = body["role"]
    if "display_name" in body:
        kwargs["display_name"] = body["display_name"]
    if body.get("password"):
        kwargs["password"] = body["password"]
    ok, err = db.update_user(uid, **kwargs)
    if not ok:
        return jsonify({"ok": False, "error": err})
    logger.info(f"编辑账号 {uid}：{list(kwargs.keys())}")
    return jsonify({"ok": True, "message": "账号已更新"})


@app.route("/api/users/<int:uid>", methods=["DELETE"])
def api_delete_user(uid):
    u = current_user()
    if u and u["id"] == uid:
        return jsonify({"ok": False, "error": "不能删除当前登录的账号"})
    ok, err = db.delete_user(uid)
    if not ok:
        return jsonify({"ok": False, "error": err})
    logger.info(f"删除账号 {uid}")
    return jsonify({"ok": True, "message": "账号已删除"})


@app.route("/api/auth/password", methods=["POST"])
def api_change_password():
    """当前用户修改自己的密码（任何角色可用）。"""
    body = request.get_json(silent=True) or {}
    old = body.get("old_password") or ""
    new = body.get("new_password") or ""
    u = current_user()
    if not u:
        return jsonify({"ok": False, "error": "未登录"}), 401
    full = db.get_user_by_username(u["username"])
    from werkzeug.security import check_password_hash
    if not check_password_hash(full["password_hash"], old):
        return jsonify({"ok": False, "error": "当前密码不正确"})
    if len(new) < 6:
        return jsonify({"ok": False, "error": "新密码至少 6 位"})
    ok, err = db.update_user(u["id"], password=new)
    if not ok:
        return jsonify({"ok": False, "error": err})
    return jsonify({"ok": True, "message": "密码已修改"})


# ---------------- API：产品搜索 ----------------
@app.route("/api/search")
def api_search():
    keyword = request.args.get("q", "").strip()
    bank = (request.args.get("bank") or "").strip() or None
    if not keyword:
        return jsonify({"ok": False, "error": "请输入产品名称关键词", "products": []})
    try:
        products, errors = service.search_products(keyword, bank=bank)
        return jsonify({"ok": True, "products": products, "total": len(products),
                        "errors": errors})
    except Exception as e:
        logger.error(f"搜索失败: {e}")
        return jsonify({"ok": False, "error": str(e), "products": []})


# ---------------- API：持仓管理 ----------------
@app.route("/api/holdings", methods=["GET"])
def api_holdings():
    items = db.list_holdings()
    for h in items:
        h["bank"] = service.detect_bank(h["product_code"], h["product_name"])
    return jsonify({"ok": True, "holdings": items})


@app.route("/api/holdings/import", methods=["POST"])
def api_import_holding():
    """历史导入：录入一笔很久前已买入、只知当前市值与历史收益的持仓。

    本金 = 当前市值 M - 历史收益 H；从今天（起算日）按净值滚动，
    累计收益 = H + 新增收益；不回溯抓取历史净值。
    """
    body = request.get_json(silent=True) or {}
    code = (body.get("product_code") or "").strip()
    name = (body.get("product_name") or "").strip()
    try:
        market_value = float(body.get("market_value"))
        historical_profit = float(body.get("historical_profit") or 0)
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "市值或历史收益格式不正确"})
    note = (body.get("note") or "").strip()
    if not code or not name:
        return jsonify({"ok": False, "error": "请先搜索并选择一个产品"})
    if market_value <= 0:
        return jsonify({"ok": False, "error": "当前市值必须大于 0"})
    if historical_profit < 0:
        return jsonify({"ok": False, "error": "历史收益不能为负"})
    if historical_profit >= market_value:
        return jsonify({"ok": False, "error": "历史收益不能大于或等于当前市值（本金须大于 0）"})

    buy_date = (body.get("buy_date") or "").strip() or datetime.now().strftime("%Y-%m-%d")
    try:
        datetime.strptime(buy_date, "%Y-%m-%d")
    except ValueError:
        return jsonify({"ok": False, "error": "起算日期格式不正确"})

    try:
        hid = service.import_historical(code, name, market_value, historical_profit, note, buy_date)
    except Exception as e:
        logger.error(f"历史导入失败: {e}")
        return jsonify({"ok": False, "error": f"导入失败：{e}"})
    if hid is None:
        return jsonify({"ok": False, "error": "该持仓已存在（相同产品/起算日/市值）"})

    principal = round(market_value - historical_profit, 2)
    logger.info(f"历史导入 {name}({code}) 市值 {market_value} 历史收益 {historical_profit} "
                f"本金 {principal} 起算 {buy_date}")
    return jsonify({"ok": True, "message": "历史持仓已导入", "id": hid,
                    "principal": principal, "buy_date": buy_date})


@app.route("/api/holdings", methods=["POST"])
def api_add_holding():
    body = request.get_json(silent=True) or {}
    code = (body.get("product_code") or "").strip()
    name = (body.get("product_name") or "").strip()
    try:
        principal = float(body.get("principal"))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "本金格式不正确"})
    buy_date = (body.get("buy_date") or "").strip()
    note = (body.get("note") or "").strip()
    if not code or not name or not buy_date or principal <= 0:
        return jsonify({"ok": False, "error": "请填写完整的产品、本金和买入日期"})
    try:
        datetime.strptime(buy_date, "%Y-%m-%d")
    except ValueError:
        return jsonify({"ok": False, "error": "买入日期格式不正确"})

    # 入账规则：T 日买入 → T+1 入账 → T+2 起息（前端也可显式传 settle_date 覆盖）
    settle_date = (body.get("settle_date") or "").strip() or service.next_settle_date(buy_date)

    # 深刷历史数据（回溯3个月）以支持从买入日滚动计算
    try:
        service.refresh_product(code, name, deep=True)
    except Exception as e:
        return jsonify({"ok": False, "error": f"抓取产品历史数据失败：{e}"})

    hid = db.add_holding(code, name, principal, buy_date, note, settle_date=settle_date)
    if hid is None:
        return jsonify({"ok": False, "error": "该持仓已存在（相同产品/日期/金额）"})
    service.rebuild_all()
    logger.info(f"新增持仓 {name}({code}) 本金 {principal} 买于 {buy_date}，入账 {settle_date}")
    return jsonify({"ok": True, "message": "持仓已添加", "id": hid,
                    "settle_date": settle_date})


@app.route("/api/holdings/<int:hid>", methods=["POST"])
def api_update_holding(hid):
    """编辑持仓：产品名称/本金/买入日/入账日/备注。

    产品代码不可改（决定净值抓取来源）；改本金或日期会影响支出分摊与计息，
    因此用 rebuild_all 重算全部持仓，保证汇总口径一致。
    """
    h = db.get_holding(hid)
    if not h:
        return jsonify({"ok": False, "error": "持仓不存在"})
    body = request.get_json(silent=True) or {}
    fields = {}

    if "principal" in body:
        try:
            principal = float(body["principal"])
        except (TypeError, ValueError):
            return jsonify({"ok": False, "error": "本金格式不正确"})
        if principal <= 0:
            return jsonify({"ok": False, "error": "本金必须大于 0"})
        fields["principal"] = principal

    if "product_name" in body:
        name = (body["product_name"] or "").strip()
        if not name:
            return jsonify({"ok": False, "error": "产品名称不能为空"})
        fields["product_name"] = name

    for k, label in (("buy_date", "买入日期"), ("settle_date", "入账日期")):
        if k in body:
            v = (body[k] or "").strip()
            if v:
                try:
                    datetime.strptime(v, "%Y-%m-%d")
                except ValueError:
                    return jsonify({"ok": False, "error": f"{label}格式不正确"})
            # 空串表示「已在账」（历史导入持仓）
            fields[k] = v or None

    if "note" in body:
        fields["note"] = (body["note"] or "").strip()
    if "historical_profit" in body:
        hp_raw = body["historical_profit"]
        if hp_raw is None or (isinstance(hp_raw, str) and not hp_raw.strip()):
            fields["historical_profit"] = None   # 改回「按银行全局分摊」
        else:
            try:
                hp = float(hp_raw)
            except (TypeError, ValueError):
                return jsonify({"ok": False, "error": "历史收益格式不正确"})
            if hp < 0:
                return jsonify({"ok": False, "error": "历史收益不能为负"})
            fields["historical_profit"] = hp
    if "active" in body:
        fields["active"] = 1 if body["active"] else 0

    if not fields:
        return jsonify({"ok": False, "error": "没有需要更新的内容"})

    if not db.update_holding(hid, **fields):
        return jsonify({"ok": False, "error": "已存在相同产品/买入日/本金的持仓，请调整后再保存"})

    service.rebuild_all()
    logger.info(f"编辑持仓 {hid}（{h['product_name']}）: {fields}")
    return jsonify({"ok": True, "message": "已保存并重算"})


@app.route("/api/holdings/<int:hid>", methods=["DELETE"])
def api_delete_holding(hid):
    db.delete_holding(hid)
    return jsonify({"ok": True, "message": "已删除"})


@app.route("/api/holdings/<int:hid>/snapshots")
def api_snapshots(hid):
    return jsonify({"ok": True, "snapshots": db.get_snapshots(hid)})


# ---------------- API：追加本金（同一产品继续买入） ----------------
@app.route("/api/additions", methods=["GET"])
def api_additions():
    return jsonify({"ok": True, "additions": db.list_additions()})


@app.route("/api/holdings/<int:hid>/capital", methods=["POST"])
def api_add_capital(hid):
    h = db.get_holding(hid)
    if not h:
        return jsonify({"ok": False, "error": "持仓不存在"})
    body = request.get_json(silent=True) or {}
    try:
        amount = float(body.get("amount"))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "金额格式不正确"})
    invest_date = (body.get("invest_date") or "").strip() or datetime.now().strftime("%Y-%m-%d")
    note = (body.get("note") or "").strip()
    if amount <= 0:
        return jsonify({"ok": False, "error": "请填写大于 0 的金额"})
    try:
        datetime.strptime(invest_date, "%Y-%m-%d")
    except ValueError:
        return jsonify({"ok": False, "error": "买入日期格式不正确"})
    settle_date = (body.get("settle_date") or "").strip() or service.next_settle_date(invest_date)
    add_id = db.add_addition(hid, amount, invest_date, settle_date, note)
    service.rebuild_all()
    logger.info(f"追加本金 {h['product_name']} +{amount} 买于 {invest_date}，入账 {settle_date}")
    return jsonify({"ok": True, "message": "追加本金已记录", "id": add_id,
                    "settle_date": settle_date,
                    "first_profit_date": service.next_settle_date(settle_date)})


@app.route("/api/additions/<int:add_id>", methods=["DELETE"])
def api_delete_addition(add_id):
    db.delete_addition(add_id)
    service.rebuild_all()
    return jsonify({"ok": True, "message": "追加本金已删除并重算"})


# ---------------- API：本金支出 ----------------
@app.route("/api/withdrawals", methods=["GET"])
def api_withdrawals():
    return jsonify({"ok": True, "withdrawals": db.list_withdrawals()})


@app.route("/api/withdrawals", methods=["POST"])
def api_add_withdrawal():
    body = request.get_json(silent=True) or {}
    try:
        amount = float(body.get("amount"))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "支出金额格式不正确"})
    expense_date = (body.get("expense_date") or "").strip()
    note = (body.get("note") or "").strip()
    if amount <= 0 or not expense_date:
        return jsonify({"ok": False, "error": "请填写支出金额和日期"})
    try:
        datetime.strptime(expense_date, "%Y-%m-%d")
    except ValueError:
        return jsonify({"ok": False, "error": "支出日期格式不正确"})

    bank = (body.get("bank") or "").strip() or None
    raw_hid = body.get("holding_id")
    if bank not in (None, "ccb", "psbc", "spdb"):
        return jsonify({"ok": False, "error": "未知银行"})

    if raw_hid not in (None, "", 0, "0"):
        # 指定产品扣减（减仓 / 单只产品支出）：金额全部从这一只产品扣掉。
        # 适用于所有银行；建行顶部统一「记录支出」不传 holding_id，仍走平均分摊。
        try:
            holding_id = int(raw_hid)
        except (TypeError, ValueError):
            return jsonify({"ok": False, "error": "产品编号不正确"})
        h = db.get_holding(holding_id)
        if not h:
            return jsonify({"ok": False, "error": "所选产品不存在"})
        hbank = service.detect_bank(h["product_code"], h["product_name"])
        if bank and hbank != bank:
            return jsonify({"ok": False, "error": "所选产品与银行不匹配"})
        bank = hbank  # 以产品实际所属银行为准
        if h.get("settle_date") and h["settle_date"] > datetime.now().strftime("%Y-%m-%d"):
            return jsonify({"ok": False, "error": "该产品尚未入账，暂不能减仓"})
    else:
        # 未指定产品：按银行口径
        holding_id = None
        if bank in ("psbc", "spdb"):
            return jsonify({"ok": False, "error": "请选择要扣减的产品"})
        # 建行 / 未指定银行：金额在该银行全部已入账产品间平均分摊

    wid = db.add_withdrawal(amount, expense_date, note, bank=bank, holding_id=holding_id)
    service.rebuild_all()   # 支出后重算所有持仓
    return jsonify({"ok": True, "message": "支出已记录并重算", "id": wid})


@app.route("/api/withdrawals/<int:wid>", methods=["DELETE"])
def api_delete_withdrawal(wid):
    db.delete_withdrawal(wid)
    service.rebuild_all()   # 删除后重算所有持仓
    return jsonify({"ok": True, "message": "支出已删除并重算"})


# ---------------- API：刷新 ----------------
@app.route("/api/refresh", methods=["POST"])
def api_refresh():
    ok, fails, details = service.daily_update()
    return jsonify({"ok": len(fails) == 0, "details": details, "success": ok, "fails": fails})


@app.route("/api/holdings/<int:hid>/refresh", methods=["POST"])
def api_refresh_holding(hid):
    h = db.get_holding(hid)
    if not h:
        return jsonify({"ok": False, "error": "持仓不存在"})
    try:
        service.refresh_product(h["product_code"], h["product_name"], deep=True)
        summary = service.rebuild_holding(hid)
        return jsonify({"ok": True, "summary": summary, "message": "已刷新"})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


# ---------------- API：设置 ----------------
@app.route("/api/settings", methods=["POST"])
def api_save_settings():
    body = request.get_json(silent=True) or {}
    allowed = ["smtp_host", "smtp_port", "smtp_user", "smtp_pass", "mail_from",
               "mail_to", "push_enabled", "push_hour", "push_minute",
               "alert_enabled", "alert_annual",
               "historical_profit", "psbc_historical_profit", "spdb_historical_profit",
               "app_yesterday_profit", "app_overall_annual", "app_ref_date",
               "psbc_app_yesterday_profit", "psbc_app_overall_annual",
               "spdb_app_yesterday_profit", "spdb_app_overall_annual"]
    updates = {k: body[k] for k in allowed if k in body}
    if "alert_annual" in updates:
        try:
            v = float(updates["alert_annual"])
        except (TypeError, ValueError):
            return jsonify({"ok": False, "error": "预警值必须是数字，例如 1.0"})
        if v < 0 or v > 20:
            return jsonify({"ok": False, "error": "预警值请填 0 ~ 20 之间的百分数"})
        updates["alert_annual"] = f"{v:g}"
    db.set_settings(updates)
    setup_scheduler()
    return jsonify({"ok": True, "message": "设置已保存"})


@app.route("/api/mail/test", methods=["POST"])
def api_mail_test():
    body = request.get_json(silent=True) or {}
    to = (body.get("to") or "").strip() or None
    ok, msg = mailer.test_connection()
    if not ok:
        return jsonify({"ok": False, "error": msg})
    data = service.dashboard_data()
    ok, msg = mailer.send_report(data, ["这是一封测试邮件"], override_to=to)
    return jsonify({"ok": ok, "message": msg, "error": None if ok else msg})


@app.route("/api/alert/check", methods=["POST"])
def api_alert_check():
    """按当前数据检查年化预警；send=true 时直接发预警邮件（设置页「立即检查」用）。"""
    body = request.get_json(silent=True) or {}
    data = service.dashboard_data()
    threshold = body.get("alert_annual")
    low, threshold = service.low_annual_products(data, threshold)
    payload = {"threshold": round(threshold, 4), "count": len(low), "products": low}
    if not low:
        payload.update({"ok": True, "email_sent": False,
                        "message": f"当前没有产品低于 {threshold:.2f}%，无需提醒"})
        return jsonify(payload)
    if body.get("send", True):
        ok, msg = mailer.send_alert(data, low, threshold)
        payload.update({"ok": ok, "email_sent": ok,
                        "message": "预警邮件已发送" if ok else f"邮件发送失败：{msg}"})
        return jsonify(payload)
    payload.update({"ok": True, "email_sent": False, "message": f"检出 {len(low)} 只产品低于阈值"})
    return jsonify(payload)


@app.route("/api/diag")
def api_diag():
    """网络连通性诊断：容器/NAS 上抓不到数据时用。"""
    targets = service.network_diag()
    all_ok = all(t["ok"] for t in targets)
    return jsonify({"ok": True, "all_ok": all_ok, "targets": targets})


@app.route("/api/report/preview")
def api_report_preview():
    data = service.dashboard_data()
    return mailer.build_report_page(data)


@app.route("/api/data/export")
def api_data_export():
    """导出全部业务数据（含设置）为 JSON，前端据此生成备份文件下载。"""
    try:
        payload = db.export_all()
        return jsonify({"ok": True, "data": payload})
    except Exception as e:
        logger.exception("导出数据失败")
        return jsonify({"ok": False, "error": f"导出失败：{e}"}), 500


@app.route("/api/data/import", methods=["POST"])
def api_data_import():
    """从备份 JSON 覆盖式恢复全部业务数据。"""
    body = request.get_json(silent=True) or {}
    payload = body.get("data")
    mode = (body.get("mode") or "replace").strip()
    if mode not in ("replace", "merge"):
        return jsonify({"ok": False, "error": "导入模式不正确"})
    if not isinstance(payload, dict):
        return jsonify({"ok": False, "error": "请提供有效的备份数据"})
    try:
        stats = db.import_all(payload, mode=mode)
        # 恢复后重算全部持仓快照，并让调度器按新设置重新加载
        service.rebuild_all()
        setup_scheduler()
        return jsonify({"ok": True, "message": "数据已导入并重建", "stats": stats})
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)})
    except Exception as e:
        logger.exception("导入数据失败")
        return jsonify({"ok": False, "error": f"导入失败：{e}"}), 500


# ---------------- 启动 ----------------
def main():
    db.init_db()
    setup_scheduler()
    scheduler.start()
    logger.info(f"服务启动，端口 {config.PORT}")
    app.run(host=config.HOST, port=config.PORT, debug=False, use_reloader=False)


if __name__ == "__main__":
    main()
