/* Independent team UI. All server content is rendered as text, never HTML. */
(() => {
  'use strict';
  if (window.TravelGuardian) return;
  const interval = 10 * 60 * 1000;
  let team = null, root, notice, teamBox, proposalBox, eventBox, resultBox;
  let timer, checking = false, refreshId = 0, pending = false, dirty = false;
  let joinToken = new URL(location.href).searchParams.get('join');
  const list = value => Array.isArray(value) ? value : [];
  const text = value => typeof value === 'string' ? value : value == null ? '' : JSON.stringify(value);
  function el(parent, tag, content, cls) {
    const node = document.createElement(tag);
    if (content != null) node.textContent = text(content);
    if (cls) node.className = cls;
    if (parent) parent.append(node);
    return node;
  }
  function status(message, error = false) {
    notice.textContent = message;
    notice.classList.toggle('tg-error', error);
  }
  function button(parent, title, action) {
    const b = el(parent, 'button', title); b.type = 'button';
    b.onclick = () => run(action, b); return b;
  }
  async function api(path, method = 'GET', body) {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 90000);
    try {
      const response = await fetch(path, { method, credentials: 'same-origin', signal: controller.signal,
        headers: body === undefined ? {} : { 'Content-Type': 'application/json' },
        body: body === undefined ? undefined : JSON.stringify(body) });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) {
        const error = new Error(response.status === 409 ? '資料已更新，已重新載入。請核對後重試。' :
          response.status === 403 ? '你沒有此操作的權限。請更新團隊資料。' :
          response.status === 401 ? '登入狀態已失效，請重新載入頁面。' : `請求失敗（${response.status}），請稍後重試。`);
        error.status = response.status; throw error;
      }
      return data;
    } finally { clearTimeout(timeout); }
  }
  async function run(action, b) {
    if (pending) return;
    pending = true; if (b) b.disabled = true;
    root.setAttribute('aria-busy', 'true');
    try { await action(); }
    catch (error) {
      if (error.status === 409) {
        dirty = false;
        try { await refresh(); } catch (_) { /* Preserve the conflict message. */ }
      }
      status(error.name === 'AbortError' ? '請求逾時，請更新資料確認結果後再重試。' : error.message || '網絡錯誤，請重試。', true);
    } finally { pending = false; if (b) b.disabled = false; root.removeAttribute('aria-busy'); }
  }
  function field(parent, label, type = 'text', value = '') {
    const wrapper = el(parent, 'label', label);
    const input = el(wrapper, 'input'); input.type = type; input.setAttribute('aria-label', label);
    if (type === 'checkbox') input.checked = Boolean(value); else input.value = value ?? '';
    return input;
  }
  function select(parent, label, options, value) {
    const wrapper = el(parent, 'label', label), node = el(wrapper, 'select'); node.setAttribute('aria-label', label);
    options.forEach(([v, title]) => { const option = el(node, 'option', title); option.value = v; });
    node.value = value; return node;
  }
  function form(parent, action) {
    const node = el(parent, 'form');
    node.onsubmit = event => { event.preventDefault(); if (node.reportValidity()) run(() => action(node), node.querySelector('[type=submit]')); };
    return node;
  }
  function submit(parent, title) { const b = el(parent, 'button', title); b.type = 'submit'; return b; }
  function sources(parent, values) {
    const items = list(values); if (!items.length) return;
    const box = el(parent, 'div', null, 'tg-sources'); el(box, 'strong', '資料來源');
    items.forEach(source => {
      if (source && typeof source==='object') el(box,'p',`${source.source || source.name || '來源'} · ${source.data_kind || (source.demo ? 'demo' : '未標示')} · ${source.fetched_at || '時間未知'}`);
      const raw = typeof source === 'string' ? source : source?.url || source?.source_url;
      try {
        const url = new URL(raw);
        if (!['https:', 'http:'].includes(url.protocol) || url.username || url.password || url.searchParams.has('join') || url.searchParams.has('token')) return;
        const a = el(box, 'a', source.title || source.name || url.hostname);
        a.href = url.href; a.target = '_blank'; a.rel = 'noopener noreferrer'; a.referrerPolicy = 'no-referrer';
      } catch (_) { /* Invalid and unsafe URLs are omitted. */ }
    });
  }
  function preferences(parent) {
    const me = list(team.members).find(member => member.id === team.member_id);
    if (!me) return;
    const revision = team.revision, pref = me.preferences || {};
    const details = el(parent, 'details'); el(details, 'summary', '我的偏好（只修改自己）');
    const f = form(details, async () => {
      const preferences = { diet: diet.value.split(/[,，]/).map(v => v.trim()).filter(Boolean),
        budget: budget.value === '' ? null : { amount: Number(budget.value), currency: currency.value },
        walking_limit_m: walking.value === '' ? null : Number(walking.value), accessibility: access.checked,
        soft: Object.fromEntries(Object.entries(soft).map(([key, input]) => [key, Number(input.value)])) };
      await api('/team/preferences', 'PATCH', { preferences, expected_revision: revision });
      dirty = false; await refresh(); status('個人偏好已儲存。');
    });
    f.oninput = () => { dirty = true; };
    const diet = field(f, '飲食限制（逗號分隔）', 'text', list(pref.diet).join(', ')); diet.maxLength = 500;
    const grid = el(f, 'div', null, 'tg-grid');
    const budget = field(grid, '預算（留空為未設定）', 'number', pref.budget?.amount); budget.min = '0'; budget.step = '0.01';
    const currencies = Array.from(new Set(['MOP', 'HKD', 'CNY', 'USD', pref.budget?.currency].filter(Boolean)));
    const currency = select(grid, '貨幣', currencies.map(v => [v, v]), pref.budget?.currency || 'MOP');
    const walking = field(grid, '步行上限（米，留空為未設定）', 'number', pref.walking_limit_m); walking.min = '0'; walking.step = '1';
    const access = field(f, '需要無障礙設施', 'checkbox', pref.accessibility);
    el(f, 'p', '興趣權重：0 不重視 — 1 很重視');
    const soft = {};
    for (const [key, label] of [['photo', '攝影'], ['culture', '文化'], ['shopping', '購物'], ['rest', '休息']]) {
      const input = field(f, label, 'range', pref.soft?.[key] ?? 0.5); input.min = '0'; input.max = '1'; input.step = '0.1';
      const output = el(input.parentElement, 'output', input.value); input.oninput = () => { output.textContent = input.value; };
      soft[key] = input;
    }
    submit(f, '儲存我的偏好');
  }
  function renderTeam() {
    teamBox.replaceChildren();
    el(teamBox, 'h3', team.name || '我的旅行團隊');
    el(teamBox, 'p', `${team.role === 'owner' ? '團主' : '成員'} · 行程版本 ${team.version} · ${list(team.members).length} 位成員`);
    const roster = el(teamBox, 'ul', null, 'tg-roster');
    list(team.members).forEach(member => {
      const row = el(roster, 'li'); el(row, 'span', `${member.name}${member.id === team.member_id ? '（我）' : ''}`);
      if (team.role === 'owner' && member.id !== team.member_id) button(row, '移除', async () => {
        if (!window.confirm(`移除成員「${member.name}」？`)) return;
        await api(`/team/members/${encodeURIComponent(member.id)}`, 'DELETE'); await refresh(); status('成員已移除。');
      });
    });
    preferences(teamBox);
    if (team.role === 'owner') {
      const invite = el(teamBox, 'div', null, 'tg-actions');
      let freshUrl = null;
      button(invite, '產生邀請連結', async () => {
        const data = await api('/team/invite', 'POST', { revoke: false }); freshUrl = data.invite_url || null;
        copy.disabled = !freshUrl;
        status(freshUrl ? '邀請已產生，按「複製邀請」分享。' : '既有邀請不會再次顯示；請撤銷後產生新邀請。');
      });
      const copy = button(invite, '複製邀請', async () => {
        if (!freshUrl) return;
        if (!navigator.clipboard?.writeText) throw new Error('此瀏覽器無法使用剪貼簿，請使用 HTTPS 或 localhost 開啟。');
        await navigator.clipboard.writeText(freshUrl); status('邀請已複製。');
      }); copy.disabled = true;
      button(invite, '撤銷邀請', async () => {
        await api('/team/invite', 'POST', { revoke: true }); freshUrl = null; copy.disabled = true; status('舊邀請已撤銷。');
      });
    }
  }
  // Whitelisted itinerary fields avoid dumping credentials, prompts or thought traces.
  function itinerary(parent, value, heading) {
    const box = el(parent, 'section'); el(box, 'h4', heading);
    if (!value) { el(box, 'p', '尚無行程'); return; }
    el(box, 'p', value.city || '城市未設定');
    const walk = (data, depth = 0) => {
      if (data == null || depth > 8) return;
      if (Array.isArray(data)) { data.forEach(item => walk(item, depth + 1)); return; }
      if (typeof data !== 'object') { el(box, 'p', text(data)); return; }
      const labels = ['date', 'day', 'time', 'arrival_time', 'end_time', 'name', 'place', 'title', 'description', 'visit_note', 'arrival_transport', 'duration_min', 'distance_m', 'price', 'currency', 'flight_no', 'from_', 'to', 'depart_time', 'arrive_time', 'check_in', 'check_out'];
      const line = labels.filter(key => data[key] != null && typeof data[key] !== 'object').map(key => `${key}: ${data[key]}`).join(' · ');
      if (line) el(box, 'p', line);
      if (data.nodes && typeof data.nodes === 'object') {
        const seen = new Set(); let id = data.head_id;
        while (id != null && data.nodes[id] && !seen.has(id)) {
          seen.add(id); walk(data.nodes[id], depth + 1); id = data.nodes[id].next_id;
        }
      }
      if (data.days && !Array.isArray(data.days)) Object.keys(data.days).sort().forEach(key => walk(data.days[key], depth + 1));
      else if (data.days) walk(data.days, depth + 1);
      ['day_timeline', 'stops', 'activities', 'items', 'itinerary', 'flights', 'hotels', 'attractions', 'schedule', 'legs'].forEach(key => { if (data[key]) walk(data[key], depth + 1); });
    };
    const count = box.childElementCount;
    walk(value.trip_plan); walk(value.nearby_plan);
    if (box.childElementCount === count) el(box, 'p', '尚無可顯示的行程細節');
  }
  function renderProposals(values) {
    proposalBox.replaceChildren(); el(proposalBox, 'h3', '待決策提案');
    if (!list(values).length) el(proposalBox, 'p', '目前沒有提案。');
    list(values).forEach(proposal => {
      const card = el(proposalBox, 'article', null, 'tg-card');
      el(card, 'h4', proposal.reason || '行程調整建議');
      el(card, 'p', `${proposal.status} · 基於版本 ${proposal.base_version} · ${proposal.created_at || ''}`);
      if (proposal.itinerary?.mode === 'demo' || proposal.mode === 'demo') el(card, 'strong', '演示提案（模擬資料）');
      const compare = el(card, 'details'); el(compare, 'summary', '比較目前行程與提案');
      const columns = el(compare, 'div', null, 'tg-compare');
      itinerary(columns, team?.itinerary, '目前已確認'); itinerary(columns, proposal.itinerary, '提案（尚未套用）');
      sources(card, proposal.sources);
      if (team?.role === 'owner' && proposal.status === 'pending') {
        const stale = proposal.base_version !== team.version || proposal.base_revision !== team.revision;
        if (stale) el(card, 'p', '此提案版本已過期，請重新規劃。', 'tg-error');
        const expectedVersion = team.version;
        const accept = button(card, '接受並更新行程', async () => {
          await api(`/proposals/${encodeURIComponent(proposal.id)}/accept`, 'POST', { expected_version: expectedVersion });
          await refresh();
          if (typeof window.refreshSchedule === 'function') await window.refreshSchedule();
          status('提案已接受，行程已更新。');
        }); accept.disabled = stale;
        button(card, '拒絕', async () => {
          await api(`/proposals/${encodeURIComponent(proposal.id)}/reject`, 'POST', { expected_version: expectedVersion });
          await refresh(); status('提案已拒絕。');
        });
      } else if (proposal.status === 'pending') el(card, 'p', '等待團主決策。');
    });
  }
  function renderEvents(values, checkedAt, cached) {
    eventBox.replaceChildren(); el(eventBox, 'h3', '行程事件');
    el(eventBox, 'small', checkedAt ? `檢查時間：${checkedAt}${cached ? '（快取）' : ''}` : '尚未手動檢查；頁面可見時每 10 分鐘檢查。');
    if (!list(values).length) el(eventBox, 'p', '目前沒有事件。');
    list(values).forEach(event => {
      const card = el(eventBox, 'article', null, 'tg-card');
      el(card, 'strong', event.title || event.type || '行程提醒');
      el(card, 'p', event.summary || event.description || event.message || ''); sources(card, event.sources);
    });
  }
  async function refresh() {
    const id = ++refreshId;
    const next = await api('/team');
    if (id !== refreshId) return;
    team = next;
    if (!dirty) renderTeam();
    renderProposals(team.proposals); renderEvents(team.events);
    try {
      const data = await api('/proposals');
      if (id === refreshId) renderProposals(data.proposals);
    } catch (error) { status('團隊已載入，但提案更新失敗；可按更新重試。', true); }
  }
  async function checkEvents(options = {}) {
    if (document.hidden || checking) return;
    checking = true;
    try {
      const demo = options.demo === true;
      const data = await api('/events/check', 'POST', { demo });
      await refresh(); renderEvents(data.events, data.checked_at, data.cached);
      if (data.proposals) renderProposals(data.proposals);
      status(demo ? '演示事件檢查完成（模擬資料）。' : '真實事件檢查完成。');
      return data;
    } finally { checking = false; }
  }
  function showResult(data, context = {}) {
    if (!root) mount();
    resultBox.replaceChildren();
    el(resultBox, 'h3', context.mode === 'demo' || data.mode === 'demo' ? '演示結果（模擬資料）' : '規劃結果');
    el(resultBox, 'p', data.chat_reply || '處理完成。');
    const results = Array.isArray(data.skill_results) ? data.skill_results : Object.entries(data.skill_results || {}).map(([name, value]) => ({ name, ...typeof value === 'object' && value !== null ? value : { summary: value } }));
    results.forEach(result => {
      const card = el(resultBox, 'article', null, 'tg-card');
      el(card, 'strong', result.name || result.skill || result.skill_id || result.id || '技能');
      if (typeof result.summary === 'string') el(card, 'p', result.summary);
      if (typeof result.status === 'string') el(card, 'small', result.status);
      const details=el(card,'details'); el(details,'summary','候選、決策與待確認事項');
      list(result.candidates).forEach(p => el(details,'p',p.name || p.id));
      list(result.actions).forEach(action => el(details,'p',action.ready_to_show || action.decision || action.text || text(action)));
      list(result.unknowns).forEach(item => el(details,'p','待確認：'+text(item)));
      list(result.errors).forEach(item => el(details,'p','資料問題：'+text(item)));
      sources(card, result.evidence || result.sources);
    });
    list(data.conflicts).forEach(item => el(resultBox,'p','衝突：'+text(item)));
    sources(resultBox, data.sources);
    if (data.proposal) el(resultBox, 'p', '已產生行程提案，需團主接受才會套用。');
    return refresh().catch(() => status('結果已收到，但團隊更新失敗，請按更新重試。', true));
  }
  function planner(parent) {
    const details = el(parent, 'details'); details.open = true; el(details, 'summary', 'Travel Guardian 規劃');
    const f = form(details, async () => {
      const selectedMode = mode.value;
      status(selectedMode === 'demo' ? '正在執行演示（模擬資料）…' : '正在查詢真實資料…');
      const selectedSkills = Array.from(skills.querySelectorAll('input:checked')).map(input => input.value);
      const data = await api('/guardian/plan', 'POST', { message: message.value.trim(), mode: selectedMode, city: city.value,
        ...(selectedSkills.length ? { skills: selectedSkills } : {}) });
      await showResult(data, { mode: selectedMode }); status('規劃完成，請查看結果及提案。');
    });
    const grid = el(f, 'div', null, 'tg-grid');
    const city = select(grid, '城市', [['澳門', '澳門'], ['香港', '香港']], '澳門');
    const mode = select(grid, '模式', [['real', '真實查詢（預設）'], ['demo', '演示：模擬資料']], 'real');
    const label = el(f, 'label', '規劃需求'), message = el(label, 'textarea'); message.required = true; message.maxLength = 4000; message.rows = 3;
    button(f, '填入此城市的演示情境', () => {
      mode.value = 'demo'; message.value = city.value === '香港' ? '演示：香港尖沙咀出發，遇到降雨，請為同行長者安排少步行、室內文化與休息行程。' : '演示：澳門大三巴出發，遇到降雨，請為素食同行者安排少步行、室內文化與休息行程。';
      status('已選擇演示模式；提交後使用模擬資料。');
    });
    const skills = el(f, 'fieldset'); el(skills, 'legend', '可選技能（未選由服務自動決定）');
    const loadSkills = async () => {
      const data = await api('/guardian/skills');
      skills.querySelectorAll('label, p').forEach(node => node.remove());
      const values = Array.isArray(data) ? data : list(data.skills);
      if (!values.length) el(skills, 'p', '服務未提供可選技能。');
      values.forEach(skill => {
        const id = typeof skill === 'string' ? skill : skill.id || skill.skill_id;
        if (!id) return;
        const input = field(skills, typeof skill === 'string' ? skill : skill.name || id, 'checkbox'); input.value = id;
      });
    };
    button(skills, '重新載入技能', loadSkills);
    loadSkills().catch(() => el(skills, 'p', '技能載入失敗，可重試或直接規劃。'));
    submit(f, '開始規劃');
  }
  function mount() {
    if (root) return;
    root = el(null, 'section', null, 'tg-panel'); root.id = 'travel-guardian'; root.setAttribute('aria-label', '團隊旅行守護');
    (document.getElementById('schedule') || document.body).prepend(root);
    el(root, 'h2', '團隊旅行守護');
    notice = el(root, 'p', '正在載入團隊…', 'tg-notice'); notice.setAttribute('role', 'status'); notice.setAttribute('aria-live', 'polite');
    const actions = el(root, 'div', null, 'tg-actions');
    button(actions, '更新團隊與提案', async () => { await refresh(); status(dirty ? '資料已更新；尚未儲存的偏好保留，儲存時會檢查版本。' : '資料已更新。'); });
    button(actions, '檢查真實事件', () => checkEvents());
    if (joinToken) {
      // Remove the invitation secret from the address bar before any API calls.
      const clean = new URL(location.href); clean.searchParams.delete('join'); history.replaceState(history.state, '', clean.pathname + clean.search + clean.hash);
      const join = form(root, async () => {
        await api('/team/join', 'POST', { token: joinToken, name: name.value.trim() });
        joinToken = null; dirty = false; join.remove(); await refresh(); status('已加入團隊。');
      });
      el(join, 'h3', '加入受邀團隊');
      const name = field(join, '你的名字'); name.required = true; name.maxLength = 80;
      submit(join, '加入團隊');
      button(join, '取消加入', () => { joinToken = null; join.remove(); });
    }
    const create = el(root, 'details'); el(create, 'summary', '建立團隊（承接既有行程）');
    const f = form(create, async () => {
      await api('/team', 'POST', { name: name.value.trim(), member_name: member.value.trim() });
      dirty = false; await refresh(); status('團隊已建立。'); create.open = false;
    });
    const name = field(f, '團隊名稱'), member = field(f, '你的名字');
    [name, member].forEach(input => { input.required = true; input.maxLength = 80; }); submit(f, '建立團隊');
    teamBox = el(root, 'section'); planner(root); resultBox = el(root, 'section');
    proposalBox = el(root, 'section'); eventBox = el(root, 'section');
    run(async () => { await refresh(); status('團隊已載入。'); });
    const schedule = () => {
      clearTimeout(timer);
      if (!document.hidden) timer = setTimeout(async () => {
        if (!document.hidden && !pending) await run(() => checkEvents());
        schedule();
      }, interval);
    };
    document.addEventListener('visibilitychange', schedule); schedule();
  }
  window.TravelGuardian = { refresh, showResult, checkEvents };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', mount, { once: true }); else mount();
})();
