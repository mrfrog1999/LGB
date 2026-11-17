// 内置银行库：品牌色 + 两字简称 + 可选官方 logo（与小程序 banks.js 一致）
// logo 文件放在 web/images/banks/ 下，路径以 /images/banks/ 开头由静态服务提供
const BANKS = [
  { id: 'icbc', name: '工商银行', short: '工行', color: '#C8102E', logo: '/images/banks/icbc.png' },
  { id: 'ccb', name: '建设银行', short: '建行', color: '#0A5CA8', logo: '/images/banks/ccb.png' },
  { id: 'abc', name: '农业银行', short: '农行', color: '#009A44', logo: '/images/banks/abc.png' },
  { id: 'boc', name: '中国银行', short: '中行', color: '#9E1F31', logo: '/images/banks/boc.png' },
  { id: 'bocom', name: '交通银行', short: '交行', color: '#00539B', logo: '/images/banks/bocom.png' },
  { id: 'psbc', name: '邮储银行', short: '邮储', color: '#007A3D', logo: '/images/banks/psbc.png' },
  { id: 'cmb', name: '招商银行', short: '招商', color: '#C8102E', logo: '/images/banks/cmb.png' },
  { id: 'citics', name: '中信银行', short: '中信', color: '#E2231A', logo: '/images/banks/citic.png' },
  { id: 'cmbc', name: '民生银行', short: '民生', color: '#0072BC' },
  { id: 'cib', name: '兴业银行', short: '兴业', color: '#004A99' },
  { id: 'spdb', name: '浦发银行', short: '浦发', color: '#00559B', logo: '/images/banks/spdb.png' },
  { id: 'ceb', name: '光大银行', short: '光大', color: '#7A1F4B' },
  { id: 'pab', name: '平安银行', short: '平安', color: '#F26A21' },
  { id: 'hxb', name: '华夏银行', short: '华夏', color: '#C8102E' },
  { id: 'cgb', name: '广发银行', short: '广发', color: '#D7000F' },
  { id: 'bob', name: '北京银行', short: '北京', color: '#0055A5', logo: '/images/banks/bob.png' },
  { id: 'shb', name: '上海银行', short: '上海', color: '#00447C' },
  { id: 'nbcb', name: '宁波银行', short: '宁波', color: '#1B7A46' },
  { id: 'njcb', name: '南京银行', short: '南京', color: '#0A5CA8' },
  { id: 'bohai', name: '渤海银行', short: '渤海', color: '#00857C' },
  { id: 'hfbank', name: '恒丰银行', short: '恒丰', color: '#007B5E' },
  { id: 'czbank', name: '浙商银行', short: '浙商', color: '#B3202C' },
  { id: 'jsbank', name: '江苏银行', short: '江苏', color: '#0055A5' },
  { id: 'hsbank', name: '徽商银行', short: '徽商', color: '#B3202C' },
  { id: 'webank', name: '微众银行', short: '微众', color: '#1677FF' },
  { id: 'mybank', name: '网商银行', short: '网商', color: '#FF6A00' }
]

const CUSTOM_COLOR = '#5B6B7F'

// 只保留带 logo 的银行，用于选择列表
const LOGO_BANKS = BANKS.filter(b => !!b.logo)

function findByName(name) {
  if (!name) return null
  const clean = String(name).trim()
  const exact = BANKS.find(b => b.name === clean)
  if (exact) return exact
  return BANKS.find(b => clean.indexOf(b.name) > -1 || b.name.indexOf(clean) > -1) || null
}

function fallback(name) {
  const clean = String(name || '').trim()
  return {
    id: '',
    name: clean || '定期存款',
    short: clean ? clean.slice(0, 2) : '银行',
    color: CUSTOM_COLOR,
    logo: ''
  }
}

// 补全银行展示信息：有 logo 用 logo，没有则前端用品牌色方块 + 简称
function decorateBank(row) {
  const bank = findByName(row.bank_name) || (row.bank_id ? BANKS.find(b => b.id === row.bank_id) : null) || fallback(row.bank_name)
  return {
    bankId: bank.id || row.bank_id || '',
    bankShort: bank.short,
    bankColor: bank.color,
    bankLogo: bank.logo || ''
  }
}

module.exports = { BANKS, LOGO_BANKS, CUSTOM_COLOR, findByName, fallback, decorateBank }
