"""数据层：SQLite 存储产品、持仓、每日净值、收益快照、配置、用户。"""
import sqlite3
import threading
import secrets
import json
from contextlib import contextmanager
from werkzeug.security import generate_password_hash, check_password_hash
from config import DB_PATH, DEFAULT_FETCH_TIMES
import secure

_write_lock = threading.RLock()

# 需要加密存储的敏感设置项（值以密文入库，读取时透明解密）
_ENCRYPTED_SETTINGS = {"smtp_pass", "widget_token"}


def _encode_setting(key, value):
    """入库编码：敏感项加密。"""
    s = "" if value is None else str(value)
    return secure.encrypt(s) if key in _ENCRYPTED_SETTINGS else s


def _decode_setting(key, value):
    """出库解码：敏感项解密。"""
    if key in _ENCRYPTED_SETTINGS:
        return secure.decrypt(value)
    return value


def get_conn():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


@contextmanager
def transaction():
    with _write_lock:
        conn = get_conn()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()


def init_db():
    with transaction() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS settings (
            key   TEXT PRIMARY KEY,
            value TEXT
        );

        -- 登录账号：role=admin 可修改，role=member 只读
        CREATE TABLE IF NOT EXISTS users (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            username      TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            role          TEXT NOT NULL DEFAULT 'member',  -- admin / member
            display_name  TEXT DEFAULT '',
            created_at    TEXT DEFAULT (datetime('now','localtime')),
            last_login    TEXT
        );

        -- 登录历史：记录每次成功登录的"谁 / 何时 / 来自哪个 IP 与设备"
        CREATE TABLE IF NOT EXISTS login_history (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            uid          INTEGER,
            username     TEXT NOT NULL,
            display_name TEXT DEFAULT '',
            role         TEXT DEFAULT 'member',
            login_at     TEXT DEFAULT (datetime('now','localtime')),
            ip           TEXT DEFAULT '',
            user_agent   TEXT DEFAULT ''
        );
        CREATE INDEX IF NOT EXISTS idx_login_history_time ON login_history(login_at DESC);

        -- 已发现/关注的产品（产品代码唯一）
        CREATE TABLE IF NOT EXISTS products (
            product_code TEXT PRIMARY KEY,
            product_name TEXT NOT NULL,
            bank         TEXT DEFAULT 'ccb',  -- 来源银行：ccb=建行, psbc=邮储, spdb=浦发/浦银理财
            updated_at   TEXT DEFAULT (datetime('now','localtime'))
        );

        -- 用户持仓：一个产品可有多笔本金，各自独立滚动
        CREATE TABLE IF NOT EXISTS holdings (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            product_code TEXT NOT NULL,
            product_name TEXT NOT NULL,
            principal    REAL NOT NULL,          -- 初始买入金额（起算市值）
            buy_date     TEXT NOT NULL,          -- 买入/记录日期 YYYY-MM-DD
            settle_date  TEXT,                   -- 本金入账日；NULL = 已在账（历史导入持仓）
            note         TEXT DEFAULT '',
            active       INTEGER DEFAULT 1,
            -- 该笔自带的历史收益基准；NULL = 沿用按银行全局分摊的历史收益
            historical_profit REAL,
            created_at   TEXT DEFAULT (datetime('now','localtime')),
            UNIQUE(product_code, buy_date, principal)
        );

        -- 追加本金（同一产品后续又买入的钱）：T 日买入 → T+1 入账 → T+2 起息
        CREATE TABLE IF NOT EXISTS capital_additions (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            holding_id  INTEGER NOT NULL,
            amount      REAL NOT NULL,
            invest_date TEXT NOT NULL,           -- 买入/记录日期
            settle_date TEXT NOT NULL,           -- 入账日（invest_date + 1）
            note        TEXT DEFAULT '',
            created_at  TEXT DEFAULT (datetime('now','localtime'))
        );

        -- 每日净值数据（来自公告 PDF）
        CREATE TABLE IF NOT EXISTS yields (
            product_code TEXT NOT NULL,
            nav_date     TEXT NOT NULL,          -- 净值日期 YYYY-MM-DD
            annual_rate  REAL,                   -- 七日年化收益率 %
            per_10k      REAL,                   -- 每万份收益
            pub_date     TEXT,                   -- 公告发布日期
            fetched_at   TEXT DEFAULT (datetime('now','localtime')),
            PRIMARY KEY (product_code, nav_date)
        );

        -- 每日收益快照（滚动复利计算结果）
        CREATE TABLE IF NOT EXISTS profit_snapshots (
            holding_id     INTEGER NOT NULL,
            nav_date       TEXT NOT NULL,
            daily_profit   REAL NOT NULL,        -- 当日收益
            expense        REAL DEFAULT 0,       -- 当日扣减的本金支出
            injection      REAL DEFAULT 0,       -- 当日入账的追加本金
            balance        REAL NOT NULL,        -- 当日结束后的滚动本金
            cum_profit     REAL NOT NULL,        -- 累计收益
            annual_rate    REAL,                 -- 当日七日年化
            per_10k        REAL,
            PRIMARY KEY (holding_id, nav_date)
        );

        CREATE INDEX IF NOT EXISTS idx_yields_date ON yields(nav_date);
        CREATE INDEX IF NOT EXISTS idx_snap_date ON profit_snapshots(nav_date);

        -- 本金支出记录
        --   bank + scope=bank  ：该银行全部产品平均分摊（建行口径）
        --   holding_id          ：只从指定这只产品扣减（邮储/浦发口径）
        --   两者都为空          ：历史记录，按全部持仓本金占比分摊
        CREATE TABLE IF NOT EXISTS withdrawals (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            amount      REAL NOT NULL,          -- 支出金额（正数）
            expense_date TEXT NOT NULL,         -- 支出日期 YYYY-MM-DD
            bank        TEXT,                   -- ccb / psbc / spdb
            holding_id  INTEGER,                -- 指定产品（单只产品扣减）
            note        TEXT DEFAULT '',
            created_at  TEXT DEFAULT (datetime('now','localtime'))
        );

        -- 抓取任务日志：存 SQLite（多实例/多 worker 共享），避免内存态导致的跨实例读不到
        CREATE TABLE IF NOT EXISTS fetch_logs (
            run_id   TEXT PRIMARY KEY,
            status   TEXT NOT NULL DEFAULT 'running',  -- running / ok / error
            lines    TEXT NOT NULL DEFAULT '[]',       -- JSON 数组
            updated  REAL NOT NULL
        );

        -- 定期存款存单（从「记账日历」小程序移植的定期监控功能）
        -- owner_uid 关联 users.id，表示这笔存单记在哪个成员名下（复用 LGB 的用户/角色体系）
        CREATE TABLE IF NOT EXISTS fixed_deposits (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            owner_uid    INTEGER,                        -- 归属成员 users.id；NULL=未指定
            bank_id      TEXT DEFAULT '',                -- 内置银行 id，如 ccb/psbc
            bank_name    TEXT DEFAULT '',                -- 银行名称（可手动填写）
            save_date    TEXT NOT NULL,                  -- 存入/起息日期 YYYY-MM-DD
            expire_date  TEXT NOT NULL,                  -- 到期日期 YYYY-MM-DD
            principal    REAL NOT NULL,                  -- 本金（元）
            rate_percent REAL DEFAULT 0,                 -- 年利率 %
            term_months  REAL DEFAULT 0,                 -- 存期（月，可带小数）
            interest     REAL DEFAULT 0,                 -- 预估利息（单利，元）
            total_money  REAL DEFAULT 0,                 -- 本息合计（元）
            remark       TEXT DEFAULT '',                -- 备注
            created_at   TEXT DEFAULT (datetime('now','localtime')),
            updated_at   TEXT DEFAULT (datetime('now','localtime'))
        );
        CREATE INDEX IF NOT EXISTS idx_fixed_expire ON fixed_deposits(expire_date);
        CREATE INDEX IF NOT EXISTS idx_fixed_owner ON fixed_deposits(owner_uid);
        """)

        # 轻量迁移：旧库补列
        cols = {r[1] for r in c.execute("PRAGMA table_info(profit_snapshots)").fetchall()}
        if "expense" not in cols:
            c.execute("ALTER TABLE profit_snapshots ADD COLUMN expense REAL DEFAULT 0")
        if "injection" not in cols:
            c.execute("ALTER TABLE profit_snapshots ADD COLUMN injection REAL DEFAULT 0")
        pcols = {r[1] for r in c.execute("PRAGMA table_info(products)").fetchall()}
        if "bank" not in pcols:
            c.execute("ALTER TABLE products ADD COLUMN bank TEXT DEFAULT 'ccb'")
        hcols = {r[1] for r in c.execute("PRAGMA table_info(holdings)").fetchall()}
        if "settle_date" not in hcols:
            # 历史持仓本金已在账，置空表示「已在账」，不启用 T+1 入账延迟
            c.execute("ALTER TABLE holdings ADD COLUMN settle_date TEXT")
        if "historical_profit" not in hcols:
            # 单笔历史收益基准；NULL 表示沿用按银行全局分摊
            c.execute("ALTER TABLE holdings ADD COLUMN historical_profit REAL")
        c.execute("CREATE INDEX IF NOT EXISTS idx_add_holding ON capital_additions(holding_id, settle_date)")
        wcols = {r[1] for r in c.execute("PRAGMA table_info(withdrawals)").fetchall()}
        if "bank" not in wcols:
            c.execute("ALTER TABLE withdrawals ADD COLUMN bank TEXT")
        if "holding_id" not in wcols:
            c.execute("ALTER TABLE withdrawals ADD COLUMN holding_id INTEGER")

        # 默认配置
        defaults = {
            # 桌面小组件免登录接口的访问令牌（随机生成，用户可在设置页重新生成）
            "widget_token": secrets.token_urlsafe(18),
            "smtp_host": "smtp.qq.com",
            "smtp_port": "465",
            "smtp_user": "",
            "smtp_pass": "",
            "mail_from": "",
            "mail_to": "",
            "push_enabled": "1",
            "push_hour": "9",
            "push_minute": "0",
            "last_push_date": "",
            # 自动抓取时间点列表（JSON 数组，元素为 "HH:MM"，可配置多个，仅抓数据不发邮件）
            "fetch_times": json.dumps(DEFAULT_FETCH_TIMES),
            # 建行 App 历史收益基准：开始监控前已累计的收益（导入日 2026-11-18）
            # 汇总时 累计收益 = 该基准 + 监控后滚动收益；总本金 = 当前市值 - 累计收益
            "historical_profit": "37.92",
            # 邮储 App 历史收益基准（天乐宝10号，导入日 2026-11-18）
            "psbc_historical_profit": "11.78",
            # 浦发 App「灵活+」历史收益基准（3 只天添利现金宝理财宝份额，导入日 2026-11-18）
            "spdb_historical_profit": "3.18",
            "app_ref_date": "2026-11-18",
            "app_yesterday_profit": "0.9",   # 建行 App 显示的昨日收益（10 产品合计），仅参考
            "app_overall_annual": "1.24",    # 建行 App 显示的综合七日年化，仅参考
            "psbc_app_yesterday_profit": "0.93",  # 邮储 App 天乐宝10号昨日收益
            "psbc_app_overall_annual": "1.33",    # 邮储 App 天乐宝10号七日年化
            "spdb_app_yesterday_profit": "0.37",  # 浦发 App 灵活+ 昨日收益（3 产品合计）
            "spdb_app_overall_annual": "1.41",    # 浦发 App 持仓七日年化
            # 定期监控：到期提醒（每天固定时间检查次日/当日到期的存单并发邮件）
            "fixed_notice_enabled": "0",
            "fixed_notice_time": "09:00",
            "fixed_notice_email": "",             # 留空则用 mail_to
            "fixed_notice_last_date": "",         # 上次发送日期，避免同一天重复发
        }
        for k, v in defaults.items():
            c.execute("INSERT OR IGNORE INTO settings(key,value) VALUES(?,?)", (k, _encode_setting(k, v)))

        # 安全迁移：把历史版本以明文入库的敏感项（如 smtp_pass）转为密文
        for k in _ENCRYPTED_SETTINGS:
            row = c.execute("SELECT value FROM settings WHERE key=?", (k,)).fetchone()
            if row and row["value"] and not secure.is_encrypted(row["value"]):
                c.execute("UPDATE settings SET value=? WHERE key=?", (secure.encrypt(row["value"]), k))

        # 默认管理员账号：首次初始化写入（若不存在），密码用 werkzeug 哈希存储
        if c.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0:
            c.execute(
                "INSERT INTO users(username,password_hash,role,display_name) VALUES(?,?,?,?)",
                ("admin", generate_password_hash("admin123"), "admin", "管理员"),
            )

    # 收紧数据库与密钥文件权限为 600（仅属主可读写），降低同主机其他用户读取风险
    secure.harden_permissions()


# ---------- settings ----------
def get_settings():
    conn = get_conn()
    rows = conn.execute("SELECT key,value FROM settings").fetchall()
    conn.close()
    return {r["key"]: _decode_setting(r["key"], r["value"]) for r in rows}


def set_setting(key, value):
    with transaction() as c:
        c.execute("INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)", (str(key), _encode_setting(key, value)))


def set_settings(d: dict):
    with transaction() as c:
        for k, v in d.items():
            c.execute("INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)", (str(k), _encode_setting(k, v)))


def regenerate_widget_token():
    """重新生成桌面小组件访问令牌，返回新的明文 token。"""
    token = secrets.token_urlsafe(18)
    set_setting("widget_token", token)
    return token


def get_fetch_times():
    """读取自动抓取时间点列表，返回形如 ['09:00','14:00'] 的规范化列表。

    容错：坏数据、空列表都回退到 config 默认；去重、排序、过滤非法值。
    """
    import re
    raw = get_settings().get("fetch_times", "")
    times = []
    try:
        val = json.loads(raw) if raw else []
        if isinstance(val, list):
            for x in val:
                m = re.match(r"^(\d{1,2}):(\d{2})$", str(x).strip())
                if m:
                    h, mi = int(m.group(1)), int(m.group(2))
                    if 0 <= h <= 23 and 0 <= mi <= 59:
                        times.append(f"{h:02d}:{mi:02d}")
    except (ValueError, TypeError):
        pass
    if not times:
        times = list(DEFAULT_FETCH_TIMES)
    return sorted(set(times))


def normalize_fetch_times(items):
    """把前端传来的抓取时间数组规范化为 JSON 字符串；非法/空则回退默认。"""
    import re
    times = []
    if isinstance(items, list):
        for x in items:
            m = re.match(r"^(\d{1,2}):(\d{2})$", str(x).strip())
            if m:
                h, mi = int(m.group(1)), int(m.group(2))
                if 0 <= h <= 23 and 0 <= mi <= 59:
                    times.append(f"{h:02d}:{mi:02d}")
    if not times:
        times = list(DEFAULT_FETCH_TIMES)
    return json.dumps(sorted(set(times)))


# ---------- products ----------
def upsert_product(code, name, bank="ccb"):
    with transaction() as c:
        c.execute("""INSERT INTO products(product_code,product_name,bank) VALUES(?,?,?)
                     ON CONFLICT(product_code) DO UPDATE SET product_name=excluded.product_name,
                     bank=excluded.bank, updated_at=datetime('now','localtime')""", (code, name, bank))


def get_product_bank(code):
    conn = get_conn()
    r = conn.execute("SELECT bank FROM products WHERE product_code=?", (code,)).fetchone()
    conn.close()
    return r["bank"] if r and r["bank"] else None


def list_products():
    conn = get_conn()
    rows = conn.execute("SELECT * FROM products ORDER BY updated_at DESC").fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ---------- holdings ----------
def add_holding(code, name, principal, buy_date, note="", settle_date=None, historical_profit=None):
    """添加持仓，成功返回新 id，重复返回 None。

    settle_date:       本金入账日；None 表示本金已在账（历史导入）。
    historical_profit: 该笔自带历史收益基准；None 表示沿用按银行全局分摊。
    """
    with transaction() as c:
        cur = c.execute("""INSERT OR IGNORE INTO holdings
                           (product_code,product_name,principal,buy_date,settle_date,note,historical_profit)
                           VALUES(?,?,?,?,?,?,?)""",
                        (code, name, principal, buy_date, settle_date, note, historical_profit))
        if cur.rowcount > 0:
            return cur.lastrowid
        return None


def update_holding(hid, **fields):
    allowed = {"product_name", "principal", "buy_date", "settle_date", "note", "active", "historical_profit"}
    sets = [f"{k}=?" for k in fields if k in allowed]
    if not sets:
        return True
    vals = [fields[k] for k in fields if k in allowed] + [hid]
    try:
        with transaction() as c:
            c.execute(f"UPDATE holdings SET {','.join(sets)} WHERE id=?", vals)
        return True
    except sqlite3.IntegrityError:
        # 唯一约束冲突：相同产品/买入日/本金已存在
        return False


def delete_holding(hid):
    with transaction() as c:
        c.execute("DELETE FROM holdings WHERE id=?", (hid,))
        c.execute("DELETE FROM profit_snapshots WHERE holding_id=?", (hid,))
        c.execute("DELETE FROM capital_additions WHERE holding_id=?", (hid,))


# ---------- 追加本金（同一产品后续买入） ----------
def add_addition(holding_id, amount, invest_date, settle_date, note=""):
    with transaction() as c:
        cur = c.execute("""INSERT INTO capital_additions(holding_id,amount,invest_date,settle_date,note)
                           VALUES(?,?,?,?,?)""",
                        (holding_id, float(amount), invest_date, settle_date, note))
        return cur.lastrowid


def list_additions(holding_id=None):
    conn = get_conn()
    if holding_id is None:
        rows = conn.execute("SELECT * FROM capital_additions ORDER BY settle_date, id").fetchall()
    else:
        rows = conn.execute("SELECT * FROM capital_additions WHERE holding_id=? ORDER BY settle_date, id",
                            (holding_id,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def delete_addition(add_id):
    with transaction() as c:
        c.execute("DELETE FROM capital_additions WHERE id=?", (add_id,))


def list_holdings(active_only=False):
    conn = get_conn()
    sql = "SELECT * FROM holdings"
    if active_only:
        sql += " WHERE active=1"
    sql += " ORDER BY id"
    rows = conn.execute(sql).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_holding(hid):
    conn = get_conn()
    r = conn.execute("SELECT * FROM holdings WHERE id=?", (hid,)).fetchone()
    conn.close()
    return dict(r) if r else None


# ---------- withdrawals ----------
def list_withdrawals():
    conn = get_conn()
    rows = conn.execute("SELECT * FROM withdrawals ORDER BY expense_date, id").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def add_withdrawal(amount, expense_date, note="", bank=None, holding_id=None):
    """新增一笔支出，返回新 id。

    bank       : ccb/psbc/spdb —— 指定银行时按该银行产品平均分摊（建行口径）
    holding_id : 指定单只持仓 —— 只从该产品扣减（邮储/浦发口径）
    """
    with transaction() as c:
        cur = c.execute("""INSERT INTO withdrawals(amount,expense_date,bank,holding_id,note)
                           VALUES(?,?,?,?,?)""",
                        (float(amount), expense_date, bank, holding_id, note))
        return cur.lastrowid


def delete_withdrawal(wid):
    with transaction() as c:
        c.execute("DELETE FROM withdrawals WHERE id=?", (wid,))


# ---------- yields ----------
def get_last_fetch_time():
    """返回数据库中最新的净值抓取时间（yields.fetched_at），无数据时返回空串。"""
    conn = get_conn()
    try:
        row = conn.execute("SELECT MAX(fetched_at) AS t FROM yields").fetchone()
        return (row["t"] or "") if row else ""
    finally:
        conn.close()


def save_yields(product_code, records, pub_date=None):
    """records: [{nav_date, annual_rate, per_10k}]"""
    n = 0
    with transaction() as c:
        for r in records:
            cur = c.execute("""INSERT INTO yields(product_code,nav_date,annual_rate,per_10k,pub_date)
                               VALUES(?,?,?,?,?)
                               ON CONFLICT(product_code,nav_date) DO UPDATE SET
                                 annual_rate=excluded.annual_rate,
                                 per_10k=excluded.per_10k,
                                 pub_date=excluded.pub_date,
                                 fetched_at=datetime('now','localtime')""",
                            (product_code, r["nav_date"], r.get("annual_rate"), r.get("per_10k"), pub_date))
            n += cur.rowcount
    return n


def get_yields(product_code, start_date=None, end_date=None):
    conn = get_conn()
    sql = "SELECT * FROM yields WHERE product_code=?"
    args = [product_code]
    if start_date:
        sql += " AND nav_date>=?"
        args.append(start_date)
    if end_date:
        sql += " AND nav_date<=?"
        args.append(end_date)
    sql += " ORDER BY nav_date ASC"
    rows = conn.execute(sql, args).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def latest_yield(product_code):
    conn = get_conn()
    r = conn.execute("SELECT * FROM yields WHERE product_code=? ORDER BY nav_date DESC LIMIT 1",
                     (product_code,)).fetchone()
    conn.close()
    return dict(r) if r else None


# ---------- snapshots ----------
def replace_snapshots(holding_id, snaps):
    with transaction() as c:
        c.execute("DELETE FROM profit_snapshots WHERE holding_id=?", (holding_id,))
        for s in snaps:
            c.execute("""INSERT INTO profit_snapshots
                         (holding_id,nav_date,daily_profit,expense,injection,balance,cum_profit,annual_rate,per_10k)
                         VALUES(?,?,?,?,?,?,?,?,?)""",
                      (holding_id, s["nav_date"], s["daily_profit"], s.get("expense", 0),
                       s.get("injection", 0), s["balance"], s["cum_profit"],
                       s.get("annual_rate"), s.get("per_10k")))


def get_snapshots(holding_id):
    conn = get_conn()
    rows = conn.execute("SELECT * FROM profit_snapshots WHERE holding_id=? ORDER BY nav_date",
                        (holding_id,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def latest_snapshot(holding_id):
    conn = get_conn()
    r = conn.execute("SELECT * FROM profit_snapshots WHERE holding_id=? ORDER BY nav_date DESC LIMIT 1",
                     (holding_id,)).fetchone()
    conn.close()
    return dict(r) if r else None


# ---------------- 整体数据导出 / 导入 ----------------
# 备份文件包含 settings（含 SMTP 授权码）、products、holdings、
# capital_additions、withdrawals、yields、profit_snapshots 全部业务数据。
# 导入为「覆盖式恢复」：先清空这些表，再按备份逐行写回（保留主键，维持引用关系）。
BACKUP_TABLES = [
    "settings",
    "products",
    "holdings",
    "capital_additions",
    "withdrawals",
    "yields",
    "profit_snapshots",
    "fixed_deposits",
    "login_history",
    "users",
]
BACKUP_VERSION = 4


# 敏感设置项：发邮箱的备份必须剔除，避免邮箱授权码/收件人等隐私外泄
SENSITIVE_SETTING_KEYS = {
    "smtp_host", "smtp_port", "smtp_user", "smtp_pass",
    "mail_from", "mail_to", "sharecode", "secret_key", "widget_token",
}
# 导入恢复时永不覆盖的表（脱敏备份不含这些；即使含也不动现有账号）
PROTECTED_TABLES = {"users"}
# 发邮箱的脱敏备份同样剔除登录历史（含 IP / 设备，属隐私信息）
SANITIZE_EXCLUDED_TABLES = {"users", "login_history"}


def export_all(sanitize=False):
    """导出全部业务数据为可序列化 dict（含 schema 版本与导出时间）。

    sanitize=True 时生成"隐私安全"备份，专用于发送到邮箱：
      - 剔除 users 表（账号、密码哈希、昵称等用户信息）
      - 剔除 settings 中的邮箱/SMTP 等敏感项（授权码、账号、收件人）
    业务数据（持仓、净值、支出、收益快照、非敏感设置）完整保留，仍可用于恢复。
    """
    conn = get_conn()
    payload = {
        "format": "lqb-backup",
        "version": BACKUP_VERSION,
        "exported_at": __import__("datetime").datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "sanitized": bool(sanitize),
        "tables": {},
    }
    try:
        for t in BACKUP_TABLES:
            if sanitize and t in SANITIZE_EXCLUDED_TABLES:
                payload["tables"][t] = []
                continue
            rows = conn.execute(f'SELECT * FROM "{t}"').fetchall()
            data = [dict(r) for r in rows]
            if sanitize and t == "settings":
                data = [r for r in data if r.get("key") not in SENSITIVE_SETTING_KEYS]
            payload["tables"][t] = data
    finally:
        conn.close()
    return payload


def _table_columns(conn, table):
    return {r[1] for r in conn.execute(f'PRAGMA table_info("{table}")').fetchall()}


def import_all(payload, mode="replace"):
    """从备份 dict 恢复数据。mode: 'replace' 覆盖 或 'merge' 合并。

    返回统计 dict：{table: 导入行数}。失败时抛异常，事务自动回滚。
    """
    if not isinstance(payload, dict):
        raise ValueError("备份文件格式不正确")
    if payload.get("format") != "lqb-backup":
        raise ValueError("不是本系统支持的备份文件")
    ver = payload.get("version")
    if not isinstance(ver, int) or ver > BACKUP_VERSION:
        raise ValueError(f"备份版本不兼容（文件版本 {ver}，当前支持 {BACKUP_VERSION}）")
    tables = payload.get("tables")
    if not isinstance(tables, dict):
        raise ValueError("备份文件缺少数据表内容")

    # 先做完整校验，确认每张表的列名都合法，避免中途失败
    with transaction() as c:
        for t in BACKUP_TABLES:
            rows = tables.get(t, [])
            if not isinstance(rows, list):
                raise ValueError(f"表 {t} 的数据格式不正确")
            valid_cols = _table_columns(c, t)
            for row in rows:
                if not isinstance(row, dict):
                    raise ValueError(f"表 {t} 存在非法数据行")
                unknown = set(row.keys()) - valid_cols
                if unknown:
                    raise ValueError(f"表 {t} 含未知字段：{', '.join(sorted(unknown))}")

        stats = {}
        sanitized = bool(payload.get("sanitized"))
        # settings 一律用"按键合并"：只覆盖备份中出现的 key，绝不删除现有项。
        # 这样脱敏备份（不含邮箱配置）恢复后不会抹掉当前 SMTP / 账号配置。
        setting_rows = tables.get("settings", [])
        existing_keys = {r[0] for r in c.execute('SELECT key FROM settings').fetchall()}
        if mode == "replace":
            # 按依赖顺序清空（先子表后主表）；settings 与受保护表不整体清空
            for t in ("profit_snapshots", "capital_additions", "withdrawals",
                      "holdings", "yields", "products", "fixed_deposits", "login_history"):
                c.execute(f'DELETE FROM "{t}"')
            # users 永不删除：脱敏备份不含账号，必须保留现有账号以避免无法登录
            # （完整备份含 users 时也走下面的按键处理，不整体覆盖）

        for t in BACKUP_TABLES:
            rows = tables.get(t, [])
            # settings 特殊处理：逐 key 更新/插入，不删除
            if t == "settings":
                done = 0
                for r in rows:
                    k = r.get("key")
                    if not k:
                        continue
                    v = r.get("value", "")
                    # 敏感项入库前加密；备份中已是 enc: 密文时 encrypt 会原样返回，不会二次加密
                    v = _encode_setting(k, v)
                    if k in existing_keys:
                        c.execute('UPDATE settings SET value=? WHERE key=?', (v, k))
                    else:
                        c.execute('INSERT INTO settings (key, value) VALUES (?, ?)', (k, v))
                    done += 1
                stats[t] = done
                continue
            # 受保护表（users）：仅在完整备份且确实带账号时才覆盖（先清后插）
            if t in PROTECTED_TABLES:
                if not rows:
                    stats[t] = 0
                    continue
                c.execute(f'DELETE FROM "{t}"')
                cols = list(rows[0].keys())
                col_sql = ",".join(f'"{x}"' for x in cols)
                ph = ",".join("?" for _ in cols)
                c.executemany(
                    f'INSERT INTO "{t}" ({col_sql}) VALUES ({ph})',
                    [tuple(r.get(x) for x in cols) for r in rows],
                )
                stats[t] = len(rows)
                continue
            if not rows:
                stats[t] = 0
                continue
            cols = list(rows[0].keys())
            col_sql = ",".join(f'"{x}"' for x in cols)
            ph = ",".join("?" for _ in cols)
            c.executemany(
                f'INSERT INTO "{t}" ({col_sql}) VALUES ({ph})',
                [tuple(r.get(x) for x in cols) for r in rows],
            )
            stats[t] = len(rows)

        # 同步自动递增序列，避免导入后新增记录主键冲突
        for t, seq_name in (("holdings", "holdings"),
                            ("capital_additions", "capital_additions"),
                            ("withdrawals", "withdrawals"),
                            ("login_history", "login_history")):
            mx = c.execute(f'SELECT COALESCE(MAX(id),0) FROM "{t}"').fetchone()[0]
            c.execute(
                "UPDATE sqlite_sequence SET seq=? WHERE name=?",
                (mx, seq_name),
            )
        return stats


# ---------------- 用户 / 认证 ----------------
def list_users():
    """返回全部账号（不含密码哈希）。"""
    conn = get_conn()
    rows = conn.execute(
        "SELECT id, username, role, display_name, created_at, last_login FROM users ORDER BY id"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_user(user_id):
    conn = get_conn()
    r = conn.execute("SELECT id, username, role, display_name, created_at, last_login FROM users WHERE id=?",
                     (user_id,)).fetchone()
    conn.close()
    return dict(r) if r else None


def get_user_by_username(username):
    conn = get_conn()
    r = conn.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
    conn.close()
    return dict(r) if r else None


def verify_user(username, password):
    """校验账号密码，成功返回公开用户信息（不含哈希），失败返回 None。"""
    u = get_user_by_username((username or "").strip())
    if not u:
        return None
    if not check_password_hash(u["password_hash"], password or ""):
        return None
    return {k: u[k] for k in ("id", "username", "role", "display_name")}


def touch_login(user_id):
    with transaction() as c:
        c.execute("UPDATE users SET last_login=datetime('now','localtime') WHERE id=?", (user_id,))


def record_login(uid, username, display_name="", role="member", ip="", user_agent=""):
    """记录一次成功登录到 login_history。"""
    with transaction() as c:
        c.execute(
            "INSERT INTO login_history(uid,username,display_name,role,ip,user_agent,login_at) "
            "VALUES(?,?,?,?,?,?,datetime('now','localtime'))",
            (uid, username or "", display_name or "", role or "member", ip or "", user_agent or ""),
        )


def list_login_history(limit=200):
    """返回最近的登录记录（新到旧），供设置页展示。"""
    conn = get_conn()
    rows = conn.execute(
        "SELECT id,uid,username,display_name,role,login_at,ip,user_agent "
        "FROM login_history ORDER BY id DESC LIMIT ?", (int(limit),)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def clear_login_history():
    """清空登录历史（管理员可选操作）。"""
    with transaction() as c:
        c.execute("DELETE FROM login_history")


def _valid_role(role):
    return role in ("admin", "member")


def create_user(username, password, role="member", display_name=""):
    """新增账号。返回 (user_id, None) 或 (None, 错误信息)。"""
    username = (username or "").strip()
    if not username:
        return None, "用户名不能为空"
    if len(username) > 40:
        return None, "用户名不能超过 40 个字符"
    if not password or len(str(password)) < 6:
        return None, "密码至少 6 位"
    if not _valid_role(role):
        return None, "权限角色不正确"
    try:
        with transaction() as c:
            cur = c.execute(
                "INSERT INTO users(username,password_hash,role,display_name) VALUES(?,?,?,?)",
                (username, generate_password_hash(str(password)), role, (display_name or "").strip()),
            )
            return cur.lastrowid, None
    except sqlite3.IntegrityError:
        return None, "用户名已存在"


def update_user(user_id, username=None, password=None, role=None, display_name=None,
                require_admin_count=True):
    """编辑账号：用户名/密码(可选)/角色/显示名。返回 (True, None) 或 (False, 错误)。"""
    u = get_user(user_id)
    if not u:
        return False, "账号不存在"
    fields, vals = [], []
    if username is not None:
        username = (username or "").strip()
        if not username:
            return False, "用户名不能为空"
        fields.append("username=?")
        vals.append(username)
    if password:
        if len(str(password)) < 6:
            return False, "密码至少 6 位"
        fields.append("password_hash=?")
        vals.append(generate_password_hash(str(password)))
    if role is not None:
        if not _valid_role(role):
            return False, "权限角色不正确"
        # 不允许把最后一个管理员降级
        if u["role"] == "admin" and role != "admin" and require_admin_count:
            conn = get_conn()
            n = conn.execute("SELECT COUNT(*) FROM users WHERE role='admin'").fetchone()[0]
            conn.close()
            if n <= 1:
                return False, "至少要保留一个管理员账号"
        fields.append("role=?")
        vals.append(role)
    if display_name is not None:
        fields.append("display_name=?")
        vals.append((display_name or "").strip())
    if not fields:
        return True, None
    vals.append(user_id)
    try:
        with transaction() as c:
            c.execute(f"UPDATE users SET {','.join(fields)} WHERE id=?", vals)
        return True, None
    except sqlite3.IntegrityError:
        return False, "用户名已存在"


def delete_user(user_id, require_admin_count=True):
    """删除账号。禁止删除最后一个管理员。"""
    u = get_user(user_id)
    if not u:
        return False, "账号不存在"
    if u["role"] == "admin" and require_admin_count:
        conn = get_conn()
        n = conn.execute("SELECT COUNT(*) FROM users WHERE role='admin'").fetchone()[0]
        conn.close()
        if n <= 1:
            return False, "不能删除唯一的管理员账号"
    with transaction() as c:
        c.execute("DELETE FROM users WHERE id=?", (user_id,))
    return True, None


def admin_count():
    conn = get_conn()
    n = conn.execute("SELECT COUNT(*) FROM users WHERE role='admin'").fetchone()[0]
    conn.close()
    return n


# ---------------- 抓取任务日志（SQLite 共享，兼容多实例部署） ----------------
import time as _time

def fetch_log_new(run_id):
    with transaction() as c:
        c.execute(
            "INSERT OR REPLACE INTO fetch_logs(run_id, status, lines, updated) VALUES(?, 'running', '[]', ?)",
            (run_id, _time.time()),
        )


def fetch_log_append(run_id, msg, level="info", tstr=None):
    """追加一行日志；原子读改写，避免并发丢行。"""
    line = {"t": tstr or _time.strftime("%H:%M:%S"), "level": level, "msg": str(msg)}
    with transaction() as c:
        row = c.execute("SELECT lines FROM fetch_logs WHERE run_id=?", (run_id,)).fetchone()
        lines = []
        if row:
            try:
                lines = json.loads(row["lines"] or "[]")
            except Exception:
                lines = []
        lines.append(line)
        c.execute("UPDATE fetch_logs SET lines=?, updated=? WHERE run_id=?",
                  (json.dumps(lines, ensure_ascii=False), _time.time(), run_id))


def fetch_log_finish(run_id, status):
    with transaction() as c:
        c.execute("UPDATE fetch_logs SET status=?, updated=? WHERE run_id=?",
                  (status, _time.time(), run_id))
        # 清理超过 1 小时的旧日志
        c.execute("DELETE FROM fetch_logs WHERE updated < ?", (_time.time() - 3600,))


def fetch_log_get(run_id):
    conn = get_conn()
    row = conn.execute("SELECT status, lines FROM fetch_logs WHERE run_id=?", (run_id,)).fetchone()
    conn.close()
    if not row:
        return None
    try:
        lines = json.loads(row["lines"] or "[]")
    except Exception:
        lines = []
    return {"status": row["status"], "lines": lines}


def fetch_log_active(max_age=180):
    """返回当前仍在运行的抓取任务 run_id（仅认最近 max_age 秒内还在更新的），无则 None。

    页面刷新后前端内存态丢失，据此外部可判断后台是否还有任务在跑，从而恢复按钮状态。
    加时间窗口是为了排除进程被 kill 时留下的僵尸 running 记录。
    """
    conn = get_conn()
    row = conn.execute(
        "SELECT run_id, updated FROM fetch_logs WHERE status='running' ORDER BY updated DESC LIMIT 1"
    ).fetchone()
    conn.close()
    if not row:
        return None
    if _time.time() - row["updated"] > max_age:
        return None
    return row["run_id"]


# ---------- 定期存款存单（fixed_deposits）----------
# 从「记账日历」小程序移植的定期监控功能。归属成员 owner_uid 关联 users.id，
# 复用 LGB 现有的用户/角色体系，不再单独维护邀请码账本与成员表。

def _deposit_row(row):
    if not row:
        return None
    return {
        "id": row["id"],
        "owner_uid": row["owner_uid"],
        "bank_id": row["bank_id"],
        "bank_name": row["bank_name"],
        "save_date": row["save_date"],
        "expire_date": row["expire_date"],
        "principal": row["principal"],
        "rate_percent": row["rate_percent"],
        "term_months": row["term_months"],
        "interest": row["interest"],
        "total_money": row["total_money"],
        "remark": row["remark"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def list_fixed_deposits(owner_uid=None):
    """列出全部存单，按到期日升序；可按归属成员过滤。"""
    conn = get_conn()
    if owner_uid is None:
        rows = conn.execute(
            "SELECT d.*, u.display_name, u.username FROM fixed_deposits d "
            "LEFT JOIN users u ON u.id=d.owner_uid ORDER BY d.expire_date ASC, d.id DESC"
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT d.*, u.display_name, u.username FROM fixed_deposits d "
            "LEFT JOIN users u ON u.id=d.owner_uid WHERE d.owner_uid=? "
            "ORDER BY d.expire_date ASC, d.id DESC", (owner_uid,)
        ).fetchall()
    conn.close()
    out = []
    for r in rows:
        item = _deposit_row(r)
        item["owner_name"] = r["display_name"] or r["username"] or ""
        out.append(item)
    return out


def get_fixed_deposit(dep_id):
    conn = get_conn()
    row = conn.execute("SELECT * FROM fixed_deposits WHERE id=?", (dep_id,)).fetchone()
    conn.close()
    return _deposit_row(row)


def add_fixed_deposit(data):
    with transaction() as c:
        cur = c.execute(
            "INSERT INTO fixed_deposits(owner_uid,bank_id,bank_name,save_date,expire_date,"
            "principal,rate_percent,term_months,interest,total_money,remark) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (data.get("owner_uid"), data.get("bank_id", ""), data.get("bank_name", ""),
             data["save_date"], data["expire_date"], data["principal"],
             data.get("rate_percent", 0), data.get("term_months", 0),
             data.get("interest", 0), data.get("total_money", 0), data.get("remark", "")),
        )
        return cur.lastrowid


def update_fixed_deposit(dep_id, data):
    allowed = ["owner_uid", "bank_id", "bank_name", "save_date", "expire_date",
               "principal", "rate_percent", "term_months", "interest", "total_money", "remark"]
    fields, values = [], []
    for k in allowed:
        if k in data:
            fields.append(f"{k}=?")
            values.append(data[k])
    if not fields:
        return 0
    fields.append("updated_at=datetime('now','localtime')")
    values.append(dep_id)
    with transaction() as c:
        cur = c.execute(f"UPDATE fixed_deposits SET {','.join(fields)} WHERE id=?", values)
        return cur.rowcount


def update_fixed_deposits_owner(ids, owner_uid):
    """批量修改存单归属成员，返回实际更新条数。owner_uid 可为 None（清除归属）。"""
    ids = [int(i) for i in ids if str(i).isdigit()]
    if not ids:
        return 0
    marks = ",".join("?" * len(ids))
    with transaction() as c:
        cur = c.execute(
            f"UPDATE fixed_deposits SET owner_uid=?, updated_at=datetime('now','localtime') WHERE id IN ({marks})",
            [owner_uid] + ids,
        )
        return cur.rowcount


def delete_fixed_deposit(dep_id):
    with transaction() as c:
        cur = c.execute("DELETE FROM fixed_deposits WHERE id=?", (dep_id,))
        return cur.rowcount


def delete_fixed_deposits(ids):
    ids = [int(i) for i in ids if str(i).isdigit()]
    if not ids:
        return 0
    marks = ",".join("?" * len(ids))
    with transaction() as c:
        cur = c.execute(f"DELETE FROM fixed_deposits WHERE id IN ({marks})", ids)
        return cur.rowcount


def add_fixed_deposits_bulk(rows):
    """批量新增存单（导入用）。rows 为 dict 列表，返回新增条数。"""
    if not rows:
        return 0
    with transaction() as c:
        for data in rows:
            c.execute(
                "INSERT INTO fixed_deposits(owner_uid,bank_id,bank_name,save_date,expire_date,"
                "principal,rate_percent,term_months,interest,total_money,remark) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (data.get("owner_uid"), data.get("bank_id", ""), data.get("bank_name", ""),
                 data["save_date"], data["expire_date"], data["principal"],
                 data.get("rate_percent", 0), data.get("term_months", 0),
                 data.get("interest", 0), data.get("total_money", 0), data.get("remark", "")),
            )
        return len(rows)


def clear_fixed_deposits():
    """清空全部存单（导入覆盖模式用），返回删除条数。"""
    with transaction() as c:
        cur = c.execute("DELETE FROM fixed_deposits")
        return cur.rowcount


def fixed_deposits_expiring_on(dates):
    """查出到期日在给定日期集合内的存单（用于到期提醒）。dates: ['YYYY-MM-DD', ...]"""
    if not dates:
        return []
    marks = ",".join("?" * len(dates))
    conn = get_conn()
    rows = conn.execute(
        f"SELECT d.*, u.display_name, u.username FROM fixed_deposits d "
        f"LEFT JOIN users u ON u.id=d.owner_uid WHERE d.expire_date IN ({marks}) "
        f"ORDER BY d.expire_date ASC", list(dates)
    ).fetchall()
    conn.close()
    out = []
    for r in rows:
        item = _deposit_row(r)
        item["owner_name"] = r["display_name"] or r["username"] or ""
        out.append(item)
    return out
