// 理财日历 Web 版前端（原生 JS，无框架、无构建步骤）
(function () {
  'use strict'

  // ---------------- 状态 ----------------
  const S = {
    user: null,
    members: [],
    settings: {},
    banks: [],
    deposits: [],
    page: 'cal',
    calMonth: startOfMonth(new Date()),
    selectedDay: todayStr(),
    tab: 'all',
    fBank: '',
    fYear: '',
    fMonth: '',
    selMode: false,
    sel: {},
    statMode: 'save',
    sYear: '',
    sMonth: '',
    sDay: '',
    editing: null,
    pickTarget: null
  }

  const LOGO_BANKS_LIMIT = 10
  const RATE_PRESETS = ['1.75', '1.65', '1.55', '1.35', '1.25']
  const TERM_PRESETS = [
    { label: '3个月', months: 3 },
    { label: '6个月', months: 6 },
    { label: '1年', months: 12 },
    { label: '2年', months: 24 },
    { label: '3年', months: 36 },
    { label: '5年', months: 60 }
  ]

  // ---------------- 工具 ----------------
  function $(id) { return document.getElementById(id) }

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, c => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
    }[c]))
  }

  function pad(n) { return n < 10 ? '0' + n : '' + n }

  function todayStr() {
    const d = new Date()
    return d.getFullYear() + '-' + pad(d.getMonth() + 1) + '-' + pad(d.getDate())
  }

  function startOfMonth(d) { return new Date(d.getFullYear(), d.getMonth(), 1) }

  function ymd(d) { return d.getFullYear() + '-' + pad(d.getMonth() + 1) + '-' + pad(d.getDate()) }

  function addMonths(dateStr, months) {
    const p = String(dateStr).split('-').map(Number)
    const base = new Date(p[0], p[1] - 1 + months, 1)
    const lastDay = new Date(base.getFullYear(), base.getMonth() + 1, 0).getDate()
    base.setDate(Math.min(p[2], lastDay))
    return ymd(base)
  }

  function termMonthsLocal(s, e) {
    if (!s || !e) return 0
    const a = new Date(s), b = new Date(e)
    let months = (b.getFullYear() - a.getFullYear()) * 12 + (b.getMonth() - a.getMonth())
    if (b.getDate() < a.getDate()) months -= 1
    const anchor = addMonths(s, Math.max(0, months))
    const days = Math.round((new Date(e) - new Date(anchor)) / 86400000)
    return months + Math.min(Math.max(days, 0), 30) / 30
  }

  function termLabel(m) {
    m = Number(m) || 0
    if (!m) return '—'
    if (m < 12) return m + ' 个月'
    const y = Math.floor(m / 12), r = m % 12
    return r === 0 ? y + ' 年' : y + ' 年 ' + r + ' 个月'
  }

  function moneyLocal(n) {
    const v = Number(n) || 0
    const parts = v.toFixed(2).split('.')
    return parts[0].replace(/\B(?=(\d{3})+(?!\d))/g, ',') + '.' + parts[1]
  }

  function calcLocal(principal, rate, months) {
    const p = Number(principal) || 0, r = Number(rate) || 0, m = Number(months) || 0
    if (!(p > 0) || !(r > 0) || !(m > 0)) return { interest: 0, total: Math.round(p * 100) / 100 }
    const interest = Math.round(p * (r / 100) * (m / 12) * 100) / 100
    return { interest, total: Math.round((p + interest) * 100) / 100 }
  }

  // ---------------- 请求 ----------------
  async function requ(path, options) {
    const opts = Object.assign({ credentials: 'same-origin' }, options || {})
    if (opts.body && typeof opts.body !== 'string') {
      opts.headers = Object.assign({ 'Content-Type': 'application/json' }, opts.headers || {})
      opts.body = JSON.stringify(opts.body)
    }
    const res = await fetch('/api' + path, opts)
    let data = null
    try { data = await res.json() } catch (e) { data = { ok: false, message: '返回格式异常' } }
    if (res.status === 401) {
      showLogin()
      throw new Error(data.message || '请先登录')
    }
    if (!res.ok || data.ok === false) {
      throw new Error((data && data.message) || ('请求失败 ' + res.status))
    }
    return data
  }

  // ---------------- 弹窗（替代浏览器 alert/confirm） ----------------
  function toast(msg) {
    const el = $('toast')
    el.textContent = msg
    el.style.display = 'block'
    clearTimeout(el._t)
    el._t = setTimeout(() => { el.style.display = 'none' }, 2200)
  }

  function dialog(title, message, opts) {
    const o = Object.assign({ confirmText: '确定', cancelText: '取消', danger: false, input: null }, opts || {})
    return new Promise(resolve => {
      const mask = document.createElement('div')
      mask.className = 'mask'
      mask.style.zIndex = 210
      mask.innerHTML =
        '<div class="sheet" style="max-width:420px;border-radius:16px;margin:auto;padding:20px">' +
        '<div style="font-size:16px;font-weight:600;margin-bottom:8px">' + esc(title) + '</div>' +
        (message ? '<div style="font-size:13px;color:#6b7385;line-height:1.7;white-space:pre-wrap">' + esc(message) + '</div>' : '') +
        (o.input ? '<div class="field" style="margin-top:12px"><label>' + esc(o.input.label || '') + '</label><input id="dlgInput" value="' + esc(o.input.value || '') + '"></div>' : '') +
        '<div style="display:flex;gap:10px;margin-top:16px">' +
        '<button class="btn ghost" style="flex:1" data-act="cancel">' + esc(o.cancelText) + '</button>' +
        '<button class="btn" style="flex:1' + (o.danger ? ';background:#fa5151' : '') + '" data-act="ok">' + esc(o.confirmText) + '</button>' +
        '</div></div>'
      document.body.appendChild(mask)
      const input = $('dlgInput')
      if (input) setTimeout(() => input.focus(), 30)
      mask.addEventListener('click', ev => {
        const act = ev.target.getAttribute && ev.target.getAttribute('data-act')
        if (!act) return
        const val = input ? input.value.trim() : null
        document.body.removeChild(mask)
        resolve(act === 'ok' ? (input ? val : true) : false)
      })
      if (input) input.addEventListener('keydown', e => { if (e.key === 'Enter') mask.querySelector('[data-act="ok"]').click() })
    })
  }

  // ---------------- 页面切换 ----------------
  function setPage(page) {
    S.page = page
    ;['cal', 'list', 'stats', 'mine'].forEach(p => {
      $('page-' + p).style.display = p === page ? '' : 'none'
    })
    Array.prototype.forEach.call($('tabbar').children, b => {
      b.classList.toggle('on', b.getAttribute('data-page') === page)
    })
    if (page === 'cal') renderCalendar()
    if (page === 'list') renderList()
    if (page === 'stats') renderStats()
    if (page === 'mine') renderMine()
  }

  // ---------------- 登录 ----------
  function showLogin() {
    $('app').style.display = 'none'
    $('login').style.display = 'flex'
  }

  function showApp() {
    $('login').style.display = 'none'
    $('app').style.display = 'block'
  }

  async function boot() {
    try {
      const me = await requ('/me', { method: 'GET' })
      S.user = me.user
      S.members = me.members || []
      S.settings = me.settings || {}
      await loadBanks()
      await loadDeposits()
      showApp()
      bindMineOnce()
      setPage('cal')
      loadVersion()
    } catch (e) {
      showLogin()
    }
  }

  async function loadBanks() {
    try {
      const r = await requ('/banks', { method: 'GET' })
      S.banks = r.banks || []
    } catch (e) { S.banks = [] }
  }

  async function loadDeposits() {
    const r = await requ('/deposits', { method: 'GET' })
    S.deposits = r.list || []
    computeFilterOptions()
  }

  async function loadVersion() {
    try {
      const r = await fetch('/api/health').then(x => x.json())
      if (r && r.version) $('verText').textContent = r.version
    } catch (e) {}
  }

  // ---------------- 银行展示 ----------------
  function bankIcon(d, size) {
    const px = size || 40
    if (d.bankLogo) {
      return '<div class="bank-ico" style="width:' + px + 'px;height:' + px + 'px"><img src="' + esc(d.bankLogo) + '" alt=""></div>'
    }
    const color = d.bankColor || '#5B6B7F'
    return '<div class="bank-ico" style="width:' + px + 'px;height:' + px + 'px;background:' + esc(color) + '">' +
      esc(d.bankShort || (d.bankName || '银行').slice(0, 2)) + '</div>'
  }

  // ---------------- 存单卡片 ----------------
  function depositCard(d, opts) {
    const o = Object.assign({ selectable: false, actions: true }, opts || {})
    const checked = !!S.sel[d.id]
    const cls = 'card deposit' + (o.selectable && checked ? ' selected' : '')
    let html = '<div class="' + cls + '" data-id="' + d.id + '">'
    html += '<div class="dep-top">'
    if (o.selectable) {
      html += '<div class="sel-check' + (checked ? ' on' : '') + '" data-sel="' + d.id + '">' + (checked ? '✓' : '') + '</div>'
    }
    html += bankIcon(d)
    html += '<div class="dep-main">'
    html += '<div class="dep-name-row"><span class="dep-name">' + esc(d.bankName) + '</span>'
    if (d.termText) html += '<span class="term-pill ' + esc(d.termTone) + '">' + esc(d.termText) + '</span>'
    html += '</div>'
    html += '<div class="dep-period">' + esc(d.saveDate) + ' → ' + esc(d.expireDate) + '</div>'
    html += '</div>'
    html += '<span class="status ' + esc(d.status) + '">' + esc(d.statusText) + '</span>'
    if (d.creatorName) html += '<div class="avatar" title="' + esc(d.creatorName) + '">' + esc(d.creatorInitial) + '</div>'
    html += '</div>'

    html += '<div class="metrics">'
    html += '<div><div class="k">本金（元）</div><div class="v">' + esc(d.principalText) + '</div></div>'
    html += '<div><div class="k">年利率</div><div class="v">' + esc(d.ratePercent) + '%</div></div>'
    html += '<div><div class="k">预估利息</div><div class="v">' + esc(d.interestText) + '</div></div>'
    html += '<div><div class="k">本息合计</div><div class="v hl">' + esc(d.totalText) + '</div></div>'
    html += '</div>'

    if (d.remark) html += '<div class="dep-remark">备注 · ' + esc(d.remark) + '</div>'

    if (o.actions && !o.selectable) {
      html += '<div class="dep-actions">' +
        '<button data-edit="' + d.id + '">编辑</button>' +
        '<button class="del" data-del="' + d.id + '">删除</button>' +
        '</div>'
    }
    html += '</div>'
    return html
  }

  function emptyBox(text) {
    return '<div class="empty">' + esc(text) + '</div>'
  }

  // ---------------- 日历 ----------------
  function renderCalendar() {
    const month = S.calMonth
    $('calTitle').textContent = month.getFullYear() + ' 年 ' + (month.getMonth() + 1) + ' 月'

    // 顶部：总本金 + 最近到期
    const total = S.deposits.reduce((s, d) => s + d.principal, 0)
    $('heroPrincipal').textContent = moneyLocal(total)
    const latest = S.deposits
      .filter(d => d.expireDate >= todayStr())
      .sort((a, b) => a.expireDate.localeCompare(b.expireDate))[0]
    if (latest) {
      $('heroBadge').textContent = '最近到期 ' + latest.expireDate
      $('heroExpire').textContent = '到期利息 ' + latest.interestText
    } else {
      $('heroBadge').textContent = '暂无未到期存单'
      $('heroExpire').textContent = ''
    }

    // 网格：周一为第一列
    const first = new Date(month.getFullYear(), month.getMonth(), 1)
    const offset = (first.getDay() + 6) % 7
    const daysInMonth = new Date(month.getFullYear(), month.getMonth() + 1, 0).getDate()
    const saveMap = {}, expMap = {}
    S.deposits.forEach(d => {
      if (d.saveDate.slice(0, 7) === ymd(month).slice(0, 7)) saveMap[d.saveDate] = (saveMap[d.saveDate] || 0) + 1
      if (d.expireDate.slice(0, 7) === ymd(month).slice(0, 7)) expMap[d.expireDate] = (expMap[d.expireDate] || 0) + 1
    })

    let html = ''
    for (let i = 0; i < offset; i++) html += '<div class="day out"></div>'
    const today = todayStr()
    for (let day = 1; day <= daysInMonth; day++) {
      const date = month.getFullYear() + '-' + pad(month.getMonth() + 1) + '-' + pad(day)
      const isToday = date === today
      const isOn = date === S.selectedDay
      const hasIn = !!saveMap[date]
      const hasOut = !!expMap[date]
      html += '<div class="day' + (isToday ? ' today' : '') + (isOn ? ' on' : '') + '" data-date="' + date + '">' +
        '<div class="num">' + day + '</div>' +
        '<div class="dots">' +
        (hasIn ? '<span class="dot in"></span>' : '') +
        (hasOut ? '<span class="dot outd"></span>' : '') +
        '</div></div>'
    }
    $('calGrid').innerHTML = html
    renderDayPanel(S.selectedDay)
  }

  function renderDayPanel(date) {
    const box = $('dayPanel')
    if (!date) { box.innerHTML = ''; return }
    const saves = S.deposits.filter(d => d.saveDate === date).sort((a, b) => b.principal - a.principal)
    const exps = S.deposits.filter(d => d.expireDate === date).sort((a, b) => b.principal - a.principal)

    let html = '<div class="card"><div style="font-size:14px;font-weight:600;margin-bottom:10px">' + esc(date) + ' 的记录</div>'
    if (!saves.length && !exps.length) {
      html += '<div class="empty" style="padding:18px 0">这一天没有存入或到期记录</div>'
    }
    if (saves.length) {
      html += '<div class="day-section"><div class="sec-head"><span class="pill pill-in">存入</span>' +
        '<span>' + saves.length + ' 笔 · 本金 ' + moneyLocal(saves.reduce((s, d) => s + d.principal, 0)) + '</span></div>'
      html += saves.map(d => depositCard(d, { actions: true })).join('')
      html += '</div>'
    }
    if (exps.length) {
      html += '<div class="day-section"><div class="sec-head"><span class="pill pill-out">到期</span>' +
        '<span>' + exps.length + ' 笔 · 本息 ' + moneyLocal(exps.reduce((s, d) => s + d.totalMoney, 0)) + '</span></div>'
      html += exps.map(d => depositCard(d, { actions: true })).join('')
      html += '</div>'
    }
    html += '</div>'
    box.innerHTML = html
  }

  // ---------------- 台账 ----------------
  function computeFilterOptions() {
    const banks = [], years = [], months = []
    S.deposits.forEach(d => {
      if (d.bankName && banks.indexOf(d.bankName) < 0) banks.push(d.bankName)
      ;[d.saveDate, d.expireDate].forEach(v => {
        const y = String(v || '').slice(0, 4)
        const m = String(v || '').slice(5, 7)
        if (y && years.indexOf(y) < 0) years.push(y)
        if (m && months.indexOf(m) < 0) months.push(m)
      })
    })
    banks.sort()
    years.sort(); years.reverse()
    months.sort()
    S.optBanks = banks
    S.optYears = years
    S.optMonths = months

    $('fBank').innerHTML = '<option value="">全部银行</option>' + banks.map(b => '<option value="' + esc(b) + '">' + esc(b) + '</option>').join('')
    $('fYear').innerHTML = '<option value="">全部年份</option>' + years.map(y => '<option value="' + y + '">' + y + ' 年</option>').join('')
    $('fMonth').innerHTML = '<option value="">全部月份</option>' + months.map(m => '<option value="' + m + '">' + Number(m) + ' 月</option>').join('')
    $('fBank').value = S.fBank
    $('fYear').value = S.fYear
    $('fMonth').value = S.fMonth
    syncFilterStyle()
  }

  function syncFilterStyle() {
    ;[['fBank', S.fBank], ['fYear', S.fYear], ['fMonth', S.fMonth]].forEach(([id, v]) => {
      $(id).classList.toggle('on', !!v)
    })
    $('btnReset').style.display = (S.fBank || S.fYear || S.fMonth) ? '' : 'none'
  }

  function filteredList() {
    let list = S.deposits.slice()
    if (S.tab === 'active') list = list.filter(d => !d.isExpired)
    if (S.tab === 'expired') list = list.filter(d => d.isExpired)
    if (S.fBank) list = list.filter(d => d.bankName === S.fBank)
    if (S.fYear || S.fMonth) {
      list = list.filter(d => {
        const hit = v => {
          v = String(v || '')
          if (S.fYear && v.slice(0, 4) !== S.fYear) return false
          if (S.fMonth && v.slice(5, 7) !== S.fMonth) return false
          return true
        }
        return hit(d.saveDate) || hit(d.expireDate)
      })
    }
    return list.sort((a, b) => a.expireDate.localeCompare(b.expireDate))
  }

  function renderList() {
    const list = filteredList()
    $('listCount').textContent = list.length
    syncFilterStyle()

    if (S.selMode) {
      $('selBar').style.display = ''
      const n = Object.keys(S.sel).length
      $('selCount').textContent = n
      $('bulkBar').style.display = 'flex'
      $('bulkApply').disabled = n === 0
      $('bulkApply').textContent = n ? '修改添加人（' + n + '）' : '修改添加人'
    } else {
      $('selBar').style.display = 'none'
      $('bulkBar').style.display = 'none'
    }

    if (!list.length) {
      $('listBox').innerHTML = emptyBox(
        S.fBank || S.fYear || S.fMonth ? '没有符合条件的存单，试试重置筛选' :
          S.tab === 'all' ? '还没有存单，点「日历」页的新增存单开始记' : '该分类下暂无存单'
      )
      return
    }
    $('listBox').innerHTML = list.map(d => depositCard(d, { selectable: S.selMode })).join('')
  }

  // ---------------- 统计 ----------------
  function renderStats() {
    const mode = S.statMode
    // 年份/月/日选项按当前模式的日期来源生成
    const dates = S.deposits.map(d => mode === 'expire' ? d.expireDate : d.saveDate)
    const years = Array.from(new Set(dates.map(d => d.slice(0, 4)))).sort().reverse()
    const months = Array.from(new Set(dates.map(d => d.slice(5, 7)))).sort()
    const days = Array.from(new Set(dates.map(d => d.slice(8, 10)))).sort()

    const yearSel = $('sYear')
    const monthSel = $('sMonth')
    const daySel = $('sDay')
    yearSel.innerHTML = '<option value="">全部年份</option>' + years.map(y => '<option value="' + y + '">' + y + ' 年</option>').join('')
    monthSel.innerHTML = '<option value="">全部月份</option>' + months.map(m => '<option value="' + m + '">' + Number(m) + ' 月</option>').join('')
    daySel.innerHTML = '<option value="">全部日期</option>' + days.map(x => '<option value="' + x + '">' + Number(x) + ' 日</option>').join('')

    if (years.indexOf(S.sYear) < 0) S.sYear = ''
    if (months.indexOf(S.sMonth) < 0) S.sMonth = ''
    if (days.indexOf(S.sDay) < 0) S.sDay = ''
    yearSel.value = S.sYear; monthSel.value = S.sMonth; daySel.value = S.sDay
    $('statHint').textContent = S.sDay ? '按日统计' : S.sMonth ? '按月统计' : S.sYear ? '按年统计' : '全部数据'

    const params = '?mode=' + mode + '&year=' + encodeURIComponent(S.sYear) + '&month=' + encodeURIComponent(S.sMonth) + '&day=' + encodeURIComponent(S.sDay)
    requ('/stats' + params, { method: 'GET' }).then(r => {
      $('statLabel').textContent = mode === 'expire' ? '到期合计' : '存入合计'
      $('statCount').textContent = r.summary.count + ' 笔'
      $('statPrincipal').textContent = r.summary.principalText
      $('statInterest').textContent = r.summary.interestText
      $('statTotal').textContent = r.summary.totalText
      const list = r.list || []
      $('statList').innerHTML = list.length
        ? list.map(d => depositCard(d, { actions: false })).join('')
        : emptyBox(mode === 'expire' ? '该区间没有到期存单' : '该区间没有存入记录')
    }).catch(e => toast(e.message))
  }

  // ---------------- 我的 ----------------
  let mineBound = false

  function bindMineOnce() {
    if (mineBound) return
    mineBound = true

    Array.prototype.forEach.call(document.querySelectorAll('.switch'), sw => {
      sw.addEventListener('click', async () => {
        const key = sw.getAttribute('data-key')
        const next = S.settings[key] === '1' ? '0' : '1'
        S.settings[key] = next
        sw.classList.toggle('on', next === '1')
        try {
          await requ('/settings', { method: 'PUT', body: { [key]: next } })
        } catch (e) { toast(e.message) }
      })
    })

    $('inNoticeTime').addEventListener('change', async e => {
      try {
        await requ('/settings', { method: 'PUT', body: { notice_time: e.target.value } })
        S.settings.notice_time = e.target.value
        toast('提醒时间已保存')
      } catch (err) { toast(err.message) }
    })

    $('inWebhookKind').addEventListener('change', e => { S.settings.webhook_kind = e.target.value })
    $('inWebhookUrl').addEventListener('input', e => { S.settings.webhook_url = e.target.value })

    $('btnSaveNotify').addEventListener('click', async () => {
      try {
        await requ('/settings', {
          method: 'PUT',
          body: {
            webhook_kind: $('inWebhookKind').value,
            webhook_url: $('inWebhookUrl').value.trim(),
            notice_time: $('inNoticeTime').value || '09:00'
          }
        })
        toast('已保存')
      } catch (e) { toast(e.message) }
    })

    $('btnTestNotify').addEventListener('click', async () => {
      try {
        const r = await requ('/notify/test', { method: 'POST' })
        const jobs = r.jobs || []
        if (!jobs.length) {
          dialog('没有可测试的存单', '今天和明天都没有到期存单。可以先加一笔到期日为明天的存单，再点测试。', { cancelText: '知道了' })
          return
        }
        const lines = jobs.map(j => j.title + '（' + j.count + ' 笔，' + moneyLocal(j.total) + ' 元）\n渠道：' + (j.channels.join(' | ') || '无'))
        dialog('测试已发送', lines.join('\n\n'), { cancelText: '关闭' })
      } catch (e) { dialog('发送失败', e.message, { cancelText: '关闭' }) }
    })

    $('btnRenameMe').addEventListener('click', async () => {
      const name = await dialog('修改我的称呼', '', { input: { label: '新称呼', value: S.user.displayName } })
      if (!name) return
      try {
        const r = await requ('/members/' + S.user.id, { method: 'PUT', body: { displayName: name } })
        S.members = r.members
        S.user.displayName = name
        renderMine()
        await loadDeposits()
        renderCalendar()
        toast('已更新')
      } catch (e) { toast(e.message) }
    })

    $('btnExport').addEventListener('click', () => { window.location.href = '/api/export' })

    $('btnImportPick').addEventListener('click', () => $('fileImport').click())
    $('fileImport').addEventListener('change', async e => {
      const file = e.target.files && e.target.files[0]
      if (!file) return
      const text = await file.text()
      let json = null
      try { json = JSON.parse(text) } catch (err) {
        dialog('文件无法解析', '这不是有效的 JSON 文件。', { cancelText: '关闭' })
        return
      }
      const ok = await dialog('确认导入', '将追加导入 ' + ((json.items || []).length) + ' 笔存单。\n注意：导入不会覆盖现有数据，重复导入会翻倍。', { confirmText: '开始导入' })
      if (!ok) return
      try {
        const r = await requ('/import', { method: 'POST', body: json })
        dialog('导入完成', '成功 ' + r.added + ' 笔' + (r.failed && r.failed.length ? '，失败 ' + r.failed.length + ' 笔' : ''), { cancelText: '好' })
        await loadDeposits()
        setPage(S.page)
      } catch (err) { dialog('导入失败', err.message, { cancelText: '关闭' }) }
      e.target.value = ''
    })

    $('btnLogout').addEventListener('click', async () => {
      const ok = await dialog('退出登录', '确定要退出吗？', { confirmText: '退出', danger: true })
      if (!ok) return
      await requ('/auth/logout', { method: 'POST' }).catch(() => {})
      location.reload()
    })
  }

  function renderMine() {
    $('meName').textContent = S.user.displayName
    $('meAccount').textContent = '账号 ' + S.user.username + (S.user.isOwner ? ' · 创建者' : '')
    $('meAvatar').textContent = (S.user.displayName || '?').slice(0, 1)

    $('swRemind').classList.toggle('on', S.settings.remind_enabled === '1')
    $('swPrev').classList.toggle('on', S.settings.remind_prev_day === '1')
    $('swSame').classList.toggle('on', S.settings.remind_same_day === '1')
    $('inNoticeTime').value = S.settings.notice_time || '09:00'
    $('inWebhookKind').value = S.settings.webhook_kind || 'generic'
    $('inWebhookUrl').value = S.settings.webhook_url || ''

    $('memberTip').textContent = '共 ' + S.members.length + ' 人'
    $('memberList').innerHTML = S.members.map(m => {
      const isMe = m.id === S.user.id
      return '<div class="member-row">' +
        '<div class="avatar" style="width:34px;height:34px;font-size:13px">' + esc(m.initial) + '</div>' +
        '<div style="flex:1">' +
        '<div class="nm">' + esc(m.displayName) +
        (m.isOwner ? ' <span class="tag owner">创建者</span>' : ' <span class="tag">成员</span>') +
        (isMe ? ' <span class="tag me">我</span>' : '') +
        '</div><div class="muted">加入于 ' + esc(m.joinedAt) + '</div></div>' +
        ((S.user.isOwner || isMe) ? '<button class="link" data-rename="' + m.id + '">改名</button>' : '') +
        '</div>'
    }).join('')

    Array.prototype.forEach.call(document.querySelectorAll('[data-rename]'), btn => {
      btn.addEventListener('click', async () => {
        const id = Number(btn.getAttribute('data-rename'))
        const m = S.members.find(x => x.id === id)
        const name = await dialog('修改成员称呼', '', { input: { label: '新称呼', value: m.displayName } })
        if (!name) return
        try {
          const r = await requ('/members/' + id, { method: 'PUT', body: { displayName: name } })
          S.members = r.members
          if (id === S.user.id) S.user.displayName = name
          renderMine()
          await loadDeposits()
          renderCalendar()
        } catch (e) { toast(e.message) }
      })
    })

    requ('/notifications', { method: 'GET' }).then(r => {
      const list = r.list || []
      $('noticeList').innerHTML = list.length ? list.map(n =>
        '<div class="notice-item">' +
        '<div class="top"><span>' + esc(n.date) + ' · ' + (n.kind === 'pre' ? '明日到期' : '当日到期') + ' · ' + n.count + ' 笔</span>' +
        '<span>' + (n.status === 'ok' ? '成功' : n.status === 'skip' ? '跳过' : '失败') + '</span></div>' +
        '<div class="body">' + esc(n.channels || '') + '</div></div>'
      ).join('') : '<div class="muted">暂无推送记录</div>'
    }).catch(() => {})
  }

  // ---------------- 存单编辑 ----------------
  function openEditor(depositId) {
    S.editing = depositId ? S.deposits.find(d => d.id === depositId) : null
    const isEdit = !!S.editing
    $('edTitle').textContent = isEdit ? '编辑存单' : '新增存单'
    $('edDelete').style.display = isEdit ? '' : 'none'

    // 银行网格：只展示有 logo 的银行（与小程序一致）
    const logoBanks = S.banks.filter(b => b.logo).slice(0, LOGO_BANKS_LIMIT)
    const selectedBank = isEdit ? S.editing.bankName : ''
    const bank = S.banks.find(b => b.name === selectedBank)
    if (bank) {
      const key = bank.id
      $('edBankCustom').value = selectedBank
      S.edBankKey = key
    } else {
      S.edBankKey = ''
      $('edBankCustom').value = selectedBank || ''
    }
    $('edBanks').innerHTML = logoBanks.map(b =>
      '<div class="bank-cell' + (b.id === S.edBankKey ? ' on' : '') + '" data-bank="' + esc(b.id) + '">' +
      '<div class="bank-ico" style="width:32px;height:32px"><img src="' + esc(b.logo) + '" alt=""></div>' +
      '<div class="nm">' + esc(b.name) + '</div></div>'
    ).join('')
    // 自定义输入里有名字且不是网格银行时清掉高亮
    if (!bank) Array.prototype.forEach.call(document.querySelectorAll('.bank-cell'), c => c.classList.remove('on'))

    $('edSaveDate').value = isEdit ? S.editing.saveDate : todayStr()
    $('edExpireDate').value = isEdit ? S.editing.expireDate : addMonths(todayStr(), 36)
    $('edPrincipal').value = isEdit ? S.editing.principal : ''
    $('edRate').value = isEdit ? (S.editing.ratePercent || '') : ''
    $('edRemark').value = isEdit ? S.editing.remark : ''

    // 存期预设
    $('edPresets').innerHTML = TERM_PRESETS.map(p =>
      '<button class="chip" data-term="' + p.months + '">' + p.label + '</button>'
    ).join('')

    // 利率预设
    $('edRatePresets').innerHTML = RATE_PRESETS.map(r =>
      '<button class="rate-chip' + (String(S.editing && S.editing.ratePercent) === r ? ' on' : '') + '" data-rate="' + r + '">' + r + '%</button>'
    ).join('')

    // 归属成员（仅编辑且成员 ≥2）
    if (isEdit && S.members.length > 1) {
      $('edMemberField').style.display = ''
      $('edMember').innerHTML = S.members.map(m =>
        '<option value="' + m.id + '"' + (m.id === S.editing.creatorId ? ' selected' : '') + '>' +
        esc(m.displayName) + (m.isOwner ? '（创建者）' : '') + '</option>'
      ).join('')
    } else {
      $('edMemberField').style.display = 'none'
    }

    syncPresetChips()
    recalcEditor()
    $('editorMask').style.display = 'flex'
  }

  function currentBankName() {
    const key = S.edBankKey
    if (key) {
      const b = S.banks.find(x => x.id === key)
      if (b) return b.name
    }
    return ($('edBankCustom').value || '').trim()
  }

  function syncPresetChips() {
    const s = $('edSaveDate').value
    const e = $('edExpireDate').value
    Array.prototype.forEach.call(document.querySelectorAll('[data-term]'), c => {
      const m = Number(c.getAttribute('data-term'))
      c.classList.toggle('on', s && e ? addMonths(s, m) === e : false)
    })
    const rate = $('edRate').value
    Array.prototype.forEach.call(document.querySelectorAll('[data-rate]'), c => {
      c.classList.toggle('on', c.getAttribute('data-rate') === rate)
    })
  }

  function recalcEditor() {
    const s = $('edSaveDate').value
    const e = $('edExpireDate').value
    const p = Number($('edPrincipal').value) || 0
    const r = Number($('edRate').value) || 0
    const months = s && e ? termMonthsLocal(s, e) : 0
    const c = calcLocal(p, r, months)
    $('edTerm').textContent = months > 0 ? termLabel(months) : '—'
    $('edInterest').textContent = moneyLocal(c.interest)
    $('edTotal').textContent = moneyLocal(c.total)
    syncPresetChips()
  }

  async function saveEditor() {
    const bankName = currentBankName()
    if (!bankName) return toast('请选择银行')
    const saveDate = $('edSaveDate').value
    const expireDate = $('edExpireDate').value
    if (!saveDate || !expireDate) return toast('请填写存入与到期日期')
    if (expireDate <= saveDate) return toast('到期日必须晚于存入日')
    const principal = Number($('edPrincipal').value)
    if (!(principal > 0)) return toast('本金必须大于 0')
    const ratePercent = Number($('edRate').value)
    if (!(ratePercent >= 0)) return toast('请填写年利率')

    const body = {
      bankName,
      saveDate,
      expireDate,
      principal,
      ratePercent,
      remark: $('edRemark').value.trim()
    }
    if (S.editing && S.members.length > 1 && $('edMember').value) {
      body.creatorId = Number($('edMember').value)
    }

    try {
      if (S.editing) await requ('/deposits/' + S.editing.id, { method: 'PUT', body })
      else await requ('/deposits', { method: 'POST', body })
      $('editorMask').style.display = 'none'
      await loadDeposits()
      setPage(S.page)
      toast('已保存')
    } catch (e) { dialog('保存失败', e.message, { cancelText: '关闭' }) }
  }

  // ---------------- 成员选择（批量修改添加人） ----------------
  function openMemberPick() {
    const ids = Object.keys(S.sel).map(Number)
    if (!ids.length) return toast('请先选择存单')
    if (S.members.length < 2) {
      return dialog('只有一个成员', '当前账本只有你一个人，添加人只能是你。可以让家人注册一个账号后再改。', { cancelText: '知道了' })
    }
    S.pickTarget = ids
    $('memberTitle').textContent = '选择添加人（' + ids.length + ' 笔）'
    $('memberPickList').innerHTML = S.members.map(m =>
      '<div class="member-row" data-pick="' + m.id + '" data-name="' + esc(m.displayName) + '" style="cursor:pointer">' +
      '<div class="avatar" style="width:34px;height:34px;font-size:13px">' + esc(m.initial) + '</div>' +
      '<div style="flex:1"><div class="nm">' + esc(m.displayName) +
      (m.isOwner ? ' <span class="tag owner">创建者</span>' : ' <span class="tag">成员</span>') +
      '</div></div><span style="color:#c2c9d6">›</span></div>'
    ).join('')
    $('memberMask').style.display = 'flex'
  }

  async function applyBatch(memberId, name) {
    const ok = await dialog('批量修改添加人', '把选中的 ' + S.pickTarget.length + ' 笔存单的添加人改为「' + name + '」？', { confirmText: '确认修改' })
    if (!ok) return
    try {
      await requ('/deposits/batch-creator', { method: 'POST', body: { ids: S.pickTarget, creatorId: memberId } })
      $('memberMask').style.display = 'none'
      exitSelect()
      await loadDeposits()
      renderList()
      toast('已修改 ' + S.pickTarget.length + ' 笔')
    } catch (e) { dialog('修改失败', e.message, { cancelText: '关闭' }) }
  }

  function exitSelect() {
    S.selMode = false
    S.sel = {}
    renderList()
  }

  // ---------------- 事件绑定 ----------------
  function bindEvents() {
    // 登录页
    $('tabLogin').addEventListener('click', () => {
      $('tabLogin').classList.add('on'); $('tabReg').classList.remove('on')
      $('loginForm').style.display = ''; $('regForm').style.display = 'none'
    })
    $('tabReg').addEventListener('click', () => {
      $('tabReg').classList.add('on'); $('tabLogin').classList.remove('on')
      $('regForm').style.display = ''; $('loginForm').style.display = 'none'
    })

    async function doLogin() {
      $('loginErr').textContent = ''
      try {
        await requ('/auth/login', { method: 'POST', body: { username: $('lgUser').value.trim(), password: $('lgPass').value } })
        await boot()
      } catch (e) { $('loginErr').textContent = e.message }
    }
    $('btnLogin').addEventListener('click', doLogin)
    $('lgPass').addEventListener('keydown', e => { if (e.key === 'Enter') doLogin() })

    async function doReg() {
      $('regErr').textContent = ''
      try {
        await requ('/auth/register', {
          method: 'POST',
          body: { username: $('rgUser').value.trim(), displayName: $('rgName').value.trim(), password: $('rgPass').value }
        })
        await boot()
      } catch (e) { $('regErr').textContent = e.message }
    }
    $('btnReg').addEventListener('click', doReg)

    // 底部导航
    $('tabbar').addEventListener('click', e => {
      const btn = e.target.closest('button[data-page]')
      if (btn) setPage(btn.getAttribute('data-page'))
    })

    // 日历
    $('calPrev').addEventListener('click', () => {
      S.calMonth = new Date(S.calMonth.getFullYear(), S.calMonth.getMonth() - 1, 1)
      renderCalendar()
    })
    $('calNext').addEventListener('click', () => {
      S.calMonth = new Date(S.calMonth.getFullYear(), S.calMonth.getMonth() + 1, 1)
      renderCalendar()
    })
    $('calToday').addEventListener('click', () => {
      const t = new Date()
      S.calMonth = startOfMonth(t)
      S.selectedDay = todayStr()
      renderCalendar()
    })
    $('calGrid').addEventListener('click', e => {
      const cell = e.target.closest('.day[data-date]')
      if (!cell) return
      S.selectedDay = cell.getAttribute('data-date')
      renderCalendar()
    })
    $('calAdd').addEventListener('click', () => openEditor(null))

    // 台账
    $('listTabs').addEventListener('click', e => {
      const btn = e.target.closest('button[data-tab]')
      if (!btn) return
      S.tab = btn.getAttribute('data-tab')
      Array.prototype.forEach.call($('listTabs').children, b => b.classList.toggle('on', b === btn))
      exitSelect()
      renderList()
    })
    $('fBank').addEventListener('change', e => { S.fBank = e.target.value; exitSelect(); renderList() })
    $('fYear').addEventListener('change', e => { S.fYear = e.target.value; exitSelect(); renderList() })
    $('fMonth').addEventListener('change', e => { S.fMonth = e.target.value; exitSelect(); renderList() })
    $('btnReset').addEventListener('click', () => {
      S.fBank = S.fYear = S.fMonth = ''
      $('fBank').value = ''; $('fYear').value = ''; $('fMonth').value = ''
      exitSelect(); renderList()
    })

    $('btnBulk').addEventListener('click', () => {
      if (!filteredList().length) return toast('当前没有存单')
      S.selMode = true
      S.sel = {}
      renderList()
    })
    $('selAll').addEventListener('click', () => {
      const list = filteredList()
      if (Object.keys(S.sel).length === list.length) S.sel = {}
      else list.forEach(d => { S.sel[d.id] = true })
      renderList()
    })
    $('selExit').addEventListener('click', exitSelect)
    $('bulkCancel').addEventListener('click', exitSelect)
    $('bulkApply').addEventListener('click', openMemberPick)

    $('listBox').addEventListener('click', async e => {
      const card = e.target.closest('.card.deposit')
      const sel = e.target.closest('[data-sel]')
      const editBtn = e.target.closest('[data-edit]')
      const delBtn = e.target.closest('[data-del]')

      if (S.selMode) {
        if (card) {
          const id = Number(card.getAttribute('data-id'))
          if (S.sel[id]) delete S.sel[id]
          else S.sel[id] = true
          renderList()
        }
        return
      }
      if (editBtn) return openEditor(Number(editBtn.getAttribute('data-edit')))
      if (delBtn) {
        const id = Number(delBtn.getAttribute('data-del'))
        const d = S.deposits.find(x => x.id === id)
        const ok = await dialog('删除存单', '确认删除「' + (d ? d.bankName + ' ' + moneyLocal(d.principal) + ' 元' : '这笔存单') + '」？删除后无法恢复。', { confirmText: '删除', danger: true })
        if (!ok) return
        try {
          await requ('/deposits/' + id, { method: 'DELETE' })
          await loadDeposits()
          renderList(); renderCalendar()
          toast('已删除')
        } catch (err) { dialog('删除失败', err.message, { cancelText: '关闭' }) }
      }
    })

    // 日历 / 统计里的卡片同样支持编辑
    $('dayPanel').addEventListener('click', async e => {
      const editBtn = e.target.closest('[data-edit]')
      const delBtn = e.target.closest('[data-del]')
      if (editBtn) return openEditor(Number(editBtn.getAttribute('data-edit')))
      if (delBtn) {
        const id = Number(delBtn.getAttribute('data-del'))
        const d = S.deposits.find(x => x.id === id)
        const ok = await dialog('删除存单', '确认删除「' + (d ? d.bankName + ' ' + moneyLocal(d.principal) + ' 元' : '这笔存单') + '」？删除后无法恢复。', { confirmText: '删除', danger: true })
        if (!ok) return
        try {
          await requ('/deposits/' + id, { method: 'DELETE' })
          await loadDeposits()
          renderCalendar()
          toast('已删除')
        } catch (err) { dialog('删除失败', err.message, { cancelText: '关闭' }) }
      }
    })

    // 统计
    $('statMode').addEventListener('click', e => {
      const btn = e.target.closest('button[data-mode]')
      if (!btn) return
      S.statMode = btn.getAttribute('data-mode')
      Array.prototype.forEach.call($('statMode').children, b => b.classList.toggle('on', b === btn))
      renderStats()
    })
    $('sYear').addEventListener('change', e => { S.sYear = e.target.value; renderStats() })
    $('sMonth').addEventListener('change', e => { S.sMonth = e.target.value; renderStats() })
    $('sDay').addEventListener('change', e => { S.sDay = e.target.value; renderStats() })

    // 编辑弹层
    $('edBanks').addEventListener('click', e => {
      const cell = e.target.closest('[data-bank]')
      if (!cell) return
      S.edBankKey = cell.getAttribute('data-bank')
      const b = S.banks.find(x => x.id === S.edBankKey)
      $('edBankCustom').value = b ? b.name : ''
      Array.prototype.forEach.call($('edBanks').children, c => c.classList.toggle('on', c === cell))
    })
    $('edBankCustom').addEventListener('input', () => {
      S.edBankKey = ''
      Array.prototype.forEach.call($('edBanks').children, c => c.classList.remove('on'))
    })
    $('edSaveDate').addEventListener('change', recalcEditor)
    $('edExpireDate').addEventListener('change', recalcEditor)
    $('edPrincipal').addEventListener('input', recalcEditor)
    $('edRate').addEventListener('input', recalcEditor)

    $('edPresets').addEventListener('click', e => {
      const chip = e.target.closest('[data-term]')
      if (!chip) return
      const months = Number(chip.getAttribute('data-term'))
      const s = $('edSaveDate').value || todayStr()
      $('edExpireDate').value = addMonths(s, months)
      recalcEditor()
    })
    $('edRatePresets').addEventListener('click', e => {
      const chip = e.target.closest('[data-rate]')
      if (!chip) return
      $('edRate').value = chip.getAttribute('data-rate')
      recalcEditor()
    })

    $('edSave').addEventListener('click', saveEditor)
    $('edDelete').addEventListener('click', async () => {
      if (!S.editing) return
      const ok = await dialog('删除存单', '确认删除这笔存单？删除后无法恢复。', { confirmText: '删除', danger: true })
      if (!ok) return
      try {
        await requ('/deposits/' + S.editing.id, { method: 'DELETE' })
        $('editorMask').style.display = 'none'
        await loadDeposits()
        setPage(S.page)
        toast('已删除')
      } catch (e2) { dialog('删除失败', e2.message, { cancelText: '关闭' }) }
    })

    // 弹层关闭
    document.addEventListener('click', e => {
      const close = e.target.getAttribute && e.target.getAttribute('data-close')
      if (!close) return
      if (close === 'editor') $('editorMask').style.display = 'none'
      if (close === 'member') $('memberMask').style.display = 'none'
    })
    $('editorMask').addEventListener('click', e => { if (e.target === $('editorMask')) $('editorMask').style.display = 'none' })
    $('memberMask').addEventListener('click', e => { if (e.target === $('memberMask')) $('memberMask').style.display = 'none' })
    $('memberPickList').addEventListener('click', e => {
      const row = e.target.closest('[data-pick]')
      if (!row) return
      applyBatch(Number(row.getAttribute('data-pick')), row.getAttribute('data-name'))
    })
  }

  // 启动
  bindEvents()
  boot()
})()
