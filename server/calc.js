// 日期、金额与单利计算：与小程序 miniprogram/utils/util.js 的口径完全一致
// （存期 = 整月 + 零头天数/30，利息 = 本金 × 年利率% × 存期月/12，保留两位）

function pad(n) {
  n = Number(n)
  return n < 10 ? '0' + n : '' + n
}

function formatDate(date) {
  if (typeof date === 'string') return date.slice(0, 10)
  return date.getFullYear() + '-' + pad(date.getMonth() + 1) + '-' + pad(date.getDate())
}

function todayStr() {
  return formatDate(new Date())
}

function parseDate(str) {
  const parts = String(str).split('-').map(Number)
  return new Date(parts[0], (parts[1] || 1) - 1, parts[2] || 1)
}

function addMonths(dateStr, months) {
  const parts = String(dateStr).split('-').map(Number)
  const base = new Date(parts[0], parts[1] - 1 + months, 1)
  const lastDay = new Date(base.getFullYear(), base.getMonth() + 1, 0).getDate()
  base.setDate(Math.min(parts[2], lastDay))
  return formatDate(base)
}

function daysBetween(fromStr, toStr) {
  return Math.round((parseDate(toStr) - parseDate(fromStr)) / 86400000)
}

function termMonths(startStr, endStr) {
  if (!startStr || !endStr) return 0
  const days = daysBetween(startStr, endStr)
  if (days <= 0) return 0
  const a = parseDate(startStr)
  const b = parseDate(endStr)
  let months = (b.getFullYear() - a.getFullYear()) * 12 + (b.getMonth() - a.getMonth())
  if (b.getDate() < a.getDate()) months -= 1
  if (months < 0) months = 0
  const anchor = addMonths(startStr, months)
  const restDays = Math.max(0, daysBetween(anchor, endStr))
  return months + Math.min(restDays, 30) / 30
}

function termLabel(months) {
  let m = Number(months) || 0
  if (!m) return ''
  // 存期按「整月 + 零头天数/30」算，会出现 60.0333 这种值，这里抹掉浮点误差
  m = Math.round(m * 10) / 10
  if (m < 12) {
    const mm = Math.max(1, Math.round(m))
    return mm + ' 个月'
  }
  const y = Math.floor(m / 12)
  const r = Math.round((m - y * 12) * 10) / 10
  if (r <= 0.4) return y + ' 年'
  const rr = Math.round(r)
  if (rr >= 12) return (y + 1) + ' 年'
  return y + ' 年 ' + rr + ' 个月'
}

// 存期标签配色：按年期分档
function termTone(months) {
  const m = Number(months) || 0
  if (!m) return ''
  if (m < 12) return 'tone-m'
  const y = Math.floor(m / 12)
  if (y === 1) return 'tone-1'
  if (y === 2) return 'tone-2'
  if (y === 3) return 'tone-3'
  if (y === 4) return 'tone-4'
  return 'tone-5'
}

function round2(num) {
  return Math.round((Number(num) || 0) * 100) / 100
}

function money(num) {
  const n = Number(num) || 0
  const fixed = n.toFixed(2)
  const parts = fixed.split('.')
  return parts[0].replace(/\B(?=(\d{3})+(?!\d))/g, ',') + '.' + parts[1]
}

function moneyCompact(num) {
  const n = Number(num) || 0
  if (Math.abs(n) >= 10000) return (n / 10000).toFixed(2) + '万'
  return money(n)
}

function calcInterest(principal, ratePercent, months) {
  const p = Number(principal) || 0
  const r = Number(ratePercent) || 0
  const m = Number(months) || 0
  if (p <= 0 || r <= 0 || m <= 0) return { interest: 0, total: p }
  const interest = p * (r / 100) * (m / 12)
  return { interest: round2(interest), total: round2(p + interest) }
}

// 给一条存单补齐展示字段（列表 / 日历 / 统计共用）
function decorate(row, members, today = todayStr()) {
  const interest = Number(row.interest) || 0
  const total = Number(row.total_money) || (Number(row.principal) || 0) + interest
  const daysLeft = daysBetween(today, row.expire_date)
  let status = 'active'
  let statusText = '未到期'
  if (daysLeft > 0) {
    statusText = '还有' + daysLeft + '天到期'
  } else if (daysLeft === 0) {
    status = 'today'
    statusText = '今天到期'
  } else {
    status = 'expired'
    statusText = '已过期' + (-daysLeft) + '天'
  }
  const creator = (members || []).find(m => m.id === row.creator_id)
  // members() 返回的是驼峰字段（displayName），这里两种写法都兼容
  const creatorName = creator ? (creator.displayName || creator.display_name || '') : ''
  return {
    id: row.id,
    bankId: row.bank_id || '',
    bankName: row.bank_name,
    saveDate: row.save_date,
    expireDate: row.expire_date,
    principal: Number(row.principal) || 0,
    ratePercent: Number(row.rate_percent) || 0,
    termMonths: Number(row.term_months) || 0,
    termText: termLabel(row.term_months),
    termTone: termTone(row.term_months),
    interest,
    totalMoney: total,
    principalText: money(row.principal),
    principalCompact: moneyCompact(row.principal),
    interestText: money(interest),
    interestCompact: moneyCompact(interest),
    totalText: money(total),
    totalCompact: moneyCompact(total),
    remark: row.remark || '',
    creatorId: row.creator_id || null,
    creatorName,
    creatorInitial: creatorName ? creatorName.slice(0, 1) : '',
    isExpired: daysLeft < 0,
    isToday: daysLeft === 0,
    daysLeft,
    status,
    statusText
  }
}

module.exports = {
  pad,
  formatDate,
  todayStr,
  parseDate,
  addMonths,
  daysBetween,
  termMonths,
  termLabel,
  termTone,
  round2,
  money,
  moneyCompact,
  calcInterest,
  decorate
}
