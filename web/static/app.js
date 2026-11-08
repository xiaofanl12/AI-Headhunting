/* ============================================================
   AI 猎头助手 · 前端逻辑（零依赖）
   路由 / API / 渲染 / 主题 / 抽屉 / Toast / 轻量 Markdown
   ============================================================ */
'use strict';

const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));

/* ---------------- 基础工具 ---------------- */
const escapeHtml = (s) => String(s == null ? '' : s)
  .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
  .replace(/"/g, '&quot;').replace(/'/g, '&#39;');

const AVATARS = [
  'linear-gradient(135deg,#6366f1,#8b5cf6)',
  'linear-gradient(135deg,#0ea5e9,#22d3ee)',
  'linear-gradient(135deg,#f59e0b,#f97316)',
  'linear-gradient(135deg,#10b981,#14b8a6)',
  'linear-gradient(135deg,#ec4899,#f43f5e)',
  'linear-gradient(135deg,#8b5cf6,#d946ef)',
  'linear-gradient(135deg,#0ea5e9,#6366f1)',
];

function avatarOf(name) {
  const s = String(name || '?');
  let h = 0;
  for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) % 9973;
  return AVATARS[h % AVATARS.length];
}

const STATUS_CLASS = {
  '待筛选': '', '待推荐': 'info', '已推荐': 'brand', '已联系': 'warn',
  '面试中': 'info', '已入职': 'ok', '已放弃': 'danger',
};

function pill(status) {
  const cls = STATUS_CLASS[status] ?? '';
  return `<span class="pill ${cls}">${escapeHtml(status || '未知')}</span>`;
}

/* ---------------- Toast ---------------- */
const ICONS = { ok: 'i-check', err: 'i-alert', info: 'i-spark' };

function toast(message, type = 'info', title = '') {
  const wrap = $('#toastWrap');
  const el = document.createElement('div');
  el.className = `toast ${type}`;
  el.innerHTML = `
    <span class="tk"><svg><use href="#${ICONS[type] || 'i-spark'}"/></svg></span>
    <div>${title ? `<b>${escapeHtml(title)}</b>` : ''}<span>${escapeHtml(message)}</span></div>`;
  wrap.appendChild(el);
  setTimeout(() => {
    el.classList.add('out');
    setTimeout(() => el.remove(), 320);
  }, type === 'err' ? 5200 : 3600);
}

/* ---------------- API ---------------- */
async function api(path, options = {}) {
  const opt = { headers: { 'Content-Type': 'application/json' }, ...options };
  opt.headers = { ...opt.headers };
  const token = localStorage.getItem('headhunter.token');
  if (token) opt.headers['X-Auth-Token'] = token;
  if (opt.body && typeof opt.body !== 'string') opt.body = JSON.stringify(opt.body);
  const res = await fetch(path, opt);
  let json = null;
  try { json = await res.json(); } catch (e) { /* 非 JSON */ }
  if (res.status === 401 && !path.startsWith('/api/auth')) {
    localStorage.removeItem('headhunter.token');
    window.location.href = '/login';
    throw new Error('登录已过期，请重新登录');
  }
  if (!res.ok || !json || json.ok === false) {
    const msg = (json && json.error) || `请求失败（HTTP ${res.status}）`;
    throw new Error(msg);
  }
  return json.data;
}

const withLoading = async (btn, fn) => {
  if (!btn) return fn();
  const html = btn.innerHTML;
  btn.disabled = true;
  btn.innerHTML = `<span class="typing"><i></i><i></i><i></i></span>`;
  try { return await fn(); } finally { btn.disabled = false; btn.innerHTML = html; }
};

/* ---------------- 轻量 Markdown ---------------- */
function inlineMd(text) {
  let s = escapeHtml(text);
  s = s.replace(/`([^`]+)`/g, '<code>$1</code>');
  s = s.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
  s = s.replace(/(^|[\s(])\*([^*\n]+)\*/g, '$1<em>$2</em>');
  // 简历链接 → 抽屉
  s = s.replace(/\[([^\]]+)\]\(\/resume\/(\d+)\)/g,
    '<a href="javascript:void(0)" class="cand-link" data-cand="$2">$1</a>');
  s = s.replace(/\[([^\]]+)\]\((https?:[^)]+)\)/g,
    '<a href="$2" target="_blank" rel="noopener">$1</a>');
  return s;
}

function mdToHtml(src) {
  const lines = String(src || '').replace(/\r/g, '').split('\n');
  const out = [];
  let i = 0;
  const isTable = (l) => /^\s*\|.*\|\s*$/.test(l);

  while (i < lines.length) {
    const raw = lines[i];
    const line = raw.trim();

    if (!line) { i++; continue; }
    if (/^(-{3,}|\*{3,})$/.test(line)) { out.push('<hr>'); i++; continue; }

    const h = line.match(/^(#{1,4})\s+(.*)$/);
    if (h) { const lv = Math.min(h[1].length + 1, 4); out.push(`<h${lv}>${inlineMd(h[2])}</h${lv}>`); i++; continue; }

    if (line.startsWith('>')) {
      const buf = [];
      while (i < lines.length && lines[i].trim().startsWith('>')) {
        buf.push(lines[i].trim().replace(/^>\s?/, '')); i++;
      }
      out.push(`<blockquote>${inlineMd(buf.join(' '))}</blockquote>`);
      continue;
    }

    if (isTable(line)) {
      const rows = [];
      while (i < lines.length && isTable(lines[i])) { rows.push(lines[i].trim()); i++; }
      const cells = rows.map((r) => r.replace(/^\||\|$/g, '').split('|').map((c) => c.trim()));
      const body = cells.filter((c) => !c.every((x) => /^:?-{2,}:?$/.test(x)));
      if (body.length) {
        const head = body[0];
        out.push('<table><thead><tr>' + head.map((c) => `<th>${inlineMd(c)}</th>`).join('') + '</tr></thead><tbody>'
          + body.slice(1).map((r) => '<tr>' + r.map((c) => `<td>${inlineMd(c)}</td>`).join('') + '</tr>').join('')
          + '</tbody></table>');
      }
      continue;
    }

    if (/^[-*+]\s+/.test(line)) {
      const buf = [];
      while (i < lines.length && /^[-*+]\s+/.test(lines[i].trim())) {
        buf.push(lines[i].trim().replace(/^[-*+]\s+/, '')); i++;
      }
      out.push('<ul>' + buf.map((b) => `<li>${inlineMd(b)}</li>`).join('') + '</ul>');
      continue;
    }

    if (/^\d+[.、]\s+/.test(line)) {
      const buf = [];
      while (i < lines.length && /^\d+[.、]\s+/.test(lines[i].trim())) {
        buf.push(lines[i].trim().replace(/^\d+[.、]\s+/, '')); i++;
      }
      out.push('<ol>' + buf.map((b) => `<li>${inlineMd(b)}</li>`).join('') + '</ol>');
      continue;
    }

    const buf = [];
    while (i < lines.length && lines[i].trim()
      && !/^(#{1,4})\s/.test(lines[i].trim())
      && !isTable(lines[i]) && !/^[-*+]\s+/.test(lines[i].trim())
      && !/^\d+[.、]\s+/.test(lines[i].trim()) && !lines[i].trim().startsWith('>')) {
      buf.push(lines[i].trim()); i++;
    }
    out.push(`<p>${inlineMd(buf.join(' '))}</p>`);
  }
  return out.join('');
}

/* ---------------- 主题 ---------------- */
function initTheme() {
  const saved = localStorage.getItem('hh-theme');
  const prefersDark = window.matchMedia('(prefers-color-scheme: dark)').matches;
  const theme = saved || (prefersDark ? 'dark' : 'light');
  document.documentElement.dataset.theme = theme;
  $('#themeBtn').addEventListener('click', () => {
    const next = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark';
    document.documentElement.dataset.theme = next;
    localStorage.setItem('hh-theme', next);
  });
}

/* ---------------- 全局状态 ---------------- */
const state = {
  view: 'home',
  overview: null,
  candidates: [],
  status: '全部',
  q: '',
  positions: [],
  companies: [],
  analyses: [],
  draft: null,
  chatBusy: false,
  weights: null,
  decisionFilter: '',
  candPage: 1,
  me: null,
  candPageSize: 20,
  eduFilter: '',
  expFilter: '',
};

const PAGE_META = {
  home: ['工作台', '用一句话完成找人、评估、触达'],
  talent: ['人才库', '候选人档案、招聘漏斗与一键导出'],
  jobs: ['在招职位', '发布职位，匹配引擎自动挑选'],
  companies: ['公司库', '背调留痕，来源可追溯'],
  analysis: ['公司分析', '5 维评分 + 合规风险核查'],
  strategy: ['匹配策略', '权重可调 · AI 过滤标记 · 人工决定邀约'],
  settings: ['账号中心', '资料、密码与会话管理'],
};

/* ---------------- 路由 ---------------- */
function currentView() {
  const hash = location.hash.replace(/^#\//, '').trim();
  return PAGE_META[hash] ? hash : 'home';
}

function go(view) {
  state.view = view;
  $$('.view').forEach((v) => v.classList.toggle('is-active', v.id === `view-${view}`));
  $$('.nav-item').forEach((n) => n.classList.toggle('is-active', n.dataset.view === view));
  const [title, sub] = PAGE_META[view];
  $('#pageTitle').textContent = title;
  $('#pageSub').textContent = sub;
  $('.views').scrollTop = 0;
  loadView(view);
}

function loadView(view) {
  if (view === 'talent') loadOverview().then(renderStats).catch(() => {});
  if (view === 'talent') loadCandidates();
  if (view === 'jobs') loadPositions();
  if (view === 'companies') loadCompanies();
  if (view === 'analysis') loadAnalyses();
  if (view === 'strategy') loadStrategy();
  if (view === 'settings') loadAccount().catch((e) => toast(e.message, 'err', '账号信息读取失败'));
  if (view === 'home') loadRail();
}

/* ---------------- 概览 / 统计 ---------------- */
async function loadOverview() {
  const data = await api('/api/overview');
  state.overview = data;
  $('#navCand').textContent = data.cards.candidates;
  $('#navPos').textContent = data.cards.positions_open;
  return data;
}

async function loadRail() {
  try {
    const d = state.overview || await loadOverview();
    const rows = $('#railStats').querySelectorAll('.mini-row b');
    rows[0].textContent = d.cards.candidates;
    rows[1].textContent = d.cards.positions_open;
    rows[2].textContent = d.cards.companies;
  } catch (e) { /* 静默 */ }
}

function sparkline(values, color = 'var(--brand)') {
  const vs = values.length ? values : [1, 1];
  const max = Math.max(...vs, 1);
  const w = 62, h = 24, step = w / Math.max(vs.length - 1, 1);
  const pts = vs.map((v, i) => `${(i * step).toFixed(1)},${(h - (v / max) * (h - 4) - 2).toFixed(1)}`).join(' ');
  return `<svg class="stat-spark" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none">
    <polyline points="${pts}" fill="none" stroke="${color}" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" opacity=".75"/>
  </svg>`;
}

function renderStats(d) {
  const c = d.cards;
  const cards = [
    { label: '候选人总数', value: c.candidates, note: `真实语料 ${c.real} / 示例 ${c.sample}`, icon: 'i-users', spark: d.funnel.map((f) => f.value) },
    { label: '在招职位', value: c.positions_open, note: `共 ${c.positions_total} 个，含已关闭`, icon: 'i-brief', spark: d.funnel.map((f) => f.value + 1) },
    { label: '合作公司', value: c.companies, note: '百科 / 企查查 / AI 收录', icon: 'i-building', spark: d.funnel.map((f) => f.value + 2) },
    { label: '已发邀约', value: c.sent, note: `平均匹配分 ${c.avg_score || 0}`, icon: 'i-mail', spark: d.funnel.map((f) => f.value + 3) },
  ];
  $('#statGrid').innerHTML = cards.map((s, i) => `
    <div class="stat" style="animation:viewIn .4s var(--ease) ${i * 40}ms both">
      <div class="stat-top">
        <span class="stat-label">${s.label}</span>
        <span class="stat-ico"><svg><use href="#${s.icon}"/></svg></span>
      </div>
      <div class="stat-value">${s.value}</div>
      <div class="stat-note">${escapeHtml(s.note)}</div>
      ${sparkline(s.spark, i === 3 ? 'var(--ok)' : 'var(--brand)')}
    </div>`).join('');

  // 漏斗
  const max = Math.max(...d.funnel.map((f) => f.value), 1);
  const total = d.funnel.reduce((a, b) => a + b.value, 0);
  $('#funnelTotal').textContent = `${total} 人`;
  $('#funnel').innerHTML = d.funnel.map((f, i) => {
    const prev = i ? d.funnel[i - 1].value : 0;
    const rate = i ? (prev ? Math.round((f.value / prev) * 100) : 0) : 100;
    return `<div class="funnel-row">
      <span class="fl">${f.label}</span>
      <div class="track"><div class="fill" style="width:${Math.max((f.value / max) * 100, f.value ? 3 : 0)}%"></div></div>
      <span class="fv">${f.value}</span>
      <span class="frate">${i ? rate + '%' : ''}</span>
    </div>`;
  }).join('');
  $('#funnelRates').innerHTML = `
    <span>推送→触达 <b>${d.rates.push_to_touch}%</b></span>
    <span>触达→面试 <b>${d.rates.touch_to_interview}%</b></span>
    <span>投递转化 <b>${d.rates.apply_convert}%</b></span>`;

  // 环形图
  const totalSrc = d.sources.reduce((a, b) => a + b.value, 0) || 1;
  const C = 2 * Math.PI * 46;
  let offset = 0;
  const colors = ['#6366f1', '#22d3ee'];
  const segs = d.sources.map((s, i) => {
    const len = (s.value / totalSrc) * C;
    const el = `<circle class="seg" cx="60" cy="60" r="46" stroke="${colors[i % 2]}"
      stroke-dasharray="${len} ${C - len}" stroke-dashoffset="${-offset}"/>`;
    offset += len;
    return el;
  }).join('');
  $('#donut').innerHTML = `<circle class="track" cx="60" cy="60" r="46"/>${segs}`;
  $('#donutLegend').innerHTML = d.sources.map((s, i) => `
    <div class="legend-item"><i style="background:${colors[i % 2]}"></i>${s.label}
      <em>${Math.round((s.value / totalSrc) * 100)}%</em><b>${s.value}</b></div>`).join('');
}

/* ---------------- 候选人 ---------------- */
const STATUS_LIST = ['全部', '待筛选', '待推荐', '已推荐', '已联系', '面试中', '已入职', '已放弃'];

function renderChips() {
  $('#statusChips').innerHTML = STATUS_LIST.map((s) =>
    `<button class="chip-btn ${state.status === s ? 'on' : ''}" data-status="${s}">${s}</button>`).join('');
  $$('#statusChips .chip-btn').forEach((b) => b.addEventListener('click', () => {
    state.status = b.dataset.status;
    renderChips();
    loadCandidates();
  }));
}

function skeletonRows(n = 6) {
  return Array.from({ length: n }).map(() => `
    <tr><td><div class="sk-row"><div class="skeleton" style="width:30px;height:30px;border-radius:9px"></div>
      <div style="flex:1"><div class="skeleton" style="width:60%"></div></div></div></td>
      <td><div class="skeleton" style="width:44px"></div></td>
      <td><div class="skeleton" style="width:30px"></div></td>
      <td><div class="skeleton" style="width:120px"></div></td>
      <td><div class="skeleton" style="width:50px"></div></td>
      <td><div class="skeleton" style="width:80px"></div></td>
      <td><div class="skeleton" style="width:56px"></div></td></tr>`).join('');
}

async function loadCandidates() {
  const body = $('#candBody');
  body.innerHTML = skeletonRows();
  try {
    const rows = await api(`/api/candidates?status=${encodeURIComponent(state.status)}&q=${encodeURIComponent(state.q)}`);
    state.candidates = rows;
    state.candPage = 1;
    renderCandidates();
  } catch (e) {
    body.innerHTML = '';
    toast(e.message, 'err', '读取失败');
  }
}

const EXP_RANGES = [
  { key: '0-3', label: '3 年以下', min: 0, max: 3 },
  { key: '3-5', label: '3-5 年', min: 3, max: 5 },
  { key: '5-10', label: '5-10 年', min: 5, max: 10 },
  { key: '10+', label: '10 年以上', min: 10, max: Infinity },
];

function filteredCandidates() {
  return state.candidates.filter((c) => {
    if (state.eduFilter && c.education !== state.eduFilter) return false;
    if (state.expFilter) {
      const rg = EXP_RANGES.find((x) => x.key === state.expFilter);
      if (rg && !(c.years >= rg.min && c.years < rg.max)) return false;
    }
    return true;
  });
}

function renderCandidates() {
  const body = $('#candBody');
  const all = filteredCandidates();
  const pages = Math.max(1, Math.ceil(all.length / state.candPageSize));
  state.candPage = Math.min(state.candPage, pages);
  const start = (state.candPage - 1) * state.candPageSize;
  const rows = all.slice(start, start + state.candPageSize);

  $('#candEmpty').hidden = all.length > 0;
  $('#candCount').textContent = `共 ${all.length} 位候选人`;
  body.innerHTML = rows.map((c, i) => `
    <tr data-id="${c.id}">
      <td class="mono muted">${start + i + 1}</td>
      <td><div class="person-meta solo"><b>${escapeHtml(c.name)}</b></div></td>
      <td class="muted">${escapeHtml(c.gender || '—')}</td>
      <td class="muted">${escapeHtml(c.education)}</td>
      <td class="mono">${c.years ? c.years + ' 年' : '—'}</td>
      <td><div class="tag-chips">${(c.tags.slice(0, 4).map((t) => `<span class="tag">${escapeHtml(t)}</span>`).join('') || '<span class="muted tiny">未填写</span>')}</div></td>
      <td>${pill(c.status)}</td>
      <td class="muted tiny">${c.email ? escapeHtml(c.email) : '<span class="pill danger">缺邮箱</span>'}</td>
      <td class="muted tiny">${escapeHtml(c.source)}</td>
    </tr>`).join('');
  $$('#candBody tr').forEach((tr) => tr.addEventListener('click', () => openDrawer(tr.dataset.id)));

  // 分页器
  const pager = $('#candPager');
  if (pages <= 1) { pager.innerHTML = ''; return; }
  let html = `<button class="pager-btn" data-page="${state.candPage - 1}" ${state.candPage === 1 ? 'disabled' : ''}>‹</button>`;
  for (let p = 1; p <= pages; p++) {
    if (pages > 7 && p > 2 && p < pages - 1 && Math.abs(p - state.candPage) > 1) {
      if (html.slice(-16) !== '<span class="dots">…</span>') html += '<span class="dots">…</span>';
      continue;
    }
    html += `<button class="pager-btn ${p === state.candPage ? 'on' : ''}" data-page="${p}">${p}</button>`;
  }
  html += `<button class="pager-btn" data-page="${state.candPage + 1}" ${state.candPage === pages ? 'disabled' : ''}>›</button>`;
  pager.innerHTML = html;
  $$('#candPager [data-page]').forEach((b) => b.addEventListener('click', () => {
    state.candPage = Number(b.dataset.page);
    renderCandidates();
  }));
}

function buildFilterMenus() {
  const eduOpts = ['', '大专', '本科', '硕士', '博士', '未填写'];
  $('#fEduMenu').innerHTML = eduOpts.map((v) =>
    `<button class="th-menu-item ${state.eduFilter === v ? 'on' : ''}" data-v="${v}">${v || '全部学历'}</button>`).join('');
  $('#fExpMenu').innerHTML = [{ key: '', label: '全部年限' }, ...EXP_RANGES].map((o) =>
    `<button class="th-menu-item ${state.expFilter === o.key ? 'on' : ''}" data-v="${o.key}">${o.label}</button>`).join('');
  $$('#fEduMenu .th-menu-item').forEach((b) => b.addEventListener('click', () => {
    state.eduFilter = b.dataset.v; state.candPage = 1;
    $('#fEduMenu').hidden = true; buildFilterMenus(); renderCandidates();
  }));
  $$('#fExpMenu .th-menu-item').forEach((b) => b.addEventListener('click', () => {
    state.expFilter = b.dataset.v; state.candPage = 1;
    $('#fExpMenu').hidden = true; buildFilterMenus(); renderCandidates();
  }));
  $('#fEduBtn').classList.toggle('on', !!state.eduFilter);
  $('#fExpBtn').classList.toggle('on', !!state.expFilter);
}

/* ---------------- 抽屉 ---------------- */
async function openDrawer(id) {
  const mask = $('#drawerMask'), drawer = $('#drawer');
  mask.classList.add('on');
  drawer.classList.add('on');
  $('#drawerName').textContent = '加载中…';
  $('#drawerStatus').textContent = '';
  $('#drawerAvatar').textContent = '';
  $('#drawerBody').innerHTML = '<div class="skeleton" style="height:14px;width:60%;margin:8px 0"></div>'.repeat(6);
  try {
    const c = await api(`/api/candidate/${id}`);
    $('#drawerName').textContent = c.name;
    $('#drawerStatus').textContent = c.status;
    $('#drawerStatus').className = `pill ${STATUS_CLASS[c.status] ?? ''}`;
    const av = $('#drawerAvatar');
    av.textContent = (c.name || '?').slice(0, 1);
    av.style.background = avatarOf(c.name);
    $('#drawerBody').innerHTML = `
      <div class="sec-label">基础信息</div>
      <div class="detail-grid">
        <div class="detail-item"><span>编号</span><b>#${c.id}</b></div>
        <div class="detail-item"><span>性别</span><b>${escapeHtml(c.gender || '未填写')}</b></div>
        <div class="detail-item"><span>学历</span><b>${escapeHtml(c.education)}</b></div>
        <div class="detail-item"><span>工作年限</span><b>${c.years ? c.years + ' 年' : '未填写'}</b></div>
        <div class="detail-item"><span>联系电话</span><b>${escapeHtml(c.phone || '未填写')}</b></div>
        <div class="detail-item"><span>电子邮箱</span><b>${escapeHtml(c.email || '未填写')}</b></div>
        <div class="detail-item"><span>简历来源</span><b>${escapeHtml(c.source)}</b></div>
      </div>
      <div class="sec-label">教育背景</div>
      ${(c.edu_background || []).length ? c.edu_background.map((e) => `
        <div class="exp-item">
          <div class="exp-top"><b>${escapeHtml(e.school || '')}</b><span class="muted tiny mono">${escapeHtml(e.period || '')}</span></div>
          <div class="muted tiny">${escapeHtml(e.major || '')}${e.degree ? ' · ' + escapeHtml(e.degree) : ''}</div>
        </div>`).join('') : '<div class="muted tiny">未填写</div>'}
      <div class="sec-label">荣誉证书</div>
      ${(c.honors || []).length ? `<div class="tag-chips">${c.honors.map((h) => `<span class="tag">${escapeHtml(h)}</span>`).join('')}</div>`
        : '<div class="muted tiny">未填写</div>'}
      <div class="sec-label">工作经验</div>
      ${(c.work_experience || []).length ? c.work_experience.map((w) => `
        <div class="exp-item">
          <div class="exp-top"><b>${escapeHtml(w.company || '')} · ${escapeHtml(w.role || '')}</b><span class="muted tiny mono">${escapeHtml(w.period || '')}</span></div>
          <div class="muted tiny">${escapeHtml(w.desc || '')}</div>
        </div>`).join('') : '<div class="muted tiny">未填写</div>'}
      <div class="sec-label">技能特长</div>
      <div class="tag-chips">${c.tags.length ? c.tags.map((t) => `<span class="tag">${escapeHtml(t)}</span>`).join('') : '<span class="muted tiny">未填写</span>'}</div>
      <div class="sec-label">简历内容</div>
      <div class="resume-text">${c.resume ? escapeHtml(c.resume) : '<span class="muted">（该候选人暂无简历内容）</span>'}</div>`;
  } catch (e) {
    $('#drawerBody').innerHTML = `<div class="empty"><b>读取失败</b><p>${escapeHtml(e.message)}</p></div>`;
  }
}

function closeDrawer() {
  $('#drawerMask').classList.remove('on');
  $('#drawer').classList.remove('on');
}

/* ---------------- 职位 ---------------- */
async function loadPositions() {
  const grid = $('#jobGrid');
  grid.innerHTML = '<div class="skeleton" style="height:110px;border-radius:12px"></div>'.repeat(3);
  try {
    const rows = await api('/api/positions');
    state.positions = rows;
    if (!rows.length) {
      grid.innerHTML = `<div class="empty" style="grid-column:1/-1">
        <div class="empty-art"><svg><use href="#i-brief"/></svg></div>
        <b>还没有职位</b><p>在右侧填一份职位，匹配引擎就能开始工作。</p></div>`;
      return;
    }
    grid.innerHTML = rows.map((p) => `
      <div class="job-card">
        <div class="job-top">
          <div>
            <div class="job-title">${escapeHtml(p.title)}</div>
            <div class="job-company"><svg style="width:12px;height:12px"><use href="#i-building"/></svg>${escapeHtml(p.company)}</div>
          </div>
          ${p.open ? '<span class="pill ok">招聘中</span>' : '<span class="pill">已关闭</span>'}
        </div>
        <div class="job-meta">
          <span>经验 <b>${p.min_years} 年+</b></span>
          <span>薪资 <b>${escapeHtml(p.salary || '面议')}</b></span>
        </div>
        <div class="tag-chips" style="margin-bottom:10px">${p.tags.slice(0, 5).map((t) => `<span class="tag">${escapeHtml(t)}</span>`).join('') || '<span class="muted tiny">未填技能</span>'}</div>
        <div class="job-foot">
          <span class="muted tiny">编号 ${p.id}</span>
          ${p.open ? `<button class="close-link" data-close="${p.id}">关闭职位</button>` : '<span class="muted tiny">已下线</span>'}
        </div>
      </div>`).join('');

    $$('[data-close]').forEach((b) => b.addEventListener('click', async () => {
      if (!confirm('确认关闭该职位？历史推荐记录会保留。')) return;
      try {
        const r = await api(`/api/positions/${b.dataset.close}/close`, { method: 'POST' });
        toast(r.message, 'ok', '已关闭');
        loadPositions();
        loadOverview().catch(() => {});
      } catch (e) { toast(e.message, 'err', '关闭失败'); }
    }));
  } catch (e) {
    grid.innerHTML = `<div class="empty"><b>读取失败</b><p>${escapeHtml(e.message)}</p></div>`;
  }
}

async function addPosition() {
  const body = {
    company: $('#posCompany').value.trim(),
    title: $('#posTitle').value.trim(),
    min_years: Number($('#posYears').value || 0),
    salary: $('#posSalary').value.trim(),
    skills: $('#posSkills').value.trim(),
  };
  if (!body.company || !body.title) { toast('公司名称和职位名称都要填', 'err', '信息不完整'); return; }
  try {
    const btn = $('#posAdd');
    await withLoading(btn, () => api('/api/positions', { method: 'POST', body }));
    toast(`职位「${body.title}」已创建`, 'ok', '创建成功');
    $('#posTitle').value = ''; $('#posSkills').value = ''; $('#posSalary').value = '';
    loadPositions();
    loadOverview().catch(() => {});
  } catch (e) { toast(e.message, 'err', '创建失败'); }
}

/* ---------------- 公司库 ---------------- */
async function loadCompanies() {
  const grid = $('#companyGrid');
  grid.innerHTML = '<div class="skeleton" style="height:160px;border-radius:16px"></div>'.repeat(3);
  try {
    const rows = await api('/api/companies');
    state.companies = rows;
    $('#companyList').innerHTML = rows.map((c) => `<option value="${escapeHtml(c.name)}">`).join('');
    if (!rows.length) {
      grid.innerHTML = `<div class="empty" style="grid-column:1/-1"><b>公司库还是空的</b><p>在上面输入公司名，背调一次就会入库。</p></div>`;
      return;
    }
    grid.innerHTML = rows.map((c) => `
      <div class="company-card clickable" data-cid="${c.id}" title="点击查看公司详情">
        <div class="company-top">
          <span class="company-logo" style="background:${avatarOf(c.name)}">${escapeHtml(c.name.slice(0, 1))}</span>
          <div>
            <div class="company-name">${escapeHtml(c.name)}</div>
            <div class="company-sub">${escapeHtml(c.industry)} · 简介 ${c.intro_len} 字</div>
          </div>
        </div>
        <div class="company-intro">${escapeHtml(c.intro || '（暂无简介）')}</div>
        <div class="company-foot">
          <span class="pill brand">${escapeHtml(c.source)}</span>
          ${c.open_positions ? `<span class="pill ok">${c.open_positions} 个在招</span>` : '<span class="pill">暂无在招</span>'}
          <span class="muted tiny" style="margin-left:auto">${escapeHtml(c.created || '')}</span>
        </div>
      </div>`).join('');
    $$('#companyGrid .company-card').forEach((el) =>
      el.addEventListener('click', () => openCompanyDetail(el.dataset.cid)));
  } catch (e) {
    grid.innerHTML = `<div class="empty"><b>读取失败</b><p>${escapeHtml(e.message)}</p></div>`;
  }
}

async function lookupCompany() {
  const name = $('#compInput').value.trim();
  if (!name) { toast('请输入公司名称', 'err'); return; }
  const btn = $('#compLookup');
  try {
    const r = await withLoading(btn, () => api('/api/companies/lookup', { method: 'POST', body: { name } }));
    const brief = r.markdown.replace(/[*#>`]/g, '').replace(/\s+/g, ' ').slice(0, 60);
    toast(brief + '…', 'ok', `「${name}」已背调入库`);
    $('#compInput').value = '';
    loadCompanies();
  } catch (e) { toast(e.message, 'err', '背调失败'); }
}

/* ---------------- 公司详情弹窗 ---------------- */
async function openCompanyDetail(cid) {
  const mask = $('#cmpMask');
  $('#cmpName').textContent = '加载中…';
  $('#cmpSub').textContent = '';
  $('#cmpStats').innerHTML = '';
  $('#cmpIntro').textContent = '正在读取公司介绍…';
  $('#cmpLink').hidden = true;
  mask.hidden = false;
  requestAnimationFrame(() => mask.classList.add('on'));
  try {
    const c = await api(`/api/companies/${cid}`);
    $('#cmpName').textContent = c.name;
    $('#cmpSub').textContent = [c.industry !== '待补充' ? c.industry : '', c.scale || '']
      .filter(Boolean).join(' · ') + ` · ${c.created} 收录`;
    $('#cmpStats').innerHTML = `
      <span class="pill brand">${escapeHtml(c.source)}</span>
      ${c.open_positions ? `<span class="pill ok">${c.open_positions} 个在招</span>` : '<span class="pill">暂无在招</span>'}
      ${c.records ? `<span class="pill">${c.records} 条推荐记录</span>` : ''}`;
    $('#cmpIntro').textContent = c.intro || '（暂无简介，可在上方输入公司名重新背调）';
    const link = $('#cmpLink');
    link.href = c.baike_url;
    link.textContent = `在百度百科查看「${c.name}」`;
    link.hidden = false;
  } catch (e) {
    $('#cmpName').textContent = '读取失败';
    $('#cmpIntro').textContent = e.message;
  }
}

function closeCompanyDetail() {
  const mask = $('#cmpMask');
  mask.classList.remove('on');
  setTimeout(() => { mask.hidden = true; }, 200);
}

function bindCompanyModal() {
  $('#cmpClose').addEventListener('click', closeCompanyDetail);
  $('#cmpCancel').addEventListener('click', closeCompanyDetail);
  $('#cmpMask').addEventListener('click', (ev) => { if (ev.target.id === 'cmpMask') closeCompanyDetail(); });
  document.addEventListener('keydown', (ev) => {
    if (ev.key === 'Escape' && !$('#cmpMask').hidden) closeCompanyDetail();
  });
}

/* ---------------- 公司分析 ---------------- */
const DIMS = ['招聘活跃度', '薪酬吸引力', '推送进展', '数据可信度', '信息完整度', '合规风险'];
// 报告章节标题是「**优势**」「**合规风险核查**（企查查开放平台）」这种粗体独占行
const SEC_RE = /^\*\*([^*]{1,14})\*\*\s*(（[^）]*）)?\s*(.*)$/;

function parseReport(markdown) {
  const raw = String(markdown || '').replace(/\r/g, '');
  const lines = raw.split('\n');
  const res = { title: '', score: null, level: '', meta: [], dims: [], sections: [], footnote: [] };
  let idx = 0;

  // ---- 头部：标题 / 评分 / 元信息 / 五维表 ----
  while (idx < lines.length) {
    const line = lines[idx].trim();
    if (!line) { idx++; continue; }

    if (!res.title && /^#{1,4}\s+/.test(line)) {
      res.title = line.replace(/^#{1,4}\s+/, '').trim();
      idx++; continue;
    }
    if (line.includes('综合评分')) {
      const m = line.match(/(\d+)\s*\/\s*100/);
      if (m) res.score = parseInt(m[1], 10);
      const parts = line.split('·');
      if (parts.length > 1) res.level = parts[parts.length - 1].replace(/[*`]/g, '').trim();
      idx++; continue;
    }
    if (line.includes('行业：')) {
      res.meta = line.replace(/\u3000/g, ' ').split('|')
        .map((s) => s.replace(/[*`]/g, '').trim()).filter(Boolean);
      idx++; continue;
    }
    if (line.startsWith('|') && line.includes('维度')) {
      idx += 2;
      while (idx < lines.length && lines[idx].trim().startsWith('|')) {
        const cells = lines[idx].trim().replace(/^\||\|$/g, '').split('|').map((s) => s.trim());
        if (cells.length >= 2 && DIMS.includes(cells[0])) {
          const m = cells[1].match(/([\d.]+)\s*\/\s*(\d+)/);
          const single = cells[1].match(/^([\d.]+)/);
          res.dims.push({
            name: cells[0],
            dedup: !m && !!single,             // 合规风险这类是「扣分」不是「得分」
            score: m ? parseFloat(m[1]) : (single ? parseFloat(single[1]) : 0),
            max: m ? parseFloat(m[2]) : 25,
            basis: cells[2] || '',
          });
        }
        idx++;
      }
      continue;
    }
    break;
  }

  // ---- 正文分节 ----
  let cur = null;
  for (; idx < lines.length; idx++) {
    const t = lines[idx].trim();
    const m = t.match(SEC_RE);
    if (m && !t.startsWith('|')) {
      cur = { title: m[1].trim(), sub: (m[2] || '').replace(/[（）]/g, ''), lines: [] };
      if (m[3]) cur.lines.push(m[3]);
      res.sections.push(cur);
      continue;
    }
    if (cur) cur.lines.push(lines[idx]);
    else if (t) res.footnote.push(t);
  }
  return res;
}

function scoreColor(score) {
  if (score >= 70) return ['var(--ok)', '可以推进', 'ok'];
  if (score >= 55) return ['var(--warn)', '谨慎推进', 'warn'];
  return ['var(--danger)', '暂缓推荐', 'danger'];
}

function renderReport(markdown, companyName) {
  const r = parseReport(markdown);
  const body = $('#analBody');

  if (r.score === null && !r.sections.length) {
    body.innerHTML = `<div class="card"><div class="prose">${mdToHtml(markdown)}</div></div>`;
    return;
  }

  const score = r.score ?? 0;
  const [color, defaultLevel] = scoreColor(score);
  const C = 2 * Math.PI * 62;
  const dash = (score / 100) * C;

  const dimsHtml = r.dims.map((d) => {
    if (d.dedup) {
      const ok = d.score <= 0;
      return `<div class="dim-item">
        <div class="dim-top"><span>${escapeHtml(d.name)}</span>
          <b style="color:${ok ? 'var(--ok)' : 'var(--danger)'}">${ok ? '未扣分' : `扣 ${d.score} 分`}</b></div>
      </div>`;
    }
    const pct = Math.round((d.score / (d.max || 25)) * 100);
    const cls = pct < 40 ? 'warn' : '';
    return `<div class="dim-item">
      <div class="dim-top"><span>${escapeHtml(d.name)}</span><b>${d.score}<i>/${d.max}</i></b></div>
      <div class="dim-track"><div class="dim-fill ${cls}" style="width:${Math.min(pct, 100)}%"></div></div>
    </div>`;
  }).join('');

  const metaHtml = r.meta.length ? `<div class="meta-chips">${r.meta.map((m) => {
    const at = m.indexOf('：');
    return at > 0
      ? `<span class="meta-chip">${escapeHtml(m.slice(0, at))}<b>${escapeHtml(m.slice(at + 1))}</b></span>`
      : `<span class="meta-chip">${escapeHtml(m)}</span>`;
  }).join('')}</div>` : '';

  // 分节渲染：合规风险核查 → 卡片组，其余 → prose
  const sectionsHtml = r.sections.map((sec) => {
    if (sec.title.includes('合规风险')) {
      const items = [], notes = [];
      sec.lines.forEach((l) => {
        const t = l.trim();
        if (!t) return;
        const m = t.match(/^[-*+]\s*(.+?)[：:]\s*(.+)$/);
        if (m) {
          const label = m[1].replace(/\*\*/g, '').trim();
          const value = m[2].replace(/\*\*/g, '').replace(/<[^>]+>/g, '').trim();
          if (/注意|备注|说明|核查时间/.test(label)) notes.push(`${label}：${value}`);
          else items.push({ label, value });
        } else {
          notes.push(t.replace(/<[^>]+>/g, '').replace(/\*\*/g, ''));
        }
      });
      const cards = items.map((it) => {
        let cls = 'unknown', icon = 'i-alert';
        if (/未命中|无记录|无风险|^0\s*条/.test(it.value)) { cls = 'ok'; icon = 'i-check'; }
        else if (/命中|条|次/.test(it.value)) { cls = 'bad'; icon = 'i-alert'; }
        else if (/通过|正常|无异常/.test(it.value)) { cls = 'ok'; icon = 'i-check'; }
        return `<div class="risk-item ${cls}">
          <div class="rk">${escapeHtml(it.label.replace(/核查$/, ''))}</div>
          <div class="rv"><svg><use href="#${icon}"/></svg>${escapeHtml(it.value)}</div>
        </div>`;
      }).join('');
      return `<div class="card" style="margin-bottom:16px">
        <div class="card-head"><div class="card-title"><div><b>合规风险核查</b>
          <i>企查查官方接口${sec.sub ? ' · ' + escapeHtml(sec.sub) : ''} · 30 天缓存</i></div></div></div>
        <div class="risk-grid">${cards || '<span class="muted tiny">本次未返回核查结果</span>'}</div>
        ${notes.length ? `<p class="tiny muted" style="margin:12px 0 0">${inlineMd(notes.join('　·　'))}</p>` : ''}
      </div>`;
    }
    const content = mdToHtml(sec.lines.join('\n'));
    if (!content.trim()) return '';
    return `<div class="card" style="margin-bottom:16px">
      <div class="prose"><h2>${escapeHtml(sec.title)}</h2>${content}</div>
    </div>`;
  }).join('');

  const foot = r.footnote.join(' ').replace(/<[^>]+>/g, '').replace(/-{3,}/g, '').trim();

  body.innerHTML = `
    <div class="report">
      <div class="report-side">
        <div class="card score-card">
          <div class="score-ring" style="--c:${color}">
            <svg viewBox="0 0 160 160">
              <circle class="track" cx="80" cy="80" r="62"/>
              <circle class="val" cx="80" cy="80" r="62" stroke="${color}"
                stroke-dasharray="${dash} ${C - dash}" stroke-dashoffset="0"/>
            </svg>
            <div class="center">
              <div class="num" style="color:${color}">${score}</div>
              <div class="of">/ 100</div>
            </div>
          </div>
          <span class="score-level" style="color:${color};background:color-mix(in srgb, ${color} 12%, transparent)">
            ${escapeHtml(r.level || defaultLevel)}
          </span>
          <div class="muted tiny" style="margin-top:10px">${escapeHtml(companyName || r.title || '')}</div>
        </div>

        ${dimsHtml ? `<div class="card">
          <div class="card-title small" style="margin-bottom:14px"><b>五维评分</b></div>
          <div class="dim-list">${dimsHtml}</div>
        </div>` : ''}

        ${metaHtml ? `<div class="card">
          <div class="card-title small" style="margin-bottom:11px"><b>基础信息</b></div>
          ${metaHtml}
        </div>` : ''}
      </div>

      <div class="report-main">
        ${sectionsHtml}
        ${foot ? `<p class="tiny muted" style="margin:0 4px">${inlineMd(foot)}</p>` : ''}
      </div>
    </div>`;
}

async function loadAnalyses() {
  try {
    const rows = await api('/api/analyses');
    state.analyses = rows;
    $('#analList').innerHTML = Array.from(new Set(rows.map((r) => r.company)))
      .map((n) => `<option value="${escapeHtml(n)}">`).join('');
    $('#histBody').innerHTML = rows.map((r) => `
      <tr data-company="${escapeHtml(r.company)}">
        <td><div class="person"><span class="avatar-sm" style="background:${avatarOf(r.company)}">${escapeHtml(r.company.slice(0, 1))}</span>
          <b style="font-weight:600">${escapeHtml(r.company)}</b></div></td>
        <td><b class="mono" style="color:${scoreColor(Number(r.score) || 0)[0]}">${r.score}</b></td>
        <td>${pill(r.level)}</td>
        <td class="muted tiny">${escapeHtml(String(r.source).includes('规则') ? '规则引擎' : r.source)}</td>
        <td class="muted tiny" style="max-width:280px">${escapeHtml(r.summary || '')}</td>
        <td class="muted tiny">${escapeHtml(r.time || '')}</td>
      </tr>`).join('');
    $$('#histBody tr').forEach((tr) => tr.addEventListener('click', () => {
      $('#analInput').value = tr.dataset.company;
      runAnalysis(false, false);
    }));
  } catch (e) { toast(e.message, 'err', '历史记录读取失败'); }
}

async function runAnalysis(refresh = false, risk = false) {
  const name = $('#analInput').value.trim();
  if (!name) { toast('请输入或选择公司名称', 'err'); return; }
  const body = $('#analBody');
  body.innerHTML = `<div class="card">
    <div style="display:flex;gap:14px;align-items:center">
      <div class="skeleton" style="width:56px;height:56px;border-radius:16px"></div>
      <div style="flex:1">
        <div class="skeleton" style="height:14px;width:40%;margin-bottom:8px"></div>
        <div class="skeleton" style="height:11px;width:64%"></div>
      </div>
    </div>
    <div class="skeleton" style="height:12px;margin-top:20px"></div>
    <div class="skeleton" style="height:12px;margin-top:10px;width:88%"></div>
    <div class="skeleton" style="height:12px;margin-top:10px;width:72%"></div>
    <p class="tiny muted" style="margin:18px 0 0">正在采集工商信息、核查风险并生成报告，首次分析约需 20–60 秒…</p>
  </div>`;
  const btn = refresh ? $('#analRefresh') : (risk ? $('#analRisk') : $('#analRun'));
  try {
    const data = await withLoading(btn, () => api('/api/analysis', {
      method: 'POST', body: { name, refresh, refresh_risk: risk },
    }));
    renderReport(data.markdown, name);
    loadAnalyses();
    toast(`「${name}」分析完成`, 'ok', '报告已生成');
  } catch (e) {
    body.innerHTML = `<div class="card intro-card"><div class="intro-art"><svg><use href="#i-alert"/></svg></div>
      <b>分析失败</b><p>${escapeHtml(e.message)}</p></div>`;
    toast(e.message, 'err', '分析失败');
  }
}

/* ---------------- 系统设置 ---------------- */
/* ---------------- 匹配策略：权重面板 + 推荐决策台 ---------------- */
function gradeOf(score, threshold) {
  if (score >= 90) return ['强烈推荐', 'g-strong'];
  if (score >= threshold) return ['推荐', 'g-rec'];
  return ['储备', 'g-pool'];
}

function paintSlider(el) {
  const pct = ((el.value - el.min) / (el.max - el.min)) * 100;
  el.style.setProperty('--fill', pct + '%');
}

function syncWeightUI() {
  const skill = Number($('#wSkill').value);
  const thr = Number($('#wThr').value);
  $('#wExp').value = 100 - skill;
  $('#wSkillVal').textContent = skill + '%';
  $('#wExpVal').textContent = (100 - skill) + '%';
  $('#wThrVal').textContent = thr + ' 分';
  $('#weightPreview').textContent = `技能 ${skill}% · 经验 ${100 - skill}% · 阈值 ${thr}`;
  [$('#wSkill'), $('#wExp'), $('#wThr')].forEach(paintSlider);
  // 高亮匹配的预设
  $$('#presetRow .chip-btn').forEach((b) => {
    b.classList.toggle('on', Number(b.dataset.skill) === skill && Number(b.dataset.thr) === thr);
  });
}

async function loadStrategy() {
  try {
    if (!state.weights) {
      state.weights = await api('/api/match-weights');
      $('#wSkill').value = Math.round(state.weights.skill * 100);
      $('#wThr').value = state.weights.threshold;
    }
    syncWeightUI();
    await loadDecisions();
  } catch (e) { toast(e.message, 'err', '权重读取失败'); }
}

async function saveWeights() {
  try {
    const r = await api('/api/match-weights', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ skill: Number($('#wSkill').value) / 100, threshold: Number($('#wThr').value) }),
    });
    state.weights = r.weights;
    toast(r.message, 'ok', '权重已保存');
    syncWeightUI();
    renderDecisions(); // 决策台档位立即按新阈值重标
    const box = $('#recalcResult');
    if (!box.hidden) {
      const w = r.weights;
      box.innerHTML = `权重已更新为 <b>技能 ${Math.round(w.skill * 100)}% / 经验 ${Math.round(w.exp * 100)}%</b>、阈值 <b>${w.threshold}</b>；`
        + '上方重算结果基于旧权重，请点击「按当前权重重算全库」刷新推荐记录与档位标记。';
    }
  } catch (e) { toast(e.message, 'err', '保存失败'); }
}

async function recalcAll() {
  const btn = $('#wRecalc');
  btn.disabled = true;
  btn.innerHTML = '<svg><use href="#i-refresh"/></svg>重算中…';
  $('#recalcResult').hidden = true;
  try {
    const r = await api('/api/match/recalc', { method: 'POST' });
    const box = $('#recalcResult');
    box.hidden = false;
    box.innerHTML = `已按 <b>技能 ${Math.round(r.weights.skill * 100)}% / 经验 ${Math.round(r.weights.exp * 100)}%</b>、阈值 <b>${r.weights.threshold}</b> 重算：`
      + `评估 ${r.evaluated} 人次 · <b>硬性门槛拦下 ${r.hard_blocked ?? 0}</b> 人次 · <b>强烈推荐 ${r.strong}</b> 人 · 推荐 ${r.recommended} 人 · 分数变化 ${r.score_changed} 条（变化记录的旧决策已重置为待审阅）。`
      + ' 算法结果仅为标记，请查阅简历后人工决定。';
    toast('全库重算完成', 'ok', '匹配引擎');
    loadDecisions();
    loadOverview().then(renderStats).catch(() => {});
  } catch (e) { toast(e.message, 'err', '重算失败'); }
  finally {
    btn.disabled = false;
    btn.innerHTML = '<svg><use href="#i-refresh"/></svg>按当前权重重算全库';
  }
}

async function loadDecisions() {
  try {
    const rows = await api('/api/records');
    state.records = rows;
    renderDecisions();
  } catch (e) { toast(e.message, 'err', '推荐记录读取失败'); }
}

const SRC_LABEL = { llm: 'AI 生成', rule: '规则模板', hr: 'HR 手写' };

function renderDecisions() {
  const thr = state.weights ? state.weights.threshold : 80;
  const f = state.decisionFilter;
  const rows = state.records.filter((r) => {
    if (f === 'all') return true;
    if (f === '') return !r.decision;
    return r.decision === f;
  });
  const body = $('#decBody');
  if (!rows.length) {
    body.innerHTML = '';
    $('#decEmpty').hidden = false;
    $('#decCount').textContent = '0 条记录';
    return;
  }
  $('#decEmpty').hidden = true;
  body.innerHTML = rows.map((r) => {
    const [gName, gCls] = gradeOf(r.score, thr);
    const dec = r.decision
      ? (r.decision === '邀约' ? '<span class="decision-pill d-yes">✓ 已邀约</span>'
                               : '<span class="decision-pill d-no">— 已暂缓</span>')
      : '<span class="decision-pill d-pending">待审阅</span>';
    const canDecide = !r.decision;
    // 理由优先展示；没有则提示可生成
    const reasonTxt = (r.reason || '').trim();
    const reasonHtml = reasonTxt
      ? `<div class="reason-text" data-reason-toggle title="点击展开/收起">${escapeHtml(reasonTxt)}</div>`
      : '<div class="reason-text empty">尚未生成推选理由</div>';
    const srcHtml = reasonTxt
      ? `<span class="src-tag src-${r.reason_src || 'rule'}">${SRC_LABEL[r.reason_src] || '规则模板'}</span>`
      : '';
    const mail = r.send_status === 'sent'
      ? `<span class="mail-pill m-sent">已推送 ${escapeHtml(r.sent_at && r.sent_at !== '-' ? r.sent_at : '')}</span>`
      : (r.send_status === 'failed'
        ? '<span class="mail-pill m-failed">发送失败</span>'
        : `<span class="mail-pill m-pending">${r.email ? '未推送' : '缺邮箱'}</span>`);
    const pushDisabled = (!r.email || r.send_status === 'sent') ? 'disabled' : '';
    return `<tr>
      <td><b>${escapeHtml(r.candidate)}</b></td>
      <td>${escapeHtml(r.position)}</td>
      <td class="score-cell">${r.score}</td>
      <td><span class="grade ${gCls}">${gName}</span></td>
      <td class="reason-cell">${reasonHtml}<div class="reason-meta">${srcHtml}</div></td>
      <td>${mail}</td>
      <td>${dec}</td>
      <td><div class="dec-actions">
        <button class="mini-btn m-view" data-view-cand="${r.candidate_id}"><svg><use href="#i-doc"/></svg>简历</button>
        <button class="mini-btn m-yes" data-decide="邀约" data-rid="${r.id}" ${canDecide ? '' : 'disabled'}>邀约</button>
        <button class="mini-btn m-no" data-decide="暂缓" data-rid="${r.id}" ${canDecide ? '' : 'disabled'}>暂缓</button>
        <button class="mini-btn m-push" data-push="${r.id}" ${pushDisabled} title="${r.email ? '推送并附推选理由' : '候选人缺少邮箱'}"><svg><use href="#i-send"/></svg>推送</button>
      </div></td>
    </tr>`;
  }).join('');
  $('#decCount').textContent = `${rows.length} 条记录`;
  $$('#decBody [data-view-cand]').forEach((b) =>
    b.addEventListener('click', () => openDrawer(b.dataset.viewCand)));
  $$('#decBody [data-decide]').forEach((b) =>
    b.addEventListener('click', () => decide(b.dataset.rid, b.dataset.decide)));
  $$('#decBody [data-push]').forEach((b) =>
    b.addEventListener('click', () => openPush(b.dataset.push)));
  $$('#decBody [data-reason-toggle]').forEach((el) =>
    el.addEventListener('click', () => el.classList.toggle('open')));
}

async function decide(rid, decision) {
  const verb = decision === '邀约' ? '确认邀约' : '标记暂缓';
  if (!confirm(`${verb}这条推荐？（算法分只是标记，决策会记录在案）`)) return;
  try {
    await api(`/api/records/${rid}/decision`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ decision }),
    });
    toast(`已${verb}`, 'ok', 'HR 决策');
    loadDecisions();
  } catch (e) { toast(e.message, 'err', '决策失败'); }
}

/* ---------------- 推送候选人（推选理由 + 邮件预览 + 确认发送） ---------------- */
const pushState = { rid: null, rec: null, mail: null, busy: false };

function setPushBusy(on, text) {
  pushState.busy = on;
  const btn = $('#pushSend');
  if (btn) {
    btn.disabled = on;
    btn.innerHTML = on
      ? `<svg><use href="#i-clock"/></svg>${text || '发送中…'}`
      : '<svg><use href="#i-send"/></svg>确认推送';
  }
}

function showPushWarn(msg, isErr) {
  const box = $('#pushWarn');
  if (!msg) { box.hidden = true; box.innerHTML = ''; return; }
  box.hidden = false;
  box.className = `push-warn${isErr ? ' err' : ''}`;
  box.innerHTML = `<svg style="width:15px;height:15px;flex:none;margin-top:2px;fill:none;stroke:currentColor;stroke-width:1.9"><use href="#i-alert"/></svg><span>${escapeHtml(msg)}</span>`;
}

function syncReasonSrc(src) {
  const el = $('#pushReasonSrc');
  el.className = `src-tag src-${src || 'rule'}`;
  el.textContent = SRC_LABEL[src] || '规则模板';
}

async function openPush(rid) {
  const rec = (state.records || []).find((r) => String(r.id) === String(rid));
  if (!rec) { toast('找不到这条推荐记录', 'err', '推送'); return; }
  pushState.rid = rid;
  pushState.rec = rec;
  pushState.mail = null;

  $('#pushSub').textContent = `${rec.candidate} → ${rec.position} · 算法分 ${rec.score}`;
  $('#pushReason').value = rec.reason || '';
  $('#pushReasonHint').textContent = '';
  syncReasonSrc(rec.reason_src);
  $('#pushTo').textContent = rec.email || '（缺邮箱）';
  $('#pushSubject').textContent = '生成中…';
  $('#pushMailBody').textContent = '正在生成邮件草稿…';
  // 未标「邀约」时才给出「跳过决策」勾选项
  $('#pushConfirmRow').hidden = rec.decision === '邀约';
  $('#pushConfirmDecision').checked = false;
  showPushWarn('');

  const mask = $('#pushMask');
  mask.hidden = false;
  requestAnimationFrame(() => mask.classList.add('on'));
  setPushBusy(true, '生成中…');
  await refreshMail();
  setPushBusy(false);
}

function closePush() {
  const mask = $('#pushMask');
  mask.classList.remove('on');
  setTimeout(() => { mask.hidden = true; }, 200);
  pushState.rid = null;
  pushState.rec = null;
  pushState.mail = null;
}

async function refreshMail() {
  if (!pushState.rid) return;
  try {
    const d = await api(`/api/records/${pushState.rid}/email?use_llm=true`);
    pushState.mail = d;
    $('#pushTo').textContent = d.to || '（缺邮箱）';
    $('#pushSubject').textContent = d.subject || '（无主题）';
    $('#pushMailBody').textContent = d.body || '（无正文）';
    // 服务端可能已经补出了理由（原记录没有时），同步回填
    if (!($('#pushReason').value || '').trim() && d.reason) {
      $('#pushReason').value = d.reason;
      syncReasonSrc(d.reason_source);
    }
    if (d.recipient_problem) showPushWarn(`${d.recipient_problem}，请先补真实邮箱再推送。`, true);
    else showPushWarn('');
  } catch (e) {
    $('#pushSubject').textContent = '生成失败';
    $('#pushMailBody').textContent = e.message || '邮件草稿生成失败';
    showPushWarn(e.message || '邮件草稿生成失败', true);
  }
}

async function genReason(useLlm) {
  if (!pushState.rid) return;
  const btn = useLlm ? $('#pushGenLlm') : $('#pushGenRule');
  const old = btn.innerHTML;
  btn.disabled = true;
  btn.innerHTML = `<svg><use href="#i-clock"/></svg>生成中…`;
  try {
    const r = await api(`/api/records/${pushState.rid}/reason`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ use_llm: useLlm, force: true }),
    });
    $('#pushReason').value = r.reason || '';
    syncReasonSrc(r.source);
    $('#pushReasonHint').textContent = r.source === 'llm' ? '已用大模型重写，可在上方继续手改' : '大模型不可用，已用规则模板生成';
    toast(r.source === 'llm' ? 'AI 理由已生成' : '已用规则模板生成理由', 'ok', '推选理由');
    await refreshMail();
  } catch (e) {
    toast(e.message, 'err', '理由生成失败');
  } finally {
    btn.disabled = false;
    btn.innerHTML = old;
  }
}

async function saveReasonOnly() {
  if (!pushState.rid) return;
  const text = $('#pushReason').value.trim();
  if (!text) { toast('理由不能为空', 'err', '保存失败'); return; }
  try {
    await api(`/api/records/${pushState.rid}/reason`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ reason: text }),
    });
    syncReasonSrc('hr');
    $('#pushReasonHint').textContent = '已保存为 HR 手写理由，重算不会覆盖';
    toast('理由已保存', 'ok', '推选理由');
    loadDecisions();
    await refreshMail();
  } catch (e) { toast(e.message, 'err', '保存失败'); }
}

async function doPush() {
  if (!pushState.rid || pushState.busy) return;
  const rec = pushState.rec;
  const reason = $('#pushReason').value.trim();
  if (!reason) { toast('请先生成或填写推选理由', 'err', '推送'); return; }
  const skip = $('#pushConfirmDecision').checked;
  const tip = skip
    ? `未标记「邀约」也要推送给 ${rec.candidate}？邮件会直接发出。`
    : `确认把 ${rec.candidate} 推送给用人部门？邮件将发送至 ${$('#pushTo').textContent}。`;
  if (!confirm(tip)) return;

  setPushBusy(true, '发送中…');
  try {
    // 先把当前编辑框里的理由存下来，保证「存档理由 == 邮件里的理由」
    await api(`/api/records/${pushState.rid}/reason`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ reason }),
    });
    const r = await api(`/api/records/${pushState.rid}/push`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ confirm_decision: skip, use_llm: true }),
    });
    if (r.status === 'sent') {
      toast(r.message, 'ok', '推送成功');
      closePush();
      loadDecisions();
    } else {
      showPushWarn(r.message, true);
      toast('推送失败', 'err', '请检查 SMTP 配置');
    }
  } catch (e) {
    showPushWarn(e.message, true);
    toast(e.message, 'err', '推送被拦截');
  } finally {
    setPushBusy(false);
  }
}

function bindPushModal() {
  $('#pushClose').addEventListener('click', closePush);
  $('#pushCancel').addEventListener('click', closePush);
  $('#pushMask').addEventListener('click', (ev) => { if (ev.target.id === 'pushMask') closePush(); });
  $('#pushSend').addEventListener('click', doPush);
  $('#pushGenLlm').addEventListener('click', () => genReason(true));
  $('#pushGenRule').addEventListener('click', () => genReason(false));
  $('#pushSaveReason').addEventListener('click', saveReasonOnly);
  $('#pushRefreshMail').addEventListener('click', async () => {
    $('#pushSubject').textContent = '生成中…';
    $('#pushMailBody').textContent = '正在重新生成邮件草稿…';
    await refreshMail();
  });
  document.addEventListener('keydown', (ev) => {
    if (ev.key === 'Escape' && !$('#pushMask').hidden) closePush();
  });
}

async function loadSelfcheck() {
  try {
    const d = await api('/api/selfcheck');
    // 顶栏与侧栏状态
    const setDot = (el, ok) => { if (el) el.className = `dot ${ok ? 'ok' : 'bad'}`; };
  } catch (e) {
    toast(e.message, 'err', '状态读取失败');
  }
}

/* ---------------- 账号中心 ---------------- */
async function loadAccount() {
  const [me, sec] = await Promise.all([api('/api/auth/me'), api('/api/auth/security')]);
  state.me = me;
  $('#acctUserInput').value = me.username;
  $('#acctNameInput').value = me.display_name || me.username;
  $('#acctRoleInput').value = me.role || 'hr';
  $('#acctSos').innerHTML = sec.sos.map((s) =>
    `<li><svg><use href="#i-check"/></svg><span>${escapeHtml(s)}</span></li>`).join('');
}

/** 撤销修改：只回填输入框，不额外请求 */
function revertProfileInputs() {
  const me = state.me;
  if (!me) return;
  $('#acctNameInput').value = me.display_name || me.username;
  $('#acctRoleInput').value = me.role || 'hr';
  toast('已撤销未保存的修改', 'info', '账号资料');
}

async function saveProfile() {
  const btn = $('#saveProfile');
  const uname = $('#acctUserInput').value.trim();
  const name = $('#acctNameInput').value.trim();
  if (!uname) { toast('用户名不能为空', 'err', '保存失败'); return; }
  if (!/^[A-Za-z0-9_]{3,20}$/.test(uname)) { toast('用户名需 3-20 位，仅限字母、数字、下划线', 'err', '保存失败'); return; }
  if (!name) { toast('显示名不能为空', 'err', '保存失败'); return; }
  try {
    const r = await withLoading(btn, () => api('/api/auth/profile', {
      method: 'POST', body: { username: uname, display_name: name, role: $('#acctRoleInput').value },
    }));
    localStorage.setItem('headhunter.user', JSON.stringify(r.user));
    toast(r.message, 'ok', '资料已更新');
    await loadAccount();
    paintAccountChip(r.user);
  } catch (e) { toast(e.message, 'err', '保存失败'); }
}

/* ---------------- 改密码：两次输入实时比对 ---------------- */
// 提示分三态：空（不显示）/ 红字（不一致或过短）/ 绿字（一致且合规）
function setPwdHint(text, state) {
  const hint = $('#pwdNew2Hint');
  if (!hint) return;
  const line = $('#pwdNew2').closest('.pwd-line');
  if (!text) {
    hint.textContent = '';
    hint.className = 'field-hint';
    if (line) line.classList.remove('is-bad');
    return;
  }
  hint.textContent = text;
  hint.className = `field-hint on${state === 'ok' ? ' ok' : ''}`;
  if (line) line.classList.toggle('is-bad', state !== 'ok');
}

function checkPwdMatch() {
  const newP = $('#pwdNew').value;
  const new2 = $('#pwdNew2').value;
  // 两个框都还空着时不打扰；确认框已有内容但新密码为空，说明先填了下面那个
  if (!newP && !new2) { setPwdHint(''); return; }
  if (!new2) { setPwdHint(''); return; }           // 还没开始确认，不提前报错
  if (newP.length < 6) { setPwdHint('新密码至少 6 位'); return; }
  if (newP !== new2) { setPwdHint('两次输入的新密码不一致'); return; }
  setPwdHint('两次输入一致', 'ok');
}

async function savePassword() {
  const oldP = $('#pwdOld').value, newP = $('#pwdNew').value, new2 = $('#pwdNew2').value;
  if (!oldP) { toast('请输入当前密码', 'err', '无法更新'); return; }
  // 长度与一致性走行内红字，比弹 toast 更靠近出错的位置
  if (newP.length < 6 || newP !== new2) { checkPwdMatch(); return; }
  try {
    const r = await withLoading($('#savePwd'), () => api('/api/auth/password', {
      method: 'POST', body: { old_password: oldP, new_password: newP },
    }));
    toast(r.message, 'ok', '密码已更新');
    ['#pwdOld', '#pwdNew', '#pwdNew2'].forEach((s) => { $(s).value = ''; });
    setPwdHint('');          // 清空后收起提示，不留"一致"绿字
  } catch (e) { toast(e.message, 'err', '更新失败'); }
}

async function doLogout() {
  if (!confirm('退出后需要重新输入账号密码，确定退出？')) return;
  try { await api('/api/auth/logout', { method: 'POST' }); } catch (e) { /* 忽略 */ }
  localStorage.removeItem('headhunter.token');
  localStorage.removeItem('headhunter.user');
  window.location.href = '/login';
}

function paintAccountChip(u) {
  if (!u) return;
  $('#userAvatar').textContent = (u.display_name || u.username || 'HR').slice(0, 1);
  $('#userName').textContent = u.display_name || u.username;
  $('#userRole').textContent = u.role === 'admin' ? '管理员' : 'HR';
  $('#umName').textContent = u.display_name || u.username;
  $('#umSub').textContent = `@${u.username}`;
}

/* ---------------- 对话 ---------------- */
function chatAppend(role, html, opts = {}) {
  const scroll = $('#chatScroll');
  const el = document.createElement('div');
  el.className = `msg ${role === 'user' ? 'user' : 'bot'}`;
  const avatar = role === 'user'
    ? '<div class="msg-avatar user">我</div>'
    : '<div class="msg-avatar bot"><svg><use href="#i-spark"/></svg></div>';
  el.innerHTML = `${avatar}<div class="bubble">${html}</div>`;
  scroll.appendChild(el);
  scroll.scrollTop = scroll.scrollHeight;
  return el;
}

async function sendChat(text) {
  const msg = (text || '').trim();
  if (!msg || state.chatBusy) return;
  state.chatBusy = true;
  $('#chatSend').disabled = true;
  chatAppend('user', `<p>${escapeHtml(msg)}</p>`);

  const pending = chatAppend('bot', '<span class="typing"><i></i><i></i><i></i></span>');
  try {
    const data = await api('/api/chat', { method: 'POST', body: { message: msg, draft: state.draft } });
    if (data.draft) state.draft = data.draft;
    const bubble = pending.querySelector('.bubble');
    bubble.innerHTML = mdToHtml(data.reply || '（无回复）');
    bindCandLinks(bubble);
    $('#chatScroll').scrollTop = $('#chatScroll').scrollHeight;
    if (/已发送至/.test(data.reply || '')) toast('邮件已发送', 'ok', '触达成功');
  } catch (e) {
    pending.querySelector('.bubble').innerHTML = `<p style="color:var(--danger)">处理失败：${escapeHtml(e.message)}</p>`;
  } finally {
    state.chatBusy = false;
    $('#chatSend').disabled = false;
    $('#chatInput').focus();
  }
}

function bindCandLinks(root) {
  $$('.cand-link', root).forEach((a) => a.addEventListener('click', (e) => {
    e.preventDefault();
    openDrawer(a.dataset.cand);
  }));
}

/* ---------------- 事件绑定 ---------------- */
function bindEvents() {
  // 导航
  $$('.nav-item').forEach((a) => a.addEventListener('click', (e) => {
    e.preventDefault();
    location.hash = `#/${a.dataset.view}`;
  }));
  window.addEventListener('hashchange', () => go(currentView()));

  // 主题
  initTheme();

  // 对话
  const input = $('#chatInput');
  const autoGrow = () => {
    input.style.height = 'auto';
    input.style.height = Math.min(input.scrollHeight, 132) + 'px';
  };
  input.addEventListener('input', autoGrow);
  input.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); const v = input.value; input.value = ''; autoGrow(); sendChat(v); }
  });
  $('#chatSend').addEventListener('click', () => { const v = input.value; input.value = ''; autoGrow(); sendChat(v); });
  $$('#chatSuggest button').forEach((b) => b.addEventListener('click', () => sendChat(b.dataset.q)));
  $('#chatClear').addEventListener('click', () => {
    $('#chatScroll').innerHTML = '';
    state.draft = null;
    chatAppend('bot', '<p>对话已清空，重新开始吧 👋</p>');
  });

  // 人才库
  let searchTimer = null;
  $('#candSearch').addEventListener('input', (e) => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => { state.q = e.target.value; loadCandidates(); }, 320);
  });
  // 表头筛选：学历 / 年限（点击列名出现，再次点击隐藏，两菜单互斥）
  buildFilterMenus();
  const closeMenus = () => { $('#fEduMenu').hidden = true; $('#fExpMenu').hidden = true; };
  $('#fEduBtn').addEventListener('click', (e) => {
    e.stopPropagation();
    const willOpen = $('#fEduMenu').hidden;
    closeMenus();
    $('#fEduMenu').hidden = !willOpen;
  });
  $('#fExpBtn').addEventListener('click', (e) => {
    e.stopPropagation();
    const willOpen = $('#fExpMenu').hidden;
    closeMenus();
    $('#fExpMenu').hidden = !willOpen;
  });
  $('#fEduMenu').addEventListener('click', (e) => e.stopPropagation());
  $('#fExpMenu').addEventListener('click', (e) => e.stopPropagation());
  document.addEventListener('click', closeMenus);
  $('#candRefresh').addEventListener('click', () => { loadCandidates(); loadOverview().then(renderStats).catch(() => {}); });
  $('#candExport').addEventListener('click', () => toast('正在导出 CSV…', 'info', '导出'));

  // 职位
  $('#posAdd').addEventListener('click', addPosition);
  $('#jobsRefresh').addEventListener('click', loadPositions);

  // 公司
  $('#compLookup').addEventListener('click', lookupCompany);
  $('#compInput').addEventListener('keydown', (e) => { if (e.key === 'Enter') lookupCompany(); });

  // 分析
  $('#analRun').addEventListener('click', () => runAnalysis(false, false));
  $('#analRefresh').addEventListener('click', () => runAnalysis(true, false));
  $('#analRisk').addEventListener('click', () => runAnalysis(true, true));
  $('#analInput').addEventListener('keydown', (e) => { if (e.key === 'Enter') runAnalysis(false, false); });

  // 匹配策略
  $('#wSkill').addEventListener('input', syncWeightUI);
  $('#wThr').addEventListener('input', syncWeightUI);
  // 经验滑块可独立拖动：拖经验时技能互补（技能 = 100 - 经验）
  $('#wExp').addEventListener('input', () => {
    $('#wSkill').value = 100 - Number($('#wExp').value);
    syncWeightUI();
  });
  $$('#presetRow .chip-btn').forEach((b) => b.addEventListener('click', () => {
    $('#wSkill').value = b.dataset.skill;
    $('#wThr').value = b.dataset.thr;
    syncWeightUI();
  }));
  $('#wSave').addEventListener('click', saveWeights);
  $('#wRecalc').addEventListener('click', recalcAll);
  $$('#decisionChips .chip-btn').forEach((b) => b.addEventListener('click', () => {
    $$('#decisionChips .chip-btn').forEach((x) => x.classList.remove('on'));
    b.classList.add('on');
    state.decisionFilter = b.dataset.d;
    renderDecisions();
  }));
  $('#decRefresh').addEventListener('click', () => { loadDecisions(); loadStrategy(); });

  // 账号中心
  $('#saveProfile').addEventListener('click', saveProfile);
  $('#resetProfile').addEventListener('click', revertProfileInputs);
  $('#savePwd').addEventListener('click', savePassword);
  // 两次密码一致性：边输边提示，不必等点按钮
  ['#pwdNew', '#pwdNew2'].forEach((s) => {
    $(s).addEventListener('input', checkPwdMatch);
    $(s).addEventListener('blur', checkPwdMatch);
  });
  // 离开设置页或离开密码区时收起提示，避免残留红字
  $('#pwdOld').addEventListener('input', () => setPwdHint(''));
  $('#gotoPwd').addEventListener('click', () => { $('#pwdOld').focus(); $('#pwdForm').scrollIntoView({ behavior: 'smooth', block: 'center' }); });
  $('#sideLogoutOthers').addEventListener('click', async () => {
    try {
      const r = await api('/api/auth/logout-others', { method: 'POST' });
      toast(r.message, 'ok', '会话管理');
      loadAccount().catch(() => {});
    } catch (e) { toast(e.message, 'err', '操作失败'); }
  });
  $('#sideLogoutNow').addEventListener('click', doLogout);
  // 密码框显示 / 隐藏
  $$('#view-settings .pwd-eye').forEach((b) => b.addEventListener('click', () => {
    const input = $(`#${b.dataset.for}`);
    const show = input.type === 'password';
    input.type = show ? 'text' : 'password';
    b.innerHTML = `<svg><use href="#i-${show ? 'eye-off' : 'eye'}"/></svg>`;
  }));

  // 抽屉
  $('#drawerClose').addEventListener('click', closeDrawer);
  $('#drawerMask').addEventListener('click', closeDrawer);
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape') closeDrawer(); });

  // 推送候选人弹窗
  bindPushModal();
  // 公司详情弹窗
  bindCompanyModal();
}

/* ---------------- 启动 ---------------- */
function bindAccount() {
  const chip = $('#userChip'), menu = $('#userMenu');
  const paint = (u) => paintAccountChip(u);
  const cached = localStorage.getItem('headhunter.user');
  if (cached) { try { paint(JSON.parse(cached)); } catch (e) { /* ignore */ } }

  chip.addEventListener('click', (e) => {
    e.stopPropagation();
    menu.hidden = !menu.hidden;
  });
  document.addEventListener('click', () => { menu.hidden = true; });
  menu.addEventListener('click', (e) => e.stopPropagation());

  $('#logoutBtn').addEventListener('click', doLogout);

  api('/api/auth/me').then((u) => {
    localStorage.setItem('headhunter.user', JSON.stringify(u));
    paint(u);
  }).catch(() => { /* 401 已由 api 统一跳转 */ });
}

(async function boot() {
  if (!localStorage.getItem('headhunter.token')) { window.location.href = '/login'; return; }
  bindAccount();
  bindEvents();
  renderChips();
  state.view = currentView();
  go(state.view);
  loadRail();
  // 首次静默预热：概览（导航角标）+ 连接状态（侧栏/顶栏圆点）
  loadOverview().catch(() => {});
  loadSelfcheck().catch(() => {});
})();
