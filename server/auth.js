// 轻量登录体系：scrypt 密码哈希 + HMAC 签名的会话 Cookie（不引入额外依赖）
const crypto = require('crypto')

const SECRET = process.env.SESSION_SECRET || 'dev-secret-change-me'
const COOKIE = 'fc_sid'
const MAX_AGE = 30 * 24 * 3600 // 30 天

function hashPassword(password) {
  const salt = crypto.randomBytes(16).toString('hex')
  const derived = crypto.scryptSync(String(password), salt, 64).toString('hex')
  return 'scrypt$' + salt + '$' + derived
}

function verifyPassword(password, stored) {
  if (!stored) return false
  const parts = String(stored).split('$')
  if (parts.length !== 3) return false
  const [, salt, expected] = parts
  const derived = crypto.scryptSync(String(password), salt, 64).toString('hex')
  try {
    return crypto.timingSafeEqual(Buffer.from(derived, 'hex'), Buffer.from(expected, 'hex'))
  } catch (e) {
    return false
  }
}

function sign(payload) {
  return crypto.createHmac('sha256', SECRET).update(payload).digest('hex').slice(0, 32)
}

function createSession(userId) {
  const exp = Date.now() + MAX_AGE * 1000
  const payload = userId + '.' + exp
  return { value: payload + '.' + sign(payload), maxAge: MAX_AGE }
}

function readSession(cookieHeader) {
  if (!cookieHeader) return null
  const raw = cookieHeader
    .split(';')
    .map(s => s.trim())
    .find(s => s.startsWith(COOKIE + '='))
  if (!raw) return null
  const token = decodeURIComponent(raw.slice(COOKIE.length + 1))
  const parts = token.split('.')
  if (parts.length !== 3) return null
  const payload = parts[0] + '.' + parts[1]
  if (sign(payload) !== parts[2]) return null
  const exp = Number(parts[1])
  if (!exp || Date.now() > exp) return null
  const uid = Number(parts[0])
  return Number.isFinite(uid) ? uid : null
}

function setCookie(res, token, maxAge) {
  const parts = [
    COOKIE + '=' + encodeURIComponent(token),
    'Path=/',
    'HttpOnly',
    'SameSite=Lax',
    'Max-Age=' + maxAge
  ]
  // 部署在 HTTPS 反代后时可用 COOKIE_SECURE=1 打开 Secure
  if (process.env.COOKIE_SECURE === '1') parts.push('Secure')
  res.append('Set-Cookie', parts.join('; '))
}

function clearCookie(res) {
  res.append('Set-Cookie', COOKIE + '=; Path=/; HttpOnly; SameSite=Lax; Max-Age=0')
}

module.exports = { hashPassword, verifyPassword, createSession, readSession, setCookie, clearCookie }
