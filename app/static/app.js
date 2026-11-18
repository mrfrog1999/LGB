/* 建行理财收益监控 · 前端 */
(() => {
  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => [...r.querySelectorAll(s)];
  const money = n => (n == null ? '--' : Number(n).toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 }));
  const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

  const state = { data: null, expanded: new Set(), busy: false };

  async function api(path, opts = {}) {
    const res = await fetch(path, { headers: { 'Content-Type': 'application/json' }, ...opts });
    const text = await res.text();
    let body = null;
    try { body = text ? JSON.parse(text) : null; } catch { body = { detail: text }; }
    if (!res.ok) throw new Error((body && (body.detail || body.error)) || `请求失败 ${res.status}`);
    return body;
  }

  /* ---------- toast ---------- */
  function toast(msg, err = false) {
    const el = document.createElement('div');
    el.className = 'toast' + (err ? ' err' : '');
    el.textContent = msg;
    $('#toastRoot').appendChild(el);
    setTimeout(() => { el.style.opacity = '0'; el.style.transition = 'opacity .3s'; }, 2200);
    setTimeout(() => el.remove(), 2600);
  }

  /* ---------- modal ---------- */
  function openModal(html) {
    const root = $('#modalRoot');
    root.innerHTML = html;
    root.hidden = false;
    root.onclick = e => { if (e.target === root) closeModal(); };
    const close = $('.modal-close', root);
    if (close) close.onclick = closeModal;
    return root;
  }
  function closeModal() {
    const root = $('#modalRoot');
    root.hidden = true;
    root.innerHTML = '';
  }
  document.addEventListener('keydown', e => { if (e.key === 'Escape') closeModal(); });

  function confirmModal(title, text, okText, onOk) {
    const root = openModal(`
      <div class="modal" style="max-width:420px">
        <div class="modal-head"><div><h3>${esc(title)}</h3><p>${esc(text)}</p></div>
          <button class="modal-close" aria-label="关闭">×</button></div>
        <div class="modal-foot">
          <button class="btn ghost" id="mCancel">取消</button>
          <button class="btn primary" id="mOk">${esc(okText || '确定')}</button>
        </div>
      </div>`);
    $('#mCancel', root).onclick = closeModal;
    $('#mOk', root).onclick = async () => { closeModal(); await onOk(); };
  }

  /* ---------- 渲染 ---------- */
  function renderHero() {
    const t = (state.data && state.data.total) || {};
    $('#totalBalance').textContent = money(t.balance);
    $('#prodCount').textContent = `${t.count || 0} 个`;
    $('#totalInterest').textContent = `+${money(t.interest)}`;
    $('#totalAnnual').textContent = `${Number(t.annualized || 0).toFixed(2)}%`;
    const next = state.data && state.data.next_run;
    $('#nextRun').textContent = next ? `每日 ${state.data.fetch_time} 自动同步` : '定时同步中';
    $('#updateHint').textContent = state.data ? `数据更新于 ${state.data.server_time}` : '';
  }

  function productCard(p) {
    const c = p.calc || {};
    const lat = p.latest || {};
    const shortName = String(p.name || '').replace(/[（(]代销建信理财[）)]/g, '').trim();
    const rate = lat.rate != null ? Number(lat.rate).toFixed(2) : '--';
    const dateStr = lat.date ? lat.date.slice(5) : '--';
    const open = state.expanded.has(p.code);

    const stats = c.days
      ? `<div class="stat"><label>当前本息</label><strong>¥${money(c.balance)}</strong></div>
         <div class="stat"><label>累计收益</label><strong class="gain">+¥${money(c.interest)}</strong></div>
         <div class="stat"><label>持有天数</label><strong>${c.days} 天</strong></div>
         <div class="stat"><label>日均收益</label><strong>¥${money(c.interest / c.days)}</strong></div>`
      : `<div class="stat"><label>当前本息</label><strong>--</strong></div>
         <div class="stat"><label>累计收益</label><strong class="gain">--</strong></div>
         <div class="stat"><label>持有天数</label><strong>--</strong></div>
         <div class="stat"><label>日均收益</label><strong>--</strong></div>`;

    const startVal = p.start_date || (c.start_date || '');
    return `
    <article class="prod" data-code="${esc(p.code)}">
      <div class="prod-head">
        <div class="prod-title">
          <h3>${esc(shortName || p.name)}</h3>
          <div class="code">${esc(p.code)}${p.has_data ? ` · ${c.data_days || 0} 天数据` : ' · 暂无数据'}</div>
        </div>
        <div class="prod-rate">
          <b>${rate}</b><span class="pct">%</span>
          <small>七日年化 · ${dateStr}</small>
        </div>
      </div>
      <div class="prod-grid">${stats}</div>
      <div class="prod-edit">
        <label class="field">本金
          <input class="principal" type="number" min="0" step="100" value="${p.principal ?? ''}" placeholder="100000">
        </label>
        <label class="field">买入日
          <input class="startdate" type="date" value="${esc(startVal)}" max="${(state.data && state.data.server_time || '').slice(0, 10)}">
        </label>
        <div class="prod-btns">
          <button class="mini save">保存并重算</button>
          <button class="mini expand">${open ? '收起' : '明细'}</button>
          <button class="mini sync">同步</button>
          <button class="mini danger del">移除</button>
        </div>
      </div>
      <div class="prod-detail" ${open ? '' : 'hidden'}></div>
    </article>`;
  }

  function render() {
    renderHero();
    const list = $('#productList');
    const products = (state.data && state.data.products) || [];
    if (!products.length) {
      list.innerHTML = `<div class="empty"><b>还没有监控任何产品</b>
        <small>点击上方「搜索产品」，输入「建信宝按日开放式」等名称，勾选你要的期次即可加入监控。</small></div>`;
      return;
    }
    list.innerHTML = products.map(productCard).join('');
    products.filter(p => state.expanded.has(p.code)).forEach(p => loadDetail(p.code));
  }

  /* ---------- 明细 ---------- */
  async function loadDetail(code) {
    const card = $(`.prod[data-code="${CSS.escape(code)}"]`);
    if (!card) return;
    const box = $('.prod-detail', card);
    box.hidden = false;
    box.innerHTML = '<div class="hint" style="padding:10px 0">加载中…</div>';
    try {
      const inp = $('.principal', card);
      const sd = $('.startdate', card);
      const q = new URLSearchParams();
      if (inp && inp.value) q.set('principal', inp.value);
      if (sd && sd.value) q.set('start_date', sd.value);
      const c = await api(`/api/products/${encodeURIComponent(code)}/calc?${q}`);
      box.innerHTML = detailHtml(c);
      drawChart($('.chart', box), c.daily);
    } catch (e) {
      box.innerHTML = `<div class="hint" style="padding:10px 0;color:#b3261e">${esc(e.message)}</div>`;
    }
  }

  function detailHtml(c) {
    const rows = (c.daily || []).slice().reverse().map(d => `
      <tr class="${d.filled ? 'filled' : ''}">
        <td>${d.date}${d.filled ? ' *' : ''}</td>
        <td>${Number(d.rate).toFixed(2)}%</td>
        <td>¥${money(d.daily_interest)}</td>
        <td>¥${money(d.balance)}</td>
      </tr>`).join('');
    return `
      <div class="detail-head">
        <h4>本息走势</h4>
        <span>${c.start_date} → ${c.end_date} · ${c.days} 天 · 区间年化 ${Number(c.annualized).toFixed(2)}%</span>
      </div>
      <svg class="chart" viewBox="0 0 600 150" preserveAspectRatio="none"></svg>
      <div class="detail-head" style="margin-top:4px">
        <h4>逐日明细</h4>
        <span>带 * 为未披露日，沿用前值</span>
      </div>
      <div class="table-wrap">
        <table>
          <thead><tr><th>日期</th><th>七日年化</th><th>当日收益</th><th>余额</th></tr></thead>
          <tbody>${rows || '<tr><td colspan="4" style="text-align:center;color:#9aa5a1">暂无数据</td></tr>'}</tbody>
        </table>
      </div>`;
  }

  function drawChart(svg, daily) {
    if (!svg || !daily || daily.length < 2) return;
    const W = 600, H = 150, P = 8;
    const vals = daily.map(d => d.balance);
    const min = Math.min(...vals), max = Math.max(...vals);
    const span = max - min || 1;
    const x = i => P + (W - 2 * P) * (i / (daily.length - 1));
    const y = v => H - P - (H - 2 * P) * ((v - min) / span);
    const pts = vals.map((v, i) => `${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(' ');
    svg.innerHTML = `
      <defs>
        <linearGradient id="g1" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stop-color="#187a67" stop-opacity=".34"/>
          <stop offset="100%" stop-color="#187a67" stop-opacity="0"/>
        </linearGradient>
      </defs>
      <polygon points="${P},${H - P} ${pts} ${W - P},${H - P}" fill="url(#g1)"/>
      <polyline points="${pts}" fill="none" stroke="#0f5c4e" stroke-width="2.2"
        stroke-linejoin="round" vector-effect="non-scaling-stroke"/>`;
  }

  /* ---------- 搜索 ---------- */
  async function doSearch() {
    const q = ($('#searchInput').value || '').trim();
    const btn = $('#searchBtn');
    btn.disabled = true; btn.textContent = '搜索中…';
    try {
      const res = await api(`/api/search?q=${encodeURIComponent(q)}`);
      showResults(res);
    } catch (e) {
      toast(e.message, true);
    } finally {
      btn.disabled = false; btn.textContent = '搜索产品';
    }
  }

  function showResults(res) {
    const items = res.items || [];
    const root = openModal(`
      <div class="modal">
        <div class="modal-head">
          <div><h3>选择要监控的产品</h3>
            <p>检索「${esc(res.keyword)}」共 ${items.length} 个产品，勾选后加入监控</p></div>
          <button class="modal-close" aria-label="关闭">×</button>
        </div>
        <div class="modal-body">
          ${items.length ? items.map(i => `
            <div class="result-row ${i.subscribed ? 'on' : ''}" data-code="${esc(i.code)}"
                 data-name="${esc(i.name)}" data-date="${esc(i.date)}" ${i.subscribed ? 'data-on="1"' : ''}>
              <div class="box">${i.subscribed ? '✓' : ''}</div>
              <div class="txt"><b>${esc(i.name)}</b>
                <small>${esc(i.code)} · 披露至 ${esc(i.date)}</small></div>
              <span class="badge">${i.subscribed ? '已监控' : '添加'}</span>
            </div>`).join('')
          : '<div style="padding:30px;text-align:center;color:#9aa5a1">没有找到相关产品，换个关键词试试</div>'}
        </div>
        <div class="modal-foot">
          <button class="btn ghost" id="mCancel">关闭</button>
          <button class="btn primary" id="mAdd">加入监控</button>
        </div>
      </div>`);

    $('#mCancel', root).onclick = closeModal;
    $$('.result-row', root).forEach(row => {
      row.onclick = () => {
        if (row.dataset.on === '1') { toast('该产品已在监控中'); return; }
        const on = row.classList.toggle('on');
        $('.box', row).textContent = on ? '✓' : '';
        const badge = $('.badge', row);
        badge.textContent = on ? '待添加' : '添加';
      };
    });

    $('#mAdd', root).onclick = async () => {
      const picked = $$('.result-row.on:not([data-on])', root);
      if (!picked.length) { toast('请先勾选产品'); return; }
      const btn = $('#mAdd', root);
      btn.disabled = true; btn.textContent = '添加中…';
      let ok = 0, fail = 0;
      for (const row of picked) {
        try {
          await api('/api/products', {
            method: 'POST',
            body: JSON.stringify({ code: row.dataset.code, name: row.dataset.name })
          });
          await api(`/api/products/${encodeURIComponent(row.dataset.code)}/sync`, { method: 'POST' });
          ok++;
        } catch (e) { fail++; console.warn(e); }
      }
      closeModal();
      await load();
      toast(fail ? `已添加 ${ok} 个，${fail} 个同步失败（可手动重试）` : `已添加 ${ok} 个产品`, !!fail);
    };
  }

  /* ---------- 操作 ---------- */
  async function saveProduct(card) {
    const code = card.dataset.code;
    const principal = $('.principal', card).value.trim();
    const start = $('.startdate', card).value.trim();
    const payload = {};
    if (principal !== '') payload.principal = Number(principal);
    if (start) payload.start_date = start;
    const btn = $('.save', card);
    btn.disabled = true;
    try {
      await api(`/api/products/${encodeURIComponent(code)}`, { method: 'PATCH', body: JSON.stringify(payload) });
      await load();
      toast('已保存并重新计算');
    } catch (e) { toast(e.message, true); }
    finally { btn.disabled = false; }
  }

  async function syncOne(card) {
    const code = card.dataset.code;
    const btn = $('.sync', card);
    btn.disabled = true; btn.textContent = '同步中';
    try {
      await api(`/api/products/${encodeURIComponent(code)}/sync`, { method: 'POST' });
      await load();
      toast('已同步到最新披露');
    } catch (e) { toast(e.message, true); }
    finally { btn.disabled = false; btn.textContent = '同步'; }
  }

  async function syncAll() {
    const btn = $('#syncBtn');
    btn.disabled = true; btn.textContent = '同步中…';
    try {
      const r = await api('/api/sync', { method: 'POST' });
      const bad = (r.results || []).filter(x => x.error);
      await load();
      toast(bad.length ? `同步完成，${bad.length} 个产品失败` : '全部同步完成', !!bad.length);
    } catch (e) { toast(e.message, true); }
    finally { btn.disabled = false; btn.textContent = '同步全部'; }
  }

  async function testMail() {
    const btn = $('#mailBtn');
    btn.disabled = true; btn.textContent = '发送中…';
    try {
      await api('/api/test-mail', { method: 'POST' });
      toast('测试邮件已发送');
    } catch (e) { toast(e.message, true); }
    finally { btn.disabled = false; btn.textContent = '发送测试邮件'; }
  }

  function bindList() {
    $('#productList').addEventListener('click', e => {
      const card = e.target.closest('.prod');
      if (!card) return;
      if (e.target.matches('.save')) saveProduct(card);
      else if (e.target.matches('.sync')) syncOne(card);
      else if (e.target.matches('.expand')) {
        const code = card.dataset.code;
        const box = $('.prod-detail', card);
        const opening = box.hidden;
        if (opening) { state.expanded.add(code); e.target.textContent = '收起'; loadDetail(code); }
        else { state.expanded.delete(code); box.hidden = true; e.target.textContent = '明细'; }
      } else if (e.target.matches('.del')) {
        const code = card.dataset.code;
        confirmModal('移除监控', '将同时删除该产品已同步的历史数据，确定移除？', '移除', async () => {
          try {
            await api(`/api/products/${encodeURIComponent(code)}`, { method: 'DELETE' });
            state.expanded.delete(code);
            await load();
            toast('已移除');
          } catch (err) { toast(err.message, true); }
        });
      }
    });
  }

  /* ---------- 启动 ---------- */
  async function load() {
    try {
      state.data = await api('/api/overview');
      render();
    } catch (e) {
      toast('加载失败：' + e.message, true);
      console.error(e);
    }
  }

  document.addEventListener('DOMContentLoaded', () => {
    bindList();
    $('#searchBtn').onclick = doSearch;
    $('#searchInput').addEventListener('keydown', e => { if (e.key === 'Enter') doSearch(); });
    $('#syncBtn').onclick = syncAll;
    $('#mailBtn').onclick = testMail;
    load();
    setInterval(load, 120000);
  });
})();
