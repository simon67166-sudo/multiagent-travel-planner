/* Independent team UI. All server content is rendered as text, never HTML. */
(() => {
  'use strict';
  if (window.TravelGuardian) return;
  const interval = 10 * 60 * 1000;
  let team = null, root, notice, teamBox, proposalBox, eventBox, resultBox;
  let syncTimer, syncing = false, inviteUrl = "", demoActions;
  let timer, checking = false, refreshId = 0, pending = false, dirty = false;
  let joinToken = new URL(location.href).searchParams.get('join');
  const list = value => Array.isArray(value) ? value : [];
  const skillNames = {food_risk:'飲食風險', group_consensus:'團隊共識', queue:'排隊調整', weather:'天氣應變', toilet:'洗手間', souvenir:'手信', citywalk:'城市漫步', crowd:'人潮避讓', safety:'安全提醒', social:'社交話術', hidden_menu:'隱藏菜單', diy:'手作體驗', lazy:'輕鬆行程', museum:'博物館', photo:'攝影'};
  const labels = {price:'價格', amount:'金額', currency:'貨幣', name:'名稱', title:'標題', description:'說明', decision:'建議', ready_to_show:'出示給店員', text:'內容', steps:'步驟', rank:'推薦次序', queue_minutes:'等候分鐘', crowd_level:'人潮程度', opening_hours:'營業時間', accessibility:'無障礙', fee:'費用', floor:'樓層', tissue:'紙巾', recipient:'收禮人', tax:'稅務', story:'故事', angle:'拍攝角度', time:'時間', materials:'材料', availability:'名額', alternatives:'其他選擇', choice:'選擇', note:'提醒', ordered_exhibits:'參觀順序', exhibits:'展品', products:'商品', viewpoints:'拍攝位置', stories:'沿途故事', known_walking_m:'已知步行米數', supported_diet:'適用飲食', cross_contact:'交叉接觸', adverse:'不利天氣', indoor_alternatives:'室內選擇', weather:'天氣', average:'平均滿意度', minimum:'最低滿意度', fairness:'綜合評分', duration_min:'分鐘', distance_m:'米', arrival_time:'抵達', end_time:'結束', visit_note:'遊覽提醒'};
  const words = {'unknown':'待確認', 'ok':'有可用候選', 'error':'資料暫不可用', 'pending':'待團主決策', 'accepted':'已接受', 'rejected':'已拒絕', 'diet suitability':'飲食是否適合', 'price and currency':'價格與幣別', 'walking distance':'步行距離', 'accessibility':'無障礙設施', 'route unavailable':'尚無可用路線', 'existing canonical itinerary unavailable':'請先建立並確認行程', 'member preferences not supplied; neutral group score':'成員尚未填寫偏好，評分僅供參考', 'verify kitchen cross-contact before ordering':'點餐前請向廚房確認交叉接觸風險', 'sheltered visits first':'優先安排有遮蔽或室內景點', 'retain current schedule':'保留目前行程', 'prefer lower observed load; snapshot not a live guarantee':'優先選擇紀錄中較少人潮的候選；到場前仍需確認', 'fee 未提供':'費用未提供', 'floor 未提供':'樓層未提供', 'tissue 未提供':'是否提供紙巾待確認'};
  const text = value => typeof value === 'string' ? (words[value] || value) : value == null ? '待確認' : typeof value === 'object' ? '' : typeof value === 'boolean' ? (value ? '是' : '否') : String(value);
  const conflictText = value => {
    if (typeof value === 'string') return value;
    if (!value || typeof value !== 'object') return text(value);
    const scope = value.poi_id || skillNames[value.skill] || value.skill;
    const reason = text(value.reason);
    return [scope, reason].filter(Boolean).join('：') || '未提供細節';
  };
  function structured(parent, value, depth = 0) {
    if (depth > 5 || value == null) return;
    if (Array.isArray(value)) { value.slice(0, 40).forEach(item => structured(parent, item, depth + 1)); return; }
    if (typeof value !== 'object') { el(parent, 'p', text(value)); return; }
    if ('amount' in value && 'currency' in value) { el(parent, 'p', `${text(value.currency)} ${text(value.amount)}`); return; }
    Object.entries(value).forEach(([key, item]) => {
      if (!labels[key] || item == null) return;
      if (typeof item === 'object') { const box = el(parent, 'div', null, 'tg-detail'); el(box, 'strong', labels[key]); structured(box, item, depth + 1); }
      else el(parent, 'p', `${labels[key]}：${text(item)}`);
    });
  }
  function errorMessage(code, data = {}) {
    const raw = typeof data.error === 'string' ? data.error : '';
    if (code === 409) return /規劃期間/.test(raw) ? '規劃期間團隊已更新，請重新規劃。未儲存偏好已保留。' : '團隊或提案已更新，請核對最新資料後重試。未儲存偏好已保留。';
    if (code === 400) {
      if (/demo|演示|scenario/i.test(raw)) return '演示情境不適用目前行程，請先建立獨立演示團隊並接受演示提案。';
      if (/硬限制|budget|walking|diet/i.test(raw)) return '行程或偏好不符合飲食、預算或步行限制，請核對後重新規劃。';
      if (/名字|名稱|name/i.test(raw)) return '請填寫有效的團隊或成員名稱。';
      return '輸入資料不完整或格式不符，請核對城市、日期、需求與偏好後重試。';
    }
    return code === 403 ? '你沒有此操作權限，或邀請已失效，請聯絡團主。' : code === 401 ? '會話已失效，請重新載入頁面。' : `服務暫時無法完成請求（${code}），請稍後重試。`;
  }
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
        const error = new Error(errorMessage(response.status, data));
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
    const box = el(parent, 'details', null, 'tg-sources'); el(box, 'summary', `查看資料來源（${items.length} 筆）`);
    items.forEach(source => {
      const kind = source?.demo || source?.data_kind === 'demo' ? '演示／虛構資料' : source?.data_kind === 'curated' ? '整理資料' : source?.data_kind === 'real' ? '真實來源' : '來源類型未確認';
      el(box, 'p', `${typeof source === 'object' ? source?.title || source?.source || source?.name || '參考資料' : '參考資料'} · ${kind} · ${source?.fetched_at || '更新時間未提供'}`);
      if (!(typeof source === 'string' ? source : source?.url || source?.source_url)) el(box, 'small', '來源未提供公開連結；內容僅供閱讀，請另行向場館確認。');
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
    const pref = me.preferences || {};
    const details = el(parent, 'details', null, 'tg-preferences'); el(details, 'summary', '我的偏好（只修改自己）');
    const f = form(details, async () => {
      const preferences = { diet: diet.value.split(/[,，]/).map(v => v.trim()).filter(Boolean),
        budget: budget.value === '' ? null : { amount: Number(budget.value), currency: currency.value },
        walking_limit_m: walking.value === '' ? null : Number(walking.value), accessibility: access.checked,
        soft: Object.fromEntries(Object.entries(soft).map(([key, input]) => [key, Number(input.value)])) };
      await api('/team/preferences', 'PATCH', { preferences, expected_revision: team.revision });
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
    const saved = dirty ? teamBox.querySelector('.tg-preferences') : null;
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
    if (saved) teamBox.append(saved); else preferences(teamBox);
    if (team.role === 'owner') {
      const invite = el(teamBox, 'div', null, 'tg-actions');
      const urlField = field(invite, '邀請連結（可選取並手動複製）', 'text', inviteUrl); urlField.readOnly = true;
      urlField.onclick = () => urlField.select();
      button(invite, '產生邀請連結', async () => {
        const data = await api('/team/invite', 'POST', { revoke: false }); inviteUrl = data.invite_url || null;
        copy.disabled = !inviteUrl; urlField.value = inviteUrl || '';
        status(inviteUrl ? '邀請已產生，按「複製邀請」分享。' : '既有邀請不會再次顯示；請撤銷後產生新邀請。');
      });
      const copy = button(invite, '複製邀請', async () => {
        if (!inviteUrl) return;
        try { if (!navigator.clipboard?.writeText) throw new Error(); await navigator.clipboard.writeText(inviteUrl); status('邀請已複製。'); }
        catch (_) { urlField.focus(); urlField.select(); status('連結已選取，請按 Ctrl+C，或長按選擇複製，再分享給同一網路的同行者。'); }
      }); copy.disabled = !inviteUrl;
      button(invite, '撤銷邀請', async () => {
        await api('/team/invite', 'POST', { revoke: true }); inviteUrl = ''; urlField.value = ''; copy.disabled = true; status('舊邀請已撤銷。');
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
      const fields = ['date', 'day', 'time', 'arrival_time', 'end_time', 'name', 'place', 'title', 'description', 'visit_note', 'arrival_transport', 'duration_min', 'distance_m', 'price', 'currency', 'flight_no', 'from_', 'to', 'depart_time', 'arrive_time', 'check_in', 'check_out'];
      const line = fields.filter(key => data[key] != null && typeof data[key] !== 'object').map(key => `${labels[key] || key}：${text(data[key])}`).join(' · ');
      if (line) el(box, 'p', line);
      if (data.price && typeof data.price === 'object') structured(box, {price:data.price});
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
      el(card, 'p', `${text(proposal.status)} · 基於版本 ${proposal.base_version} · ${proposal.created_at || ''}`);
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
    if (team && JSON.stringify(team) === JSON.stringify(next)) return;
    const switched = team && (team.team_id !== next.team_id || team.member_id !== next.member_id);
    if (switched) { dirty = false; inviteUrl = ''; }
    const changed = team && (team.version !== next.version || switched);
    team = next;
    if (changed && typeof window.refreshSchedule === 'function') await window.refreshSchedule();
    renderTeam();
    demoActions.hidden = team.role !== 'owner';
    demoActions.querySelectorAll('button').forEach(b => { b.disabled = !isDemoTrip(); });
    renderProposals(team.proposals); renderEvents(team.events);
    try {
      const data = await api('/proposals');
      if (id === refreshId) renderProposals(data.proposals);
    } catch (error) { status('團隊已載入，但提案更新失敗；可按更新重試。', true); }
  }
  function isDemoTrip() { return team?.itinerary?.mode === 'demo' || team?.itinerary?.nearby_plan?.demo === true || team?.itinerary?.nearby_plan?.data_kind === 'demo'; }
  async function checkEvents(options = {}) {
    if (document.hidden || checking) return;
    checking = true;
    try {
      const demo = options.demo === true;
      if (demo && (team?.role !== 'owner' || !isDemoTrip())) throw new Error('只有團主可播放演示事件；請先在獨立演示團隊接受演示提案。');
      const data = await api('/events/check', 'POST', { demo, ...(demo ? {scenario: options.scenario || 'queue'} : {}) });
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
    data = {...data, skill_results: data.results || data.skill_results};
    const results = Array.isArray(data.skill_results) ? data.skill_results : Object.entries(data.skill_results || {}).map(([name, value]) => ({ name, ...typeof value === 'object' && value !== null ? value : { summary: value } }));
    results.forEach(result => {
      const card = el(resultBox, 'article', null, 'tg-card');
      el(card, 'strong', result.name || skillNames[result.skill] || result.skill || result.skill_id || result.id || '技能');
      if (typeof result.summary === 'string') el(card, 'p', result.summary);
      if (typeof result.status === 'string') el(card, 'small', result.status);
      const details=el(card,'details'); el(details,'summary','候選、決策與待確認事項');
      list(result.candidates).forEach(p => structured(details, p));
      list(result.actions).forEach(action => { const box = el(details, 'section', null, 'tg-card'); const poi = list(result.candidates).find(p => p.id === action.poi_id); if (poi) el(box, 'strong', poi.name); structured(box, action); });
      list(result.unknowns).forEach(item => el(details,'p','待確認：'+text(item)));
      list(result.errors).forEach(item => el(details,'p','資料問題：'+text(item)));
      sources(card, result.evidence || result.sources);
    });
    list(data.conflicts).forEach(item => el(resultBox,'p','衝突：'+conflictText(item)));
    sources(resultBox, data.sources);
    const draft = data.draftproposal || data.draft_proposal || data.proposal;
    if (draft?.itinerary) itinerary(resultBox, draft.itinerary, '提案預覽（尚未套用）');
    if (data.proposal || data.draftproposal || data.draft_proposal) el(resultBox, 'p', '已產生行程提案，需團主接受才會套用。');
    return refresh().catch(() => status('結果已收到，但團隊更新失敗，請按更新重試。', true));
  }
  function planner(parent) {
    const details = el(parent, 'details'); details.open = true; el(details, 'summary', 'Travel Guardian 規劃');
    const f = form(details, async () => {
      const selectedMode = mode.value;
      if (selectedMode === 'demo' && team?.itinerary && !isDemoTrip() && (team.itinerary.nearby_plan || list(team.itinerary.trip_plan?.days).length)) throw new Error('請先建立獨立演示團隊，避免把演示資料混入真實行程。');
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
    demoActions = el(root, 'section', null, 'tg-demo'); demoActions.hidden = true;
    el(demoActions, 'strong', '演示事件播放器（僅團主）');
    el(demoActions, 'p', '先建立獨立演示團隊，選澳門或香港填入情境，規劃並接受演示提案。演示事件只用於已確認的演示行程。');
    [['queue','餐廳排隊增加'],['rain','降雨：尋找室內替代'],['clear','天氣轉晴']].forEach(([scenario, label]) => button(demoActions, label, () => checkEvents({demo:true, scenario})));

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
    const create = el(root, 'details'); el(create, 'summary', '建立團隊');
    const f = form(create, async () => {
      await api('/team', 'POST', { name: name.value.trim(), member_name: member.value.trim(), inherit: inherit.checked });
      dirty = false; await refresh(); status('團隊已建立。'); create.open = false;
    });
    const name = field(f, '團隊名稱'), member = field(f, '你的名字');
    const inheritLabel = el(f, 'label');
    const inherit = el(inheritLabel, 'input'); inherit.type = 'checkbox'; inherit.checked = true;
    el(inheritLabel, 'span', '承接目前行程（取消勾選可建立空白演示團隊）');
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
    const sync = async () => {
      if (document.hidden || pending || syncing || checking) return;
      syncing = true;
      try { await refresh(); } catch (_) { status('團隊同步暫時中斷，將自動重試；未儲存偏好保留。', true); }
      finally { syncing = false; }
    };
    const scheduleSync = () => { clearInterval(syncTimer); if (!document.hidden) { syncTimer = setInterval(sync, 15000); void sync(); } };
    document.addEventListener('visibilitychange', () => { schedule(); scheduleSync(); }); schedule(); scheduleSync();
  }
  window.TravelGuardian = { refresh, showResult, checkEvents, errorMessage };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', mount, { once: true }); else mount();
})();
