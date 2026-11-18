#!/usr/bin/env node
// 新增成员（注册入口已关闭时的备用方式）
// 用法：node server/adduser.js <账号> <称呼> <密码>
//   docker compose exec finance-calendar node server/adduser.js zhangsan 张三 123456

const path = require('path')
const { db } = require('./db')
const auth = require('./auth')

const [username, displayName, password] = process.argv.slice(2)

if (!username || !password) {
  console.error('用法: node server/adduser.js <账号> <称呼> <密码>')
  console.error('例如: node server/adduser.js zhangsan 张三 123456')
  process.exit(1)
}

const u = String(username).trim()
const p = String(password)
const name = String(displayName || u).trim().slice(0, 20)

if (!/^[a-zA-Z0-9_-]{3,24}$/.test(u)) {
  console.error('账号需 3-24 位字母/数字/下划线')
  process.exit(1)
}
if (p.length < 6) {
  console.error('密码至少 6 位')
  process.exit(1)
}
if (db.prepare('SELECT id FROM users WHERE username = ?').get(u)) {
  console.error('账号已存在：' + u)
  process.exit(1)
}

const info = db
  .prepare('INSERT INTO users (username, password_hash, display_name, is_owner, created_at) VALUES (?, ?, ?, 0, ?)')
  .run(u, auth.hashPassword(p), name || u, new Date().toISOString())

console.log('已创建成员 #' + info.lastInsertRowid + '：' + u + '（' + (name || u) + '）')
console.log('数据库：' + path.resolve(process.env.DB_PATH || '/data/finance.db'))
