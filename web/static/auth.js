/* 登录 / 注册页逻辑：零依赖，纯原生 */
(() => {
  const $ = (s) => document.querySelector(s);
  const TOKEN_KEY = 'headhunter.token';
  const REMEMBER_KEY = 'headhunter.remember';
  const state = { mode: 'login' };

  /* ---------- 工具 ---------- */
  const api = async (path, options = {}) => {
    const opt = {
      headers: { 'Content-Type': 'application/json' },
      ...options,
    };
    if (opt.body && typeof opt.body !== 'string') opt.body = JSON.stringify(opt.body);
    const res = await fetch(path, opt);
    let json = null;
    try { json = await res.json(); } catch (e) { /* ignore */ }
    if (!res.ok || !json || json.ok === false) {
      throw new Error((json && json.error) || `请求失败（HTTP ${res.status}）`);
    }
    return json.data;
  };

  const note = (msg, type = 'err') => {
    const el = $('#formNote');
    if (!msg) { el.hidden = true; el.textContent = ''; return; }
    el.hidden = false;
    el.className = `form-note ${type === 'ok' ? 'ok' : ''}`;
    el.textContent = msg;
  };

  /* ---------- 记住密码（localStorage，密码 base64 存储） ---------- */
  const loadRemembered = () => {
    try {
      const raw = localStorage.getItem(REMEMBER_KEY);
      if (!raw) return null;
      const obj = JSON.parse(raw);
      if (!obj || !obj.u || !obj.p) return null;
      return { u: obj.u, p: atob(obj.p) };
    } catch (e) { return null; }
  };
  const saveRemembered = (u, p) => {
    localStorage.setItem(REMEMBER_KEY, JSON.stringify({ u, p: btoa(p) }));
  };
  const clearRemembered = () => localStorage.removeItem(REMEMBER_KEY);

  /* ---------- 模式切换（标题即页面名：登录 / 注册） ---------- */
  const switchMode = (mode, opts = {}) => {
    state.mode = mode;
    const isReg = mode === 'register';
    $('#formTitle').textContent = isReg ? '注册' : '登录';
    $('#nameField').hidden = !isReg;
    $('#confirmField').hidden = !isReg;
    $('#meter').hidden = !isReg;
    $('#rememberRow').hidden = isReg;
    $('#submitText').textContent = isReg ? '注册' : '登录';
    $('#password').setAttribute('autocomplete', isReg ? 'new-password' : 'current-password');
    $('#altText').innerHTML = isReg
      ? '已有账号？<button type="button" class="link" id="switchBtn">直接登录</button>'
      : '还没账号？<button type="button" class="link" id="switchBtn">立即注册</button>';
    $('#switchBtn').addEventListener('click', () => switchMode(isReg ? 'login' : 'register'));
    if (!opts.keepNote) note('');
    syncMeter();
  };

  /* ---------- 密码强度 ---------- */
  const syncMeter = () => {
    if (state.mode !== 'register') return;
    const v = $('#password').value;
    let score = 0;
    if (v.length >= 6) score++;
    if (v.length >= 10 || /[^A-Za-z0-9]/.test(v)) score++;
    if (/[A-Z]/.test(v) && /[a-z]/.test(v) && /\d/.test(v)) score++;
    $('#meter').className = `meter s${score}`;
  };

  /* ---------- 提交 ---------- */
  const submit = async (e) => {
    e.preventDefault();
    const btn = $('#submitBtn');
    const username = $('#username').value.trim();
    const password = $('#password').value;

    if (!username || !password) { note('请填写用户名和密码'); return; }
    if (state.mode === 'register') {
      if (!/^[A-Za-z0-9_]{3,20}$/.test(username)) { note('用户名需 3-20 位，仅限字母、数字、下划线'); return; }
      if (password.length < 6) { note('密码至少 6 位'); return; }
      if (password !== $('#password2').value) { note('两次输入的密码不一致'); return; }
    }

    btn.disabled = true;
    const oldText = $('#submitText').textContent;
    $('#submitText').textContent = state.mode === 'register' ? '注册中…' : '登录中…';
    try {
      const body = state.mode === 'register'
        ? { username, password, display_name: $('#displayName').value.trim() }
        : { username, password };
      if (state.mode === 'register') {
        await api('/api/auth/register', { method: 'POST', body });
        btn.disabled = false;
        const name = $('#username').value.trim();
        switchMode('login');
        $('#username').value = name;
        $('#password').value = '';
        $('#password2').value = '';
        note('注册成功！请使用新账号登录', 'ok');
        $('#password').focus();
        return;
      }
      const data = await api('/api/auth/login', { method: 'POST', body });
      // 记住密码：仅保存账号密码，下次自动填入；登录本身仍需点击按钮
      if ($('#rememberPwd').checked) saveRemembered(username, password);
      else clearRemembered();
      localStorage.setItem(TOKEN_KEY, data.token);
      localStorage.setItem('headhunter.user', JSON.stringify(data.user));
      note(`欢迎，${data.user.display_name}！正在进入工作台…`, 'ok');
      setTimeout(() => { window.location.href = '/'; }, 480);
    } catch (err) {
      note(err.message);
      btn.disabled = false;
      $('#submitText').textContent = oldText;
    }
  };

  /* ---------- 密码显示 / 隐藏 ---------- */
  const bindEye = (btnSel, inputSel) => {
    $(btnSel).addEventListener('click', () => {
      const input = $(inputSel);
      const isPwd = input.type === 'password';
      input.type = isPwd ? 'text' : 'password';
      $(btnSel).innerHTML = `<svg><use href="#i-${isPwd ? 'eye-off' : 'eye'}"/></svg>`;
    });
  };

  /* ---------- 初始化 ---------- */
  const init = async () => {
    $('#authForm').addEventListener('submit', submit);
    bindEye('#eyeBtn', '#password');
    bindEye('#eyeBtn2', '#password2');

    // 已登录直接进工作台
    const token = localStorage.getItem(TOKEN_KEY);
    if (token) {
      try {
        await api('/api/auth/me', { headers: { 'Content-Type': 'application/json', 'X-Auth-Token': token } });
        window.location.href = '/';
        return;
      } catch (e) { localStorage.removeItem(TOKEN_KEY); }
    }

    // 初始模式：?mode=register 或 remembered 存在则进登录页
    const params = new URLSearchParams(location.search);
    const startMode = params.get('mode') === 'register' ? 'register' : 'login';
    switchMode(startMode, { keepNote: true });

    // 记住密码：自动填入账号和密码（不自动登录）
    const remembered = loadRemembered();
    if (remembered && startMode === 'login') {
      $('#username').value = remembered.u;
      $('#password').value = remembered.p;
      $('#rememberPwd').checked = true;
    }

    requestAnimationFrame(() => switchMode(startMode, { keepNote: true }));
  };

  init();
})();
