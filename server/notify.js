// 到期提醒：每天在设定时间检查「明日到期 / 当日到期」，按到期日汇总成一条消息
// 通知渠道：Webhook（通用 / 企业微信机器人 / Bark / Server酱）+ 可选 SMTP 邮件
const cron = require('node-cron')
const nodemailer = require('nodemailer')
const { db, allSettings } = require('./db')
const calc = require('./calc')

const TZ = process.env.TZ || 'Asia/Shanghai'

function pad(n) {
  return n < 10 ? '0' + n : '' + n
}

function localDate(d = new Date()) {
  return d.getFullYear() + '-' + pad(d.getMonth() + 1) + '-' + pad(d.getDate())
}

function localHHMM(d = new Date()) {
  return pad(d.getHours()) + ':' + pad(d.getMinutes())
}

function addDays(dateStr, n) {
  const p = dateStr.split('-').map(Number)
  const d = new Date(p[0], p[1] - 1, p[2] + n)
  return localDate(d)
}

function toMinutes(hhmm) {
  const p = String(hhmm || '09:00').split(':')
  return Number(p[0]) * 60 + Number(p[1] || 0)
}

// 是否落在「设定时间 → +30 分钟」窗口内（与小程序一致）
function inSlot(noticeTime, now = new Date()) {
  const nowMin = now.getHours() * 60 + now.getMinutes()
  let diff = nowMin - toMinutes(noticeTime)
  if (diff < 0) diff += 24 * 60
  return diff >= 0 && diff < 30
}

// 把一个到期日的存单压成一条文案
function buildMessage(kind, expireDate, rows) {
  const total = rows.reduce((s, r) => s + (Number(r.total_money) || 0), 0)
  const banks = []
  rows.forEach(r => {
    if (r.bank_name && banks.indexOf(r.bank_name) < 0) banks.push(r.bank_name)
  })
  const title = (kind === 'pre' ? '明日到期' : '今日到期') + (rows.length > 1 ? ' · 共' + rows.length + '笔' : '')
  const lines = [
    title + '（' + expireDate + '）',
    '本息合计：' + calc.money(total) + ' 元',
    ''
  ]
  rows.slice(0, 10).forEach(r => {
    lines.push(
      '- ' + r.bank_name + '  ' + calc.money(r.total_money) + ' 元' +
      '（本金 ' + calc.money(r.principal) + ' + 利息 ' + calc.money(r.interest) + '）'
    )
  })
  if (rows.length > 10) lines.push('… 还有 ' + (rows.length - 10) + ' 笔')
  return {
    title,
    date: expireDate,
    banks,
    count: rows.length,
    total,
    text: lines.join('\n')
  }
}

async function postWebhook(url, kind, msg) {
  if (!url) return { ok: false, skipped: true }
  try {
    if (kind === 'wecom') {
      const res = await fetch(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ msgtype: 'text', text: { content: msg.text } })
      })
      return { ok: res.ok, detail: 'wecom ' + res.status }
    }
    if (kind === 'bark') {
      // bark: https://api.day.app/<KEY>   直接 GET 追加标题正文
      const full = url.replace(/\/$/, '') + '/' + encodeURIComponent(msg.title) + '/' + encodeURIComponent(msg.text)
      const res = await fetch(full)
      return { ok: res.ok, detail: 'bark ' + res.status }
    }
    if (kind === 'serverchan') {
      const res = await fetch(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
        body: new URLSearchParams({ title: msg.title, desp: msg.text }).toString()
      })
      return { ok: res.ok, detail: 'serverchan ' + res.status }
    }
    // generic：POST JSON，方便接飞书 / n8n / 自建服务
    const res = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ title: msg.title, text: msg.text, date: msg.date, count: msg.count, total: msg.total })
    })
    return { ok: res.ok, detail: 'generic ' + res.status }
  } catch (e) {
    return { ok: false, detail: (e && e.message) || 'webhook fail' }
  }
}

async function sendMail(msg) {
  const host = process.env.SMTP_HOST
  if (!host) return { ok: false, skipped: true }
  try {
    const transporter = nodemailer.createTransport({
      host,
      port: Number(process.env.SMTP_PORT || 465),
      secure: String(process.env.SMTP_SECURE || 'true') === 'true',
      auth: process.env.SMTP_USER ? { user: process.env.SMTP_USER, pass: process.env.SMTP_PASS } : undefined
    })
    await transporter.sendMail({
      from: process.env.SMTP_FROM || process.env.SMTP_USER,
      to: process.env.SMTP_TO || process.env.SMTP_USER,
      subject: '【理财日历】' + msg.title,
      text: msg.text
    })
    return { ok: true, detail: 'mail' }
  } catch (e) {
    return { ok: false, detail: 'mail fail: ' + ((e && e.message) || '') }
  }
}

function logInbox(entry) {
  db.prepare(
    'INSERT INTO inbox (created_at, send_date, kind, count, amount, channels, status, detail) VALUES (?, ?, ?, ?, ?, ?, ?, ?)'
  ).run(
    new Date().toISOString(),
    entry.date,
    entry.kind,
    entry.count,
    entry.total,
    entry.channels,
    entry.status,
    entry.detail || ''
  )
}

// 核心检查：返回本次发送结果，便于「我的」页做测试
async function runCheck(options = {}) {
  const opts = Object.assign({ force: false, test: false }, options || {})
  const settings = allSettings()
  const today = localDate()
  const tomorrow = addDays(today, 1)

  if (settings.remind_enabled !== '1') return { ok: true, skipped: '提醒已关闭' }
  if (!opts.force && !inSlot(settings.notice_time, new Date())) {
    return { ok: true, skipped: '不在提醒时间窗口（当前 ' + localHHMM() + '，设定 ' + settings.notice_time + '）' }
  }

  const jobs = []
  if (settings.remind_prev_day === '1') {
    const rows = db.prepare('SELECT * FROM deposits WHERE expire_date = ?').all(tomorrow)
    if (rows.length) jobs.push({ kind: 'pre', date: tomorrow, rows })
  }
  if (settings.remind_same_day === '1' || opts.test) {
    const rows = db.prepare('SELECT * FROM deposits WHERE expire_date = ?').all(today)
    if (rows.length) jobs.push({ kind: 'on', date: today, rows })
  }

  const results = []
  for (const job of jobs) {
    const key = job.kind + '|' + job.date
    // 非测试模式：同一天同一类型只发一次
    if (!opts.test) {
      const existed = db.prepare('SELECT sent_on FROM notice_log WHERE key = ?').get(key)
      if (existed && existed.sent_on === today) {
        results.push({ date: job.date, kind: job.kind, skipped: '今日已发过' })
        continue
      }
    }

    const msg = buildMessage(job.kind, job.date, job.rows)
    const channels = []
    const wh = await postWebhook(settings.webhook_url, settings.webhook_kind, msg)
    if (!wh.skipped) channels.push((wh.ok ? '✓ ' : '✗ ') + 'webhook(' + wh.detail + ')')
    const mail = await sendMail(msg)
    if (!mail.skipped) channels.push((mail.ok ? '✓ ' : '✗ ') + 'email(' + mail.detail + ')')

    const noChannel = wh.skipped && mail.skipped
    const ok = !noChannel && (wh.ok || mail.ok)
    const status = noChannel ? 'skip' : ok ? 'ok' : 'fail'
    logInbox({
      date: job.date,
      kind: job.kind,
      count: msg.count,
      total: msg.total,
      channels: channels.join(' | ') || '未配置通知渠道',
      status,
      detail: msg.text
    })

    if (!opts.test && ok) {
      db.prepare(
        'INSERT INTO notice_log (key, sent_on, created_at) VALUES (?, ?, ?) ON CONFLICT(key) DO UPDATE SET sent_on = excluded.sent_on, created_at = excluded.created_at'
      ).run(key, today, new Date().toISOString())
    }

    results.push({
      date: job.date,
      kind: job.kind,
      count: msg.count,
      total: msg.total,
      title: msg.title,
      channels,
      status
    })
  }

  return { ok: true, ranAt: localDate() + ' ' + localHHMM(), jobs: results }
}

let task = null

function start() {
  // 每分钟检查一次，落在 30 分钟窗口时执行，靠 notice_log 去重
  task = cron.schedule('* * * * *', () => {
    runCheck().catch(e => console.error('[notify]', e && e.message))
  }, { timezone: TZ })
  console.log('[notify] 到期提醒调度已启动（' + TZ + '）')
}

function stop() {
  if (task) task.stop()
}

module.exports = { start, stop, runCheck, buildMessage }
