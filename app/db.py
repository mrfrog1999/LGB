"""SQLite 存储层：监控产品、收益率历史、设置、日志。"""
import sqlite3
import threading
from datetime import datetime
from typing import Iterable

from .config import settings

_lock = threading.RLock()
_conn: sqlite3.Connection | None = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS products (
    code        TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    principal   REAL NOT NULL DEFAULT 0,
    start_date  TEXT,
    subscribed  INTEGER NOT NULL DEFAULT 1,
    sort        INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS rates (
    code    TEXT NOT NULL,
    date    TEXT NOT NULL,
    rate    REAL NOT NULL,
    wanfen  REAL,
    PRIMARY KEY (code, date)
);
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS logs (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    ts      TEXT NOT NULL,
    level   TEXT NOT NULL,
    message TEXT NOT NULL
);
"""


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def connect() -> sqlite3.Connection:
    global _conn
    with _lock:
        if _conn is None:
            settings.ensure_dirs()
            _conn = sqlite3.connect(settings.DB_PATH, check_same_thread=False, timeout=10)
            _conn.row_factory = sqlite3.Row
            _conn.execute("PRAGMA journal_mode=WAL")
            _conn.executescript(SCHEMA)
            _conn.commit()
        return _conn


# ---------------- 产品 ----------------

def list_products(only_subscribed: bool = True) -> list[dict]:
    with _lock:
        sql = "SELECT * FROM products"
        if only_subscribed:
            sql += " WHERE subscribed=1"
        sql += " ORDER BY sort ASC, name ASC"
        return [dict(r) for r in connect().execute(sql).fetchall()]


def get_product(code: str) -> dict | None:
    with _lock:
        row = connect().execute("SELECT * FROM products WHERE code=?", (code,)).fetchone()
        return dict(row) if row else None


def upsert_product(code: str, name: str, principal: float | None = None,
                   start_date: str | None = None, subscribed: int = 1) -> dict:
    with _lock:
        conn = connect()
        ts = _now()
        exists = get_product(code)
        if exists:
            conn.execute(
                "UPDATE products SET name=?, principal=COALESCE(?, principal), "
                "start_date=COALESCE(?, start_date), subscribed=?, updated_at=? WHERE code=?",
                (name or exists["name"], principal, start_date, subscribed, ts, code),
            )
        else:
            order = conn.execute("SELECT COALESCE(MAX(sort),0) FROM products").fetchone()[0] + 1
            conn.execute(
                "INSERT INTO products(code,name,principal,start_date,subscribed,sort,created_at,updated_at)"
                " VALUES(?,?,?,?,?,?,?,?)",
                (code, name, principal or 0.0, start_date, subscribed, order, ts, ts),
            )
        conn.commit()
        return get_product(code)


def update_product(code: str, **fields) -> dict | None:
    allowed = {"principal", "start_date", "subscribed", "sort", "name"}
    data = {k: v for k, v in fields.items() if k in allowed and v is not None}
    if not data:
        return get_product(code)
    with _lock:
        conn = connect()
        sets = ", ".join(f"{k}=?" for k in data)
        conn.execute(f"UPDATE products SET {sets}, updated_at=? WHERE code=?",
                     (*data.values(), _now(), code))
        conn.commit()
        return get_product(code)


def remove_product(code: str) -> None:
    with _lock:
        conn = connect()
        conn.execute("DELETE FROM products WHERE code=?", (code,))
        conn.execute("DELETE FROM rates WHERE code=?", (code,))
        conn.commit()


# ---------------- 收益率 ----------------

def upsert_rates(code: str, rows: Iterable[dict]) -> int:
    n = 0
    with _lock:
        conn = connect()
        for r in rows:
            conn.execute(
                "INSERT INTO rates(code,date,rate,wanfen) VALUES(?,?,?,?) "
                "ON CONFLICT(code,date) DO UPDATE SET rate=excluded.rate, wanfen=excluded.wanfen",
                (code, r["date"], float(r["rate"]), r.get("wanfen")),
            )
            n += 1
        conn.commit()
    return n


def rates_of(code: str, limit: int | None = None) -> list[dict]:
    with _lock:
        sql = "SELECT date, rate, wanfen FROM rates WHERE code=? ORDER BY date ASC"
        cur = connect().execute(sql, (code,))
        rows = [dict(r) for r in cur.fetchall()]
        return rows[-limit:] if limit else rows


def latest_rate(code: str) -> dict | None:
    with _lock:
        row = connect().execute(
            "SELECT date, rate, wanfen FROM rates WHERE code=? ORDER BY date DESC LIMIT 1", (code,)
        ).fetchone()
        return dict(row) if row else None


def rate_count(code: str) -> int:
    with _lock:
        return connect().execute("SELECT COUNT(*) FROM rates WHERE code=?", (code,)).fetchone()[0]


# ---------------- settings / logs ----------------

def get_setting(key: str, default: str | None = None) -> str | None:
    with _lock:
        row = connect().execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default


def set_setting(key: str, value: str) -> None:
    with _lock:
        conn = connect()
        conn.execute("INSERT INTO settings(key,value) VALUES(?,?) "
                     "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, str(value)))
        conn.commit()


def add_log(level: str, message: str) -> None:
    with _lock:
        conn = connect()
        conn.execute("INSERT INTO logs(ts,level,message) VALUES(?,?,?)", (_now(), level, message[:2000]))
        conn.execute("DELETE FROM logs WHERE id <= (SELECT MAX(id) - ? FROM logs)", (settings.LOG_ROWS,))
        conn.commit()


def recent_logs(limit: int = 30) -> list[dict]:
    with _lock:
        cur = connect().execute("SELECT id, ts, level, message FROM logs ORDER BY id DESC LIMIT ?", (limit,))
        return [dict(r) for r in cur.fetchall()]
