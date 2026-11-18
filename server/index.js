// 理财日历 Web 版 —— Express 入口
// 页面：日历 / 台账 / 统计 / 我的（单页应用，静态文件在 ../web）
const path = require('path')
const express = require('express')
const { db, members, getSetting, setSetting, allSettings } = require('./db')
const auth = require('./auth')
const calc = require('./calc')
const banks = require('./banks')
const notify = require('./notify')

const app = express()
const PORT = Number(process.env.PORT || 8080)

app.set('trust proxy', 1)
app.use(express.json({ limit: '8mb' }))

// ---------- 身份校验 ----------
function attachUser(req, res, next) {
  const uid = auth.readSession(req.headers.cookie)
  if (uid) {
    const row = db
      .prepare('SELECT id, username, display_name, is_owner FROM users WHERE id = ?')
      .get(uid)
    if (row) {
      req.user = {
        id: row.id,
        username: row.username,
        displayName: row.display_name,
        isOwner: !!row.is_owner
      }
    }
  }
  next()
}

function requireLogin(req, res, next) {
  if (!req.user) return res.status(401).json({ ok: false, message: '请先登录' })
  next()
}

app.use(attachUser)

// ---------- 登录 / 注册 ----------
app.post('/api/auth/login', (req, res) => {
  const { username, password } = req.body || {}
  if (!username || !password) return res.status(400).json({ ok: false, message: '请填写账号和密码' })
  const row = db.prepare('SELECT * FROM users WHERE username = ?').get(String(username).trim())
  if (!row || !auth.verifyPassword(password, row.password_hash)) {
    return res.status(400).json({ ok: false, message: '账号或密码不正确' })
  }
  const sess = auth.createSession(row.id)
  auth.setCookie(res, sess.value, sess.maxAge)
  res.json({ ok: true, user: { id: row.id, username: row.username, displayName: row.display_name } })
})

// 成员注册默认关闭（ALLOW_REGISTER=1 才放行），避免陌生人注册后看到整份账本
const ALLOW_REGISTER = String(process.env.ALLOW_REGISTER || '0') === '1'

app.post('/api/auth/register', (req, res) => {
  if (!ALLOW_REGISTER) {
    return res.status(403).json({ ok: false, message: '已关闭成员注册' })
  }
  const { username, password, displayName } = req.body || {}
  const u = String(username || '').trim()
  const p = String(password || '')
  const name = String(displayName || u).trim().slice(0, 20)
  if (!/^[a-zA-Z0-9_-]{3,24}$/.test(u)) return res.status(400).json({ ok: false, message: '账号需 3-24 位字母/数字/下划线' })
  if (p.length < 6) return res.status(400).json({ ok: false, message: '密码至少 6 位' })
  const exists = db.prepare('SELECT id FROM users WHERE username = ?').get(u)
  if (exists) return res.status(400).json({ ok: false, message: '该账号已存在' })
  const isOwner = db.prepare('SELECT COUNT(*) AS n FROM users').get().n === 0 ? 1 : 0
  const info = db
    .prepare('INSERT INTO users (username, password_hash, display_name, is_owner, created_at) VALUES (?, ?, ?, ?, ?)')
    .run(u, auth.hashPassword(p), name || u, isOwner, new Date().toISOString())
  const sess = auth.createSession(info.lastInsertRowid)
  auth.setCookie(res, sess.value, sess.maxAge)
  res.json({ ok: true, user: { id: info.lastInsertRowid, username: u, displayName: name || u } })
})

// 创建者新增成员（页面「我的 → 成员 → 添加成员」调用）
app.post('/api/members', requireLogin, (req, res) => {
  if (!req.user.isOwner) return res.status(403).json({ ok: false, message: '只有创建者可以添加成员' })
  const { username, password, displayName } = req.body || {}
  const u = String(username || '').trim()
  const p = String(password || '')
  const name = String(displayName || u).trim().slice(0, 20)
  if (!/^[a-zA-Z0-9_-]{3,24}$/.test(u)) return res.status(400).json({ ok: false, message: '账号需 3-24 位字母/数字/下划线' })
  if (p.length < 6) return res.status(400).json({ ok: false, message: '密码至少 6 位' })
  if (db.prepare('SELECT id FROM users WHERE username = ?').get(u)) {
    return res.status(400).json({ ok: false, message: '该账号已存在' })
  }
  const info = db
    .prepare('INSERT INTO users (username, password_hash, display_name, is_owner, created_at) VALUES (?, ?, ?, 0, ?)')
    .run(u, auth.hashPassword(p), name || u, new Date().toISOString())
  res.json({ ok: true, id: info.lastInsertRowid, displayName: name || u })
})

app.post('/api/auth/logout', (req, res) => {
  auth.clearCookie(res)
  res.json({ ok: true })
})

app.get('/api/me', requireLogin, (req, res) => {
  res.json({
    ok: true,
    user: req.user,
    members: members(),
    settings: allSettings()
  })
})

// ---------- 银行库 ----------
app.get('/api/banks', requireLogin, (req, res) => {
  res.json({ ok: true, banks: banks.BANKS })
})

// ---------- 存单 ----------
function normalizeDeposit(body, creatorId) {
  const bankName = String(body.bankName || '').trim()
  if (!bankName) throw new Error('请选择或填写银行')
  const saveDate = String(body.saveDate || '').slice(0, 10)
  const expireDate = String(body.expireDate || '').slice(0, 10)
  if (!/^\d{4}-\d{2}-\d{2}$/.test(saveDate) || !/^\d{4}-\d{2}-\d{2}$/.test(expireDate)) throw new Error('日期格式不正确')
  if (expireDate <= saveDate) throw new Error('到期日必须晚于存入日')
  const principal = Math.round((Number(body.principal) || 0) * 100) / 100
  if (!(principal > 0)) throw new Error('本金必须大于 0')
  const rate = Number(body.ratePercent)
  if (!isFinite(rate) || rate < 0 || rate > 30) throw new Error('年利率应在 0%-30% 之间')
  const months = calc.termMonths(saveDate, expireDate)
  const { interest, total } = calc.calcInterest(principal, rate, months)
  const bank = banks.findByName(bankName)
  return {
    bankId: (bank && bank.id) || String(body.bankId || ''),
    bankName: bank ? bank.name : bankName,
    saveDate,
    expireDate,
    principal,
    ratePercent: rate,
    termMonths: calc.round2(months),
    interest,
    totalMoney: total,
    remark: String(body.remark || '').trim().slice(0, 120),
    creatorId: creatorId || null
  }
}

app.get('/api/deposits', requireLogin, (req, res) => {
  const rows = db.prepare('SELECT * FROM deposits ORDER BY expire_date ASC, id ASC').all()
  const ms = members()
  const list = rows.map(r => Object.assign(calc.decorate(r, ms), banks.decorateBank(r)))
  res.json({ ok: true, list, total: list.length })
})

app.post('/api/deposits', requireLogin, (req, res) => {
  try {
    const data = normalizeDeposit(req.body || {}, req.user.id)
    const now = new Date().toISOString()
    const info = db
      .prepare(
        `INSERT INTO deposits (bank_id, bank_name, save_date, expire_date, principal, rate_percent, term_months, interest, total_money, remark, creator_id, created_at, updated_at)
         VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`
      )
      .run(
        data.bankId, data.bankName, data.saveDate, data.expireDate, data.principal, data.ratePercent,
        data.termMonths, data.interest, data.totalMoney, data.remark, data.creatorId, now, now
      )
    res.json({ ok: true, id: info.lastInsertRowid })
  } catch (e) {
    res.status(400).json({ ok: false, message: e.message })
  }
})

app.put('/api/deposits/:id', requireLogin, (req, res) => {
  const id = Number(req.params.id)
  const row = db.prepare('SELECT * FROM deposits WHERE id = ?').get(id)
  if (!row) return res.status(404).json({ ok: false, message: '这笔存单不存在' })
  try {
    // 归属成员：允许改，但必须是已注册成员
    let creatorId = row.creator_id
    if (req.body && req.body.creatorId !== undefined) {
      const target = Number(req.body.creatorId)
      const exists = db.prepare('SELECT id FROM users WHERE id = ?').get(target)
      if (!exists) throw new Error('该成员不存在')
      creatorId = target
    }
    const data = normalizeDeposit(Object.assign({}, req.body, { creatorId }), creatorId)
    db.prepare(
      `UPDATE deposits SET bank_id=?, bank_name=?, save_date=?, expire_date=?, principal=?, rate_percent=?,
       term_months=?, interest=?, total_money=?, remark=?, creator_id=?, updated_at=? WHERE id=?`
    ).run(
      data.bankId, data.bankName, data.saveDate, data.expireDate, data.principal, data.ratePercent,
      data.termMonths, data.interest, data.totalMoney, data.remark, creatorId, new Date().toISOString(), id
    )
    res.json({ ok: true })
  } catch (e) {
    res.status(400).json({ ok: false, message: e.message })
  }
})

app.delete('/api/deposits/:id', requireLogin, (req, res) => {
  const id = Number(req.params.id)
  const row = db.prepare('SELECT id FROM deposits WHERE id = ?').get(id)
  if (!row) return res.status(404).json({ ok: false, message: '这笔存单不存在' })
  db.prepare('DELETE FROM deposits WHERE id = ?').run(id)
  res.json({ ok: true })
})

// 批量修改添加人
app.post('/api/deposits/batch-creator', requireLogin, (req, res) => {
  const ids = Array.isArray((req.body || {}).ids) ? req.body.ids.map(Number).filter(n => Number.isFinite(n)) : []
  const creatorId = Number((req.body || {}).creatorId)
  if (!ids.length) return res.status(400).json({ ok: false, message: '请先选择存单' })
  if (ids.length > 300) return res.status(400).json({ ok: false, message: '一次最多修改 300 笔' })
  const exists = db.prepare('SELECT id FROM users WHERE id = ?').get(creatorId)
  if (!exists) return res.status(400).json({ ok: false, message: '该成员不存在' })
  const stmt = db.prepare('UPDATE deposits SET creator_id = ?, updated_at = ? WHERE id = ?')
  const now = new Date().toISOString()
  const tx = db.transaction(list => list.forEach(id => stmt.run(creatorId, now, id)))
  tx(ids)
  res.json({ ok: true, updated: ids.length })
})

// ---------- 统计 ----------
app.get('/api/stats', requireLogin, (req, res) => {
  const mode = req.query.mode === 'expire' ? 'expire' : 'save'
  const year = String(req.query.year || '')
  const month = String(req.query.month || '')
  const day = String(req.query.day || '')
  const column = mode === 'expire' ? 'expire_date' : 'save_date'
  const rows = db
    .prepare('SELECT * FROM deposits WHERE ' + column + ' LIKE ? ORDER BY ' + column + ' ASC')
    .all(year ? year + '%' : '%')
  const ms = members()
  const list = rows
    .map(r => Object.assign(calc.decorate(r, ms), banks.decorateBank(r)))
    .filter(item => {
      const d = mode === 'expire' ? item.expireDate : item.saveDate
      if (month && d.slice(5, 7) !== month) return false
      if (day && d.slice(8, 10) !== day) return false
      return true
    })
  const summary = {
    count: list.length,
    principal: calc.round2(list.reduce((s, i) => s + i.principal, 0)),
    interest: calc.round2(list.reduce((s, i) => s + i.interest, 0)),
    total: calc.round2(list.reduce((s, i) => s + i.totalMoney, 0)),
    principalText: calc.money(list.reduce((s, i) => s + i.principal, 0)),
    interestText: calc.money(list.reduce((s, i) => s + i.interest, 0)),
    totalText: calc.money(list.reduce((s, i) => s + i.totalMoney, 0))
  }
  res.json({ ok: true, mode, list, summary })
})

// ---------- 设置 / 成员 ----------
app.get('/api/settings', requireLogin, (req, res) => {
  res.json({ ok: true, settings: allSettings() })
})

app.put('/api/settings', requireLogin, (req, res) => {
  const allowed = ['ledger_name', 'notice_time', 'remind_enabled', 'remind_prev_day', 'remind_same_day', 'webhook_url', 'webhook_kind']
  const body = req.body || {}
  if (body.notice_time !== undefined && !/^([01]\d|2[0-3]):[0-5]\d$/.test(String(body.notice_time))) {
    return res.status(400).json({ ok: false, message: '提醒时间格式应为 HH:mm' })
  }
  allowed.forEach(k => {
    if (body[k] !== undefined) setSetting(k, body[k])
  })
  res.json({ ok: true, settings: allSettings() })
})

app.put('/api/members/:id', requireLogin, (req, res) => {
  const id = Number(req.params.id)
  const target = db.prepare('SELECT * FROM users WHERE id = ?').get(id)
  if (!target) return res.status(404).json({ ok: false, message: '成员不存在' })
  const body = req.body || {}
  // 只有本人可以改自己；创建者可以改所有人
  if (!req.user.isOwner && req.user.id !== id) {
    return res.status(403).json({ ok: false, message: '只有账本创建者可以修改其他成员' })
  }
  const name = body.displayName !== undefined ? String(body.displayName).trim().slice(0, 20) : target.display_name
  if (!name) return res.status(400).json({ ok: false, message: '名称不能为空' })
  if (body.password !== undefined) {
    if (String(body.password).length < 6) return res.status(400).json({ ok: false, message: '密码至少 6 位' })
    db.prepare('UPDATE users SET display_name = ?, password_hash = ? WHERE id = ?')
      .run(name, auth.hashPassword(body.password), id)
  } else {
    db.prepare('UPDATE users SET display_name = ? WHERE id = ?').run(name, id)
  }
  res.json({ ok: true, members: members() })
})

// ---------- 备份 / 导入 ----------
app.get('/api/export', requireLogin, (req, res) => {
  const rows = db.prepare('SELECT * FROM deposits ORDER BY save_date ASC').all()
  const items = rows.map(r => ({
    bankName: r.bank_name,
    saveDate: r.save_date,
    expireDate: r.expire_date,
    principal: Number(r.principal),
    ratePercent: Number(r.rate_percent),
    termMonths: Number(r.term_months),
    interest: Number(r.interest),
    totalMoney: Number(r.total_money),
    remark: r.remark || ''
  }))
  res.setHeader('Content-Disposition', 'attachment; filename="finance-calendar-backup.json"')
  res.setHeader('Content-Type', 'application/json; charset=utf-8')
  res.send(JSON.stringify({ app: 'finance-calendar', version: 1, exportAt: new Date().toISOString(), items }, null, 2))
})

app.post('/api/import', requireLogin, (req, res) => {
  const body = req.body || {}
  const items = Array.isArray(body.items) ? body.items : Array.isArray(body) ? body : null
  if (!items) return res.status(400).json({ ok: false, message: 'JSON 格式不正确，需要 { items: [...] }' })
  const replace = body.replace === true
  const now = new Date().toISOString()
  const insert = db.prepare(
    `INSERT INTO deposits (bank_id, bank_name, save_date, expire_date, principal, rate_percent, term_months, interest, total_money, remark, creator_id, created_at, updated_at)
     VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`
  )
  let added = 0
  const failed = []
  try {
    const tx = db.transaction(() => {
      if (replace) db.prepare('DELETE FROM deposits').run()
      items.forEach((raw, i) => {
        try {
          const data = normalizeDeposit({
            bankName: raw.bankName || raw.bank_name,
            saveDate: raw.saveDate || raw.save_date,
            expireDate: raw.expireDate || raw.expire_date,
            principal: raw.principal,
            ratePercent: raw.ratePercent !== undefined ? raw.ratePercent : raw.rate_percent,
            remark: raw.remark
          }, req.user.id)
          insert.run(
            data.bankId, data.bankName, data.saveDate, data.expireDate, data.principal, data.ratePercent,
            data.termMonths, data.interest, data.totalMoney, data.remark, data.creatorId, now, now
          )
          added++
        } catch (e) {
          failed.push({ index: i, message: e.message })
        }
      })
    })
    tx()
  } catch (e) {
    return res.status(400).json({ ok: false, message: '导入失败：' + e.message })
  }
  res.json({ ok: true, added, replaced: replace, failed })
})

// ---------- 提醒 ----------
app.post('/api/notify/test', requireLogin, async (req, res) => {
  try {
    const r = await notify.runCheck({ force: true, test: true })
    res.json(Object.assign({ ok: true }, r))
  } catch (e) {
    res.status(500).json({ ok: false, message: e.message })
  }
})

app.get('/api/notifications', requireLogin, (req, res) => {
  const rows = db
    .prepare('SELECT * FROM inbox ORDER BY id DESC LIMIT 40')
    .all()
    .map(r => ({
      id: r.id,
      createdAt: r.created_at,
      date: r.send_date,
      kind: r.kind,
      count: r.count,
      amount: r.amount,
      amountText: calc.money(r.amount),
      channels: r.channels,
      status: r.status,
      detail: r.detail
    }))
  res.json({ ok: true, list: rows })
})

// ---------- 健康检查 & 静态资源 ----------
app.get('/api/health', (req, res) => {
  res.json({ ok: true, db: db.open, version: process.env.APP_VERSION || '1.0.0' })
})

const WEB = path.join(__dirname, '..', 'web')
app.use(express.static(WEB, { maxAge: '1h', index: 'index.html' }))
app.get('*', (req, res) => {
  if (req.path.startsWith('/api/')) return res.status(404).json({ ok: false, message: '接口不存在' })
  res.sendFile(path.join(WEB, 'index.html'))
})

// 统一错误处理
app.use((err, req, res, next) => {
  console.error('[server error]', err)
  res.status(500).json({ ok: false, message: (err && err.message) || '服务异常' })
})

app.listen(PORT, '0.0.0.0', () => {
  console.log('[理财日历] 服务已启动：http://0.0.0.0:' + PORT)
  notify.start()
})
