"""龙钱包收益监控 - Flask Web 服务 + 后台定时任务。"""
import logging
import hmac
import threading
import uuid
from datetime import datetime, timedelta

from functools import wraps

from flask import Flask, render_template, request, jsonify, redirect, url_for, session

import config
import db
import service
import mailer
from logo_asset import LOGO_DATA_URI
from pwa import pwa_bp
from apscheduler.schedulers.background import BackgroundScheduler

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")
logger = logging.getLogger("app")

app = Flask(__name__)
app.secret_key = config.SECRET_KEY
# 持久登录：设置会话有效期，登录时 session.permanent=True 后 cookie 会带上 Max-Age/Expires
app.permanent_session_lifetime = timedelta(days=config.SESSION_LIFETIME_DAYS)


# ---------------- 登录认证与权限 ----------------
# 未登录可访问的路径（登录页、登录接口、图标等）
# /api/widget/banks 免登录，但接口内部用 widget_token 独立鉴权
PUBLIC_PATHS = {"/login", "/api/auth/login", "/favicon.ico", "/api/widget/banks",
                "/manifest.json", "/sw.js", "/offline.html", "/pwa-offline-lib.js"}

# PWA 支持：manifest / Service Worker / 离线兜底页
app.register_blueprint(pwa_bp)


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
    # 预览报告含完整持仓与收益信息，仅管理员可查看
    if path == "/api/report/preview" and user["role"] != "admin":
        return jsonify({"ok": False, "error": "只有管理员可以查看预览报告"}), 403
    # 设置页仅管理员可访问，普通用户直接回首页
    if path == "/settings" and user["role"] != "admin":
        return redirect(url_for("index"))
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
        "logo_data_uri": LOGO_DATA_URI,
        # 当前请求的 sharecode（优先 URL 参数，兜底环境变量），供 PWA 资源 URL 透传鉴权
        "sharecode": request.args.get("sharecode", config.SHARECODE_FALLBACK),
    }


# ---------------- 抓取日志（存 SQLite，多实例/多 worker 共享，供前端轮询） ----------------
# 说明：早期用进程内存 dict，发布平台多实例时 start 与轮询落到不同进程，
#       轮询读不到日志导致前端一直转圈。改为 db.fetch_logs 表共享存储。
def _log_new(run_id):
    db.fetch_log_new(run_id)


def _log_append(run_id, msg, level="info"):
    db.fetch_log_append(run_id, msg, level)


def _log_finish(run_id, status):
    db.fetch_log_finish(run_id, status)


def _make_run_id():
    return datetime.now().strftime("%Y%m%d%H%M%S") + "-" + uuid.uuid4().hex[:6]


def _run_fetch_async(run_id):
    """在后台线程执行抓取并逐行写入日志，结束置状态。

    抓取耗时可能几十秒（网络请求 + TLS 重协商），必须在后台线程跑，
    否则同步长请求会占住 Web 线程，导致前端轮询日志的请求排队、卡在"正在连接"。
    """
    def emit(msg, level="info"):
        logger.info(msg)
        _log_append(run_id, msg, level)

    try:
        ok, fails, details = service.daily_update(log=emit)
        _log_append(run_id, f"抓取完成：成功 {ok} 只，失败 {len(fails)} 只", "step")
        _log_finish(run_id, "error" if fails else "ok")
    except Exception as e:
        logger.exception("后台抓取异常")
        _log_append(run_id, f"抓取异常: {e}", "error")
        _log_finish(run_id, "error")


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


def fetch_job(run_id=None, label="定时抓取"):
    """通用抓取任务：仅抓取更新数据 + 重算快照，不发邮件。

    run_id 传入时，把每一步日志写入内存日志区供前端查看。
    """
    logger.info(f"=== 执行{label}任务 ===")

    def emit(msg, level="info"):
        logger.info(msg)
        if run_id:
            _log_append(run_id, msg, level)

    try:
        ok, fails, details = service.daily_update(log=emit)
        logger.info(f"{label}完成: 成功 {ok} 只" + (f"，失败 {len(fails)} 只: {fails}" if fails else ""))
        if run_id:
            _log_finish(run_id, "error" if fails else "ok")
    except Exception as e:
        logger.error(f"{label}任务异常: {e}")
        if run_id:
            _log_append(run_id, f"任务异常: {e}", "error")
            _log_finish(run_id, "error")


def afternoon_fetch_job():
    """兼容旧调用：下午补充抓取。"""
    fetch_job(label="下午补充抓取")


def backup_job():
    """每日定时：导出全部数据备份并发送到邮箱。受 backup_enabled 开关控制。"""
    settings = db.get_settings()
    if settings.get("backup_enabled", "0") != "1":
        logger.info("自动备份已关闭，跳过")
        return
    logger.info("=== 执行每日自动备份任务 ===")
    try:
        # 发邮箱的备份必须脱敏：不含邮箱配置与用户信息
        payload = db.export_all(sanitize=True)
        success, msg = mailer.send_backup(payload)
        logger.info(f"自动备份邮件: {msg}")
        if success:
            db.set_setting("last_backup_date", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    except Exception as e:
        logger.error(f"自动备份任务异常: {e}")


def fixed_notice_job():
    """每日定时：检查今明两日到期存单并发提醒邮件（开关与去重在 mailer 内）。"""
    try:
        ok, msg = mailer.send_fixed_notice()
        logger.info(f"定期到期提醒: {msg}")
    except Exception as e:
        logger.error(f"定期到期提醒任务异常: {e}")


def setup_scheduler():
    s = db.get_settings()
    hour = int(s.get("push_hour") or config.DEFAULT_PUSH_HOUR)
    minute = int(s.get("push_minute") or config.DEFAULT_PUSH_MINUTE)
    backup_hour = int(s.get("backup_hour") or config.DEFAULT_BACKUP_HOUR)
    backup_minute = int(s.get("backup_minute") or config.DEFAULT_BACKUP_MINUTE)
    notice_time = (s.get("fixed_notice_time") or "09:00").strip()
    try:
        notice_hour, notice_minute = (int(x) for x in notice_time.split(":"))
    except (ValueError, TypeError):
        notice_hour, notice_minute = 9, 0
    fetch_times = db.get_fetch_times()
    scheduler.remove_all_jobs()
    # 主任务：每天指定时间更新数据 + 邮件推送
    scheduler.add_job(scheduled_job, "cron", hour=hour, minute=minute, id="daily_push", replace_existing=True)
    # 抓取任务：按用户在设置页配置的时间点，注册多个纯抓取任务（不发邮件）
    for idx, t in enumerate(fetch_times):
        fh, fm = (int(x) for x in t.split(":"))
        scheduler.add_job(
            fetch_job, "cron", hour=fh, minute=fm,
            kwargs={"label": f"定时抓取 {t}"},
            id=f"fetch_{idx}", replace_existing=True,
        )
    # 备份任务：每天指定时间导出数据并发送到邮箱（开关在任务内部判断）
    scheduler.add_job(
        backup_job, "cron",
        hour=backup_hour, minute=backup_minute,
        id="daily_backup", replace_existing=True,
    )
    # 定期到期提醒：每天指定时间检查（开关在任务内部判断）
    scheduler.add_job(
        fixed_notice_job, "cron",
        hour=notice_hour, minute=notice_minute,
        id="fixed_notice", replace_existing=True,
    )
    logger.info(
        f"定时任务已设置: 主任务每天 {hour:02d}:{minute:02d}，"
        f"自动抓取时间点 {fetch_times}，"
        f"自动备份每天 {backup_hour:02d}:{backup_minute:02d}，"
        f"定期到期提醒每天 {notice_hour:02d}:{notice_minute:02d}"
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
                           last_fetch_time=db.get_last_fetch_time(),
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


@app.route("/fixed")
def fixed_page():
    """定期监控页：从「记账日历」小程序移植的定期存款管理。"""
    s = db.get_settings()
    users = db.list_users()
    return render_template("fixed.html", settings=s, users=users)


@app.route("/withdrawals")
def withdrawals_page():
    """本金支出 / 赎回：独立的支出记录台账页。"""
    data = service.dashboard_data()
    return render_template("withdrawals.html", data=data)


@app.route("/fixed/deposit")
def fixed_deposit_page():
    """新增 / 编辑存单：独立表单页（仅管理员）。?id=xxx 为编辑模式。"""
    u = current_user()
    if not u or u["role"] != "admin":
        return redirect(url_for("fixed_page"))
    edit_id = request.args.get("id", "").strip()
    return render_template("deposit-form.html", users=db.list_users(), edit_id=edit_id)


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
    # 持久登录：30 天内无需重复登录（cookie 带 Max-Age/Expires）
    session.permanent = True
    db.touch_login(user["id"])
    # 记录登录历史：谁、什么时间、来自哪个 IP / 设备
    db.record_login(
        user["id"], user.get("username", username), user.get("display_name", ""),
        user["role"],
        ip=(request.headers.get("X-Forwarded-For", "").split(",")[0].strip()
            or request.remote_addr or ""),
        user_agent=(request.headers.get("User-Agent") or "")[:300],
    )
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


@app.route("/api/login-history")
def api_login_history():
    """登录历史（仅管理员）：谁、什么时间、来自哪个 IP / 设备。"""
    u = current_user()
    if not u or u["role"] != "admin":
        return jsonify({"ok": False, "error": "只有管理员可以查看登录记录"}), 403
    return jsonify({"ok": True, "items": db.list_login_history(limit=200)})


@app.route("/api/login-history/clear", methods=["POST"])
def api_login_history_clear():
    """清空登录历史（仅管理员）。"""
    u = current_user()
    if not u or u["role"] != "admin":
        return jsonify({"ok": False, "error": "只有管理员可以清空登录记录"}), 403
    db.clear_login_history()
    logger.info(f"清空登录历史（操作人 {u.get('username')}）")
    return jsonify({"ok": True})


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
@app.route("/api/refresh/start", methods=["POST"])
def api_refresh_start():
    """发起一次抓取：后台线程执行，立即返回 run_id；前端据此轮询日志。"""
    run_id = _make_run_id()
    _log_new(run_id)
    _log_append(run_id, "已创建抓取任务，正在后台抓取…", "step")
    t = threading.Thread(target=_run_fetch_async, args=(run_id,), daemon=True)
    t.start()
    return jsonify({"ok": True, "run_id": run_id})


@app.route("/api/refresh", methods=["POST"])
def api_refresh():
    """同步执行抓取，一次性返回完整日志行。

    不依赖 run_id / 轮询 / 跨请求状态，规避多实例部署下 start 与轮询分落不同实例、
    或两阶段请求被网关串行化导致的"一直正在连接"问题。抓取本身可能耗时数十秒。
    """
    lines = []

    def emit(msg, level="info"):
        lines.append({"t": datetime.now().strftime("%H:%M:%S"), "level": level, "msg": str(msg)})

    try:
        ok, fails, details = service.daily_update(log=emit)
        return jsonify({"ok": len(fails) == 0, "lines": lines, "details": details,
                        "success": ok, "fails": fails})
    except Exception as e:
        logger.exception("手动刷新异常")
        lines.append({"t": datetime.now().strftime("%H:%M:%S"), "level": "error", "msg": f"抓取异常: {e}"})
        return jsonify({"ok": False, "lines": lines, "details": [f"✗ {e}"],
                        "success": 0, "fails": [str(e)]})


@app.route("/api/refresh/log")
def api_refresh_log():
    """按 run_id 返回抓取日志（含状态），供前端模态框轮询。"""
    run_id = (request.args.get("run_id") or "").strip()
    entry = db.fetch_log_get(run_id)
    if entry is None:
        return jsonify({"ok": False, "error": "未知的任务编号"})
    return jsonify({
        "ok": True,
        "run_id": run_id,
        "status": entry["status"],
        "lines": entry["lines"],
    })


@app.route("/api/refresh/active")
def api_refresh_active():
    """查询当前是否有后台抓取任务仍在运行，供页面刷新后恢复按钮状态。"""
    run_id = db.fetch_log_active()
    return jsonify({"ok": True, "run_id": run_id, "running": bool(run_id)})


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
               "mail_to", "push_enabled", "push_hour", "push_minute", "fetch_times",
               "backup_enabled", "backup_hour", "backup_minute",
               "alert_enabled", "alert_annual",
               "historical_profit", "psbc_historical_profit", "spdb_historical_profit",
               "app_yesterday_profit", "app_overall_annual", "app_ref_date",
               "psbc_app_yesterday_profit", "psbc_app_overall_annual",
               "spdb_app_yesterday_profit", "spdb_app_overall_annual",
               "fixed_notice_enabled", "fixed_notice_time", "fixed_notice_email"]
    updates = {k: body[k] for k in allowed if k in body}
    if "alert_annual" in updates:
        try:
            v = float(updates["alert_annual"])
        except (TypeError, ValueError):
            return jsonify({"ok": False, "error": "预警值必须是数字，例如 1.0"})
        if v < 0 or v > 20:
            return jsonify({"ok": False, "error": "预警值请填 0 ~ 20 之间的百分数"})
        updates["alert_annual"] = f"{v:g}"
    if "fetch_times" in updates:
        raw = updates["fetch_times"]
        items = raw if isinstance(raw, list) else []
        if isinstance(raw, str):
            import json as _json
            try:
                parsed = _json.loads(raw)
                items = parsed if isinstance(parsed, list) else []
            except (ValueError, TypeError):
                items = []
        updates["fetch_times"] = db.normalize_fetch_times(items)
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


# ---------------- 桌面小组件免登录接口（token 鉴权） ----------------
@app.route("/api/widget/banks")
def api_widget_banks():
    """供 iOS Scriptable 桌面组件读取：三家银行年化 + 汇总。

    免网页登录，但必须携带正确的 widget_token（URL 参数 token 或请求头 X-Widget-Token）。
    token 在数据库中加密存储，get_settings 读取时已透明解密为明文。
    """
    supplied = (request.args.get("token") or request.headers.get("X-Widget-Token") or "").strip()
    expected = db.get_settings().get("widget_token", "")
    if not expected or not supplied or not hmac.compare_digest(supplied, expected):
        return jsonify({"ok": False, "error": "invalid token"}), 401

    import base64, os
    data = service.dashboard_data()
    pf = data["portfolio"]
    bs = pf.get("bank_summary", {})
    names = {"ccb": "建设银行", "psbc": "邮储银行", "spdb": "浦发银行"}

    # 当日收益：每笔按 市值/10000 × 每万份收益 汇总到银行（与七日年化同口径的最新净值）
    daily_by_bank = {}
    for h in data.get("holdings", []):
        v = h.get("current_value", 0) or 0
        p10k = h.get("latest_per10k")
        if p10k is not None and v:
            daily_by_bank[h.get("bank")] = daily_by_bank.get(h.get("bank"), 0) + v / 10000.0 * float(p10k)

    logo_dir = os.path.join(app.root_path, "static")

    def logo_b64(code):
        p = os.path.join(logo_dir, f"bank_{code}.png")
        try:
            with open(p, "rb") as f:
                return base64.b64encode(f.read()).decode("ascii")
        except OSError:
            return ""

    banks = []
    for code in ("ccb", "psbc", "spdb"):
        b = bs.get(code)
        banks.append({
            "code": code,
            "name": names[code],
            "annual": (round(b["avg_annual"], 2) if b.get("avg_annual") is not None
                       else (round(b["annual"], 2) if b.get("annual") is not None else None)),
            "nav_date": b.get("avg_date"),
            "daily_profit": round(daily_by_bank.get(code, 0), 2),
            "value": b.get("value", 0),
            "profit": b.get("profit", 0),
            "count": b.get("count", 0),
            "logo": logo_b64(code),
        })
    resp = jsonify({
        "ok": True,
        "updated_at": db.get_last_fetch_time(),
        "banks": banks,
        "total": {
            "value": pf.get("total_value", 0),
            "profit": pf.get("total_profit", 0),
            "principal": pf.get("total_principal", 0),
            "daily_profit": round(sum(daily_by_bank.values()), 2),
        },
    })
    resp.headers["Cache-Control"] = "no-store"
    return resp


@app.route("/api/widget/token/regenerate", methods=["POST"])
def api_widget_token_regenerate():
    """管理员重新生成桌面组件访问令牌（设置页调用）。旧 token 立即失效。"""
    u = current_user()
    if u is None or u["role"] != "admin":
        return jsonify({"ok": False, "error": "只有管理员可以操作"}), 403
    token = db.regenerate_widget_token()
    return jsonify({"ok": True, "token": token})


@app.route("/api/data/export")
def api_data_export():
    """导出全部业务数据（含设置）为 JSON，前端据此生成备份文件下载。"""
    try:
        payload = db.export_all()
        return jsonify({"ok": True, "data": payload})
    except Exception as e:
        logger.exception("导出数据失败")
        return jsonify({"ok": False, "error": f"导出失败：{e}"}), 500


@app.route("/api/backup/send-now", methods=["POST"])
def api_backup_send_now():
    """立即导出数据备份并发送到邮箱（设置页测试用），不改变定时开关。"""
    body = request.get_json(silent=True) or {}
    to = (body.get("to") or "").strip() or None
    try:
        # 发邮箱的备份必须脱敏：不含邮箱配置与用户信息
        payload = db.export_all(sanitize=True)
        ok, msg = mailer.send_backup(payload, override_to=to, test=True)
        return jsonify({"ok": ok, "message": "备份邮件已发送" if ok else f"发送失败：{msg}",
                        "error": None if ok else msg})
    except Exception as e:
        logger.exception("立即发送备份失败")
        return jsonify({"ok": False, "error": f"发送失败：{e}"}), 500


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


# ---------------- API：定期存款存单（定期监控）----------------
def _deposit_calc(save_date, expire_date, principal, rate_percent):
    """按单利计算存期月数与预估利息，后端兜底（前端也会算一遍）。"""
    from datetime import date as _date
    try:
        a = _date.fromisoformat(save_date)
        b = _date.fromisoformat(expire_date)
    except (ValueError, TypeError):
        return 0.0, 0.0, float(principal or 0)
    days = (b - a).days
    if days <= 0:
        return 0.0, 0.0, float(principal or 0)
    # 存期月数：整月 + 零头天数按 30 天折算（与小程序 termMonths 口径一致）
    months = (b.year - a.year) * 12 + (b.month - a.month)
    if b.day < a.day:
        months -= 1
    if months < 0:
        months = 0
    from datetime import timedelta as _td
    anchor_year = a.year + (a.month - 1 + months) // 12
    anchor_month = (a.month - 1 + months) % 12 + 1
    import calendar as _cal
    anchor_day = min(a.day, _cal.monthrange(anchor_year, anchor_month)[1])
    anchor = _date(anchor_year, anchor_month, anchor_day)
    rest = max(0, (b - anchor).days)
    term_months = months + min(rest, 30) / 30.0
    p = float(principal or 0)
    r = float(rate_percent or 0)
    interest = round(p * (r / 100.0) * (term_months / 12.0), 2)
    return round(term_months, 4), interest, round(p + interest, 2)


def _validate_deposit(body, partial=False):
    """校验并规范化存单入参，返回 (data, error)。"""
    data = {}
    if not partial or "bank_name" in body:
        bank_name = (body.get("bank_name") or "").strip()
        if not bank_name:
            return None, "请填写银行名称"
        data["bank_name"] = bank_name
    if "bank_id" in body:
        data["bank_id"] = (body.get("bank_id") or "").strip()
    if "owner_uid" in body:
        uid = body.get("owner_uid")
        try:
            data["owner_uid"] = int(uid) if uid not in (None, "", "null") else None
        except (ValueError, TypeError):
            return None, "归属成员格式不正确"
    if not partial or "save_date" in body:
        save_date = (body.get("save_date") or "").strip()
        if not save_date:
            return None, "请填写存入日期"
        data["save_date"] = save_date
    if not partial or "expire_date" in body:
        expire_date = (body.get("expire_date") or "").strip()
        if not expire_date:
            return None, "请填写到期日期"
        data["expire_date"] = expire_date
    if not partial or "principal" in body:
        try:
            principal = float(body.get("principal"))
        except (TypeError, ValueError):
            return None, "本金格式不正确"
        if principal <= 0:
            return None, "本金必须大于 0"
        data["principal"] = principal
    if "rate_percent" in body:
        try:
            data["rate_percent"] = float(body.get("rate_percent") or 0)
        except (ValueError, TypeError):
            return None, "年利率格式不正确"
    if "remark" in body:
        data["remark"] = (body.get("remark") or "").strip()

    # 重算存期/利息（当关键字段变化时）
    need_calc = any(k in data for k in ("save_date", "expire_date", "principal", "rate_percent"))
    if need_calc:
        save_date = data.get("save_date")
        expire_date = data.get("expire_date")
        principal = data.get("principal")
        rate = data.get("rate_percent", 0)
        if save_date and expire_date and principal is not None:
            if not partial:
                tm, interest, total = _deposit_calc(save_date, expire_date, principal, rate)
                data["term_months"], data["interest"], data["total_money"] = tm, interest, total
            else:
                # 部分更新：取库里现有值补齐后再算
                dep_id = body.get("_id")
                old = db.get_fixed_deposit(dep_id) if dep_id else None
                sd = save_date or (old or {}).get("save_date")
                ed = expire_date or (old or {}).get("expire_date")
                pr = principal if principal is not None else (old or {}).get("principal")
                rt = rate if "rate_percent" in data else (old or {}).get("rate_percent", 0)
                if sd and ed and pr is not None:
                    tm, interest, total = _deposit_calc(sd, ed, pr, rt)
                    data["term_months"], data["interest"], data["total_money"] = tm, interest, total
    return data, None


@app.route("/api/fixed/deposits")
def api_fixed_list():
    owner = request.args.get("owner_uid")
    owner_uid = int(owner) if owner and owner.isdigit() else None
    return jsonify({"ok": True, "list": db.list_fixed_deposits(owner_uid)})


@app.route("/api/fixed/deposits", methods=["POST"])
def api_fixed_add():
    body = request.get_json(silent=True) or {}
    data, err = _validate_deposit(body, partial=False)
    if err:
        return jsonify({"ok": False, "error": err})
    try:
        new_id = db.add_fixed_deposit(data)
    except Exception as e:
        logger.exception("新增存单失败")
        return jsonify({"ok": False, "error": f"保存失败：{e}"})
    return jsonify({"ok": True, "message": "存单已添加", "id": new_id})


@app.route("/api/fixed/deposits/<int:dep_id>", methods=["POST"])
def api_fixed_update(dep_id):
    body = request.get_json(silent=True) or {}
    body["_id"] = dep_id
    data, err = _validate_deposit(body, partial=True)
    if err:
        return jsonify({"ok": False, "error": err})
    if not data:
        return jsonify({"ok": True, "message": "没有需要更新的内容"})
    try:
        n = db.update_fixed_deposit(dep_id, data)
    except Exception as e:
        logger.exception("更新存单失败")
        return jsonify({"ok": False, "error": f"保存失败：{e}"})
    return jsonify({"ok": True, "message": "存单已更新", "updated": n})


@app.route("/api/fixed/deposits/<int:dep_id>", methods=["DELETE"])
def api_fixed_delete(dep_id):
    n = db.delete_fixed_deposit(dep_id)
    return jsonify({"ok": True, "deleted": n})


@app.route("/api/fixed/deposits/batch-delete", methods=["POST"])
def api_fixed_batch_delete():
    body = request.get_json(silent=True) or {}
    ids = body.get("ids") or []
    if not isinstance(ids, list):
        return jsonify({"ok": False, "error": "参数格式不正确"})
    n = db.delete_fixed_deposits(ids)
    return jsonify({"ok": True, "deleted": n})


@app.route("/api/fixed/deposits/batch-owner", methods=["POST"])
def api_fixed_batch_owner():
    """批量修改存单归属成员（仅管理员，写操作由全局 auth_guard 拦截只读成员）。"""
    body = request.get_json(silent=True) or {}
    ids = body.get("ids") or []
    if not isinstance(ids, list) or not ids:
        return jsonify({"ok": False, "error": "请先选择存单"})
    if "owner_uid" not in body:
        return jsonify({"ok": False, "error": "缺少归属成员参数"})
    owner_uid = body.get("owner_uid")
    if owner_uid is not None:
        try:
            owner_uid = int(owner_uid)
        except (TypeError, ValueError):
            return jsonify({"ok": False, "error": "归属成员参数不正确"})
        if not db.get_user(owner_uid):
            return jsonify({"ok": False, "error": "目标成员不存在"})
    n = db.update_fixed_deposits_owner(ids, owner_uid)
    return jsonify({"ok": True, "updated": n})


@app.route("/api/fixed/export")
def api_fixed_export():
    """导出定期存单为「记账日历」小程序兼容的 finance-calendar JSON。"""
    rows = db.list_fixed_deposits()
    items = [{
        "bankName": d.get("bank_name", ""),
        "saveDate": d.get("save_date", ""),
        "expireDate": d.get("expire_date", ""),
        "principal": round(float(d.get("principal") or 0), 2),
        "ratePercent": float(d.get("rate_percent") or 0),
        "interest": round(float(d.get("interest") or 0), 2),
        "totalMoney": round(float(d.get("total_money") or 0), 2),
        "remark": d.get("remark", "") or "",
    } for d in rows]
    from datetime import timezone as _tz
    payload = {
        "app": "finance-calendar",
        "version": 1,
        "exportAt": datetime.now(_tz.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
        "items": items,
    }
    return jsonify({"ok": True, "data": payload})


@app.route("/api/fixed/import", methods=["POST"])
def api_fixed_import():
    """导入 finance-calendar JSON（兼容小程序导出格式），支持 append / replace。"""
    body = request.get_json(silent=True) or {}
    payload = body.get("data")
    mode = (body.get("mode") or "append").strip()
    if mode not in ("append", "replace"):
        return jsonify({"ok": False, "error": "导入模式不正确"})
    # data 可以是完整 payload（含 items），也可以直接是 items 数组
    if isinstance(payload, list):
        items = payload
    elif isinstance(payload, dict):
        items = payload.get("items")
    else:
        items = None
    if not isinstance(items, list):
        return jsonify({"ok": False, "error": "未找到有效的 items 数据"})

    # 归属成员：导入的存单统一归到指定成员，默认当前管理员
    owner_raw = body.get("owner_uid")
    u = current_user()
    try:
        owner_uid = int(owner_raw) if owner_raw not in (None, "", "null") else (u["id"] if u else None)
    except (ValueError, TypeError):
        return jsonify({"ok": False, "error": "归属成员格式不正确"})

    from datetime import date as _date
    rows, skipped = [], []
    for i, it in enumerate(items):
        if not isinstance(it, dict):
            skipped.append({"index": i, "reason": "条目格式不正确"})
            continue
        bank_name = str(it.get("bankName") or it.get("bank_name") or "").strip()
        save_date = str(it.get("saveDate") or it.get("save_date") or "").strip()
        expire_date = str(it.get("expireDate") or it.get("expire_date") or "").strip()
        if not bank_name or not save_date or not expire_date:
            skipped.append({"index": i, "reason": "缺少银行/存入日/到期日"})
            continue
        try:
            _date.fromisoformat(save_date)
            _date.fromisoformat(expire_date)
        except (ValueError, TypeError):
            skipped.append({"index": i, "reason": "日期格式不正确"})
            continue
        if expire_date <= save_date:
            skipped.append({"index": i, "reason": "到期日不晚于存入日"})
            continue
        try:
            principal = float(it.get("principal"))
        except (TypeError, ValueError):
            skipped.append({"index": i, "reason": "本金格式不正确"})
            continue
        if principal <= 0:
            skipped.append({"index": i, "reason": "本金必须大于 0"})
            continue
        try:
            rate = float(it.get("ratePercent") or it.get("rate_percent") or 0)
        except (TypeError, ValueError):
            rate = 0.0
        # 利息/本息：优先采用文件里的值，缺失时按存期重算
        try:
            interest = float(it.get("interest")) if it.get("interest") not in (None, "") else None
        except (TypeError, ValueError):
            interest = None
        try:
            total = float(it.get("totalMoney") or it.get("total_money")) if (it.get("totalMoney") or it.get("total_money")) else None
        except (TypeError, ValueError):
            total = None
        tm, calc_interest, calc_total = _deposit_calc(save_date, expire_date, principal, rate)
        rows.append({
            "owner_uid": owner_uid,
            "bank_id": "",
            "bank_name": bank_name,
            "save_date": save_date,
            "expire_date": expire_date,
            "principal": principal,
            "rate_percent": rate,
            "term_months": tm,
            "interest": round(interest, 2) if interest is not None else calc_interest,
            "total_money": round(total, 2) if total is not None else calc_total,
            "remark": str(it.get("remark") or "").strip(),
        })

    if not rows:
        return jsonify({"ok": False, "error": "没有可导入的有效存单", "skipped": skipped[:20]})
    try:
        if mode == "replace":
            db.clear_fixed_deposits()
        added = db.add_fixed_deposits_bulk(rows)
    except Exception as e:
        logger.exception("导入定期存单失败")
        return jsonify({"ok": False, "error": f"导入失败：{e}"})
    return jsonify({
        "ok": True,
        "message": f"成功导入 {added} 笔存单" + (f"，跳过 {len(skipped)} 笔" if skipped else ""),
        "imported": added,
        "skipped_count": len(skipped),
        "skipped": skipped[:20],
    })


@app.route("/api/fixed/notice/test", methods=["POST"])
def api_fixed_notice_test():
    """发送一封定期到期提醒测试邮件（到期当天 + 到期前一天，与实际提醒一致）。"""
    from datetime import timedelta as _td
    today = datetime.now().date()
    due_today = db.fixed_deposits_expiring_on([today.isoformat()])
    due_tomorrow = db.fixed_deposits_expiring_on([(today + _td(days=1)).isoformat()])
    s = db.get_settings()
    to = (s.get("fixed_notice_email") or "").strip() or (s.get("mail_to") or "").strip()
    subject = f"定期存款到期提醒测试 · 到期当天 {len(due_today)} 笔 / 到期前一天 {len(due_tomorrow)} 笔"
    # 与实际提醒复用同一套构造，测试邮件即真实邮件样式（含完整存单明细）
    html = mailer.build_fixed_notice_html(due_today, due_tomorrow)
    prefix = (
        '<div style="font-family:-apple-system,BlinkMacSystemFont,Segoe UI,sans-serif;max-width:560px;'
        'margin:0 auto 12px;padding:10px 14px;background:#eff6ff;border:1px solid #dbeafe;border-radius:10px;'
        'color:#1d4ed8;font-size:12.5px;line-height:1.6;">这是一封测试邮件，样式与真实到期提醒完全一致。每笔存单会在'
        '<b>到期前一天</b>与<b>到期当天</b>各提醒一次。</div>'
    )
    ok, msg = mailer.send_raw_mail(subject, prefix + html, to=to or None)
    return jsonify({"ok": ok, "message": msg, "error": None if ok else msg})


# ---------------- 启动 ----------------
def main():
    db.init_db()
    setup_scheduler()
    scheduler.start()
    logger.info(f"服务启动，端口 {config.PORT}")
    app.run(host=config.HOST, port=config.PORT, debug=False, use_reloader=False)


if __name__ == "__main__":
    main()
