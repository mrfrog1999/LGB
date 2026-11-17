// SQLite 数据层：单文件数据库，随容器 volume 持久化
const fs = require('fs')
const path = require('path')
const Database = require('better-sqlite3')
const auth = require('./auth')

const DB_PATH = process.env.DB_PATH || path.join(__dirname, '..', 'data', 'finance.db')
fs.mkdirSync(path.dirname(DB_PATH), { recursive: true })

const db = new Database(DB_PATH)
db.pragma('journal_mode = WAL')

db.exec(`
CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  username TEXT UNIQUE NOT NULL,
  password_hash TEXT NOT NULL,
  display_name TEXT NOT NULL,
  is_owner INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS deposits (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  bank_id TEXT NOT NULL DEFAULT '',
  bank_name TEXT NOT NULL,
  save_date TEXT NOT NULL,
  expire_date TEXT NOT NULL,
  principal REAL NOT NULL DEFAULT 0,
  rate_percent REAL NOT NULL DEFAULT 0,
  term_months REAL NOT NULL DEFAULT 0,
  interest REAL NOT NULL DEFAULT 0,
  total_money REAL NOT NULL DEFAULT 0,
  remark TEXT NOT NULL DEFAULT '',
  creator_id INTEGER,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_deposits_save ON deposits(save_date);
CREATE INDEX IF NOT EXISTS idx_deposits_expire ON deposits(expire_date);

CREATE TABLE IF NOT EXISTS settings (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS notice_log (
  key TEXT PRIMARY KEY,
  sent_on TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS inbox (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  created_at TEXT NOT NULL,
  send_date TEXT NOT NULL,
  kind TEXT NOT NULL,
  count INTEGER NOT NULL DEFAULT 0,
  amount REAL NOT NULL DEFAULT 0,
  channels TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'ok',
  detail TEXT NOT NULL DEFAULT ''
);
`)

// ---------- 默认设置 ----------
const DEFAULT_SETTINGS = {
  ledger_name: '我的账本',
  notice_time: '09:00',
  remind_enabled: '1',
  remind_prev_day: '1',
  remind_same_day: '1',
  webhook_url: '',
  webhook_kind: 'generic' // generic | wecom | bark | serverchan
}

const insertSetting = db.prepare('INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)')
Object.keys(DEFAULT_SETTINGS).forEach(k => insertSetting.run(k, DEFAULT_SETTINGS[k]))

// ---------- 首次启动创建初始账号 ----------
const userCount = db.prepare('SELECT COUNT(*) AS n FROM users').get().n
if (userCount === 0) {
  const username = process.env.BOOTSTRAP_USER || 'admin'
  const password = process.env.BOOTSTRAP_PASSWORD || 'admin12345'
  const displayName = process.env.BOOTSTRAP_NAME || '管理员'
  db.prepare(
    'INSERT INTO users (username, password_hash, display_name, is_owner, created_at) VALUES (?, ?, ?, 1, ?)'
  ).run(username, auth.hashPassword(password), displayName, new Date().toISOString())
  console.log('[init] 已创建初始账号 ' + username + '（请登录后修改密码）')
}

function getSetting(key, fallback = '') {
  const row = db.prepare('SELECT value FROM settings WHERE key = ?').get(key)
  return row ? row.value : fallback
}

function setSetting(key, value) {
  db.prepare(
    'INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value'
  ).run(key, String(value == null ? '' : value))
}

function allSettings() {
  const rows = db.prepare('SELECT key, value FROM settings').all()
  const out = {}
  rows.forEach(r => (out[r.key] = r.value))
  return out
}

function members() {
  return db
    .prepare('SELECT id, username, display_name, is_owner, created_at FROM users ORDER BY id')
    .all()
    .map(u => ({
      id: u.id,
      username: u.username,
      displayName: u.display_name,
      isOwner: !!u.is_owner,
      initial: (u.display_name || '?').slice(0, 1),
      joinedAt: (u.created_at || '').slice(0, 10)
    }))
}

module.exports = { db, DB_PATH, getSetting, setSetting, allSettings, members, DEFAULT_SETTINGS }
