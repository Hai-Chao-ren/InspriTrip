const destinations = {
  qinglan: { name: '青岚岛', city: '舟山', score: 94, reason: '把时间留给海风和慢下来的自己。', facts: ['人均约 ¥820', '建议 2 天', '需换乘轮渡'], evidence: '合成证据：海边安静，沿岸没有连续商业街，适合一个人放空。', caveat: '透明限制：大风天气可能影响轮渡班次。' },
  songxi: { name: '松溪古镇', city: '湖州', score: 91, reason: '在水巷和旧屋之间慢慢走一整天。', facts: ['人均约 ¥620', '建议 1–2 天', '高铁约 110 分钟'], evidence: '合成证据：老屋、水巷、独立咖啡和本地小吃可以步行串联。', caveat: '透明限制：节假日下午主街人流集中。' },
  zhuyin: { name: '竹隐山谷', city: '安吉', score: 88, reason: '住进竹林，把注意力交还给山风。', facts: ['人均约 ¥760', '建议 2 天', '适合自驾'], evidence: '合成证据：竹林步道安静，轻徒步和露营可以组合。', caveat: '透明限制：连续降雨时部分步道关闭。' },
  nanan: { name: '南岸艺仓', city: '上海', score: 86, reason: '用一场展览和一杯咖啡重新充电。', facts: ['人均约 ¥460', '建议 1 天', '地铁可达'], evidence: '合成证据：展览、咖啡和沿江步道适合一日灵感补给。', caveat: '透明限制：热门展览需要预约。' },
  qihu: { name: '栖湖营地', city: '苏州', score: 85, reason: '和朋友在湖边把周末过得有点野。', facts: ['人均约 ¥820', '建议 1–2 天', '适合自驾'], evidence: '合成证据：湖边露营、骑行和看日落可以组合。', caveat: '透明限制：周末营位需要提前预约。' },
  yunquan: { name: '云泉小城', city: '南京', score: 84, reason: '泡进温泉里，认真休息一个晚上。', facts: ['人均约 ¥1180', '建议 2 天', '高铁约 145 分钟'], evidence: '合成证据：住宿与温泉在同一区域，晚上不需要继续赶路。', caveat: '透明限制：单人入住成本偏高。' }
};

const scenarios = {
  sea: {
    tag: '场景 A', query: '想安静看海，不要太商业化', origin: '上海', budget: 1000, days: 2,
    intent: ['松弛治愈', '自然小众', '看海'], flow: [6, 5, 4, 3], results: ['qinglan', 'zhuyin', 'songxi'], clarify: false
  },
  town: {
    tag: '场景 B', query: '周末想逛古镇、喝咖啡，预算别太高', origin: '', budget: 800, days: 2,
    intent: ['怀旧松弛', '古朴本地', '古镇 / 咖啡'], flow: [6, 5, 4, 3], results: ['songxi', 'nanan', 'yunquan'], clarify: true
  },
  tight: {
    tag: '场景 C', query: '两天看海、泡温泉、露营，人均 200 元以内', origin: '上海', budget: 200, days: 2,
    intent: ['自由治愈', '自然', '看海 / 温泉 / 露营'], flow: [6, 0, 0, 0], results: [], clarify: false
  }
};

const state = { scenario: 'sea', view: 'discover', forced: 'normal', clarifiedOrigin: '' };
const screen = document.getElementById('screen');
const viewTitle = document.getElementById('view-title');
const viewKicker = document.getElementById('view-kicker');
const scenarioTag = document.getElementById('scenario-tag');

const titles = {
  discover: ['从一句人话开始', '今天想要什么感觉？'],
  clarify: ['只追问阻断信息', '还差一个关键条件'],
  trace: ['确定性工作流', '推荐是怎样形成的？'],
  results: ['理由与事实分离', '可信结果，不只是一段文案'],
  feedback: ['验证真实价值', '把反馈送回产品闭环']
};

function escapeHtml(value) {
  return String(value).replace(/[&<>'"]/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[char]));
}

function setActive(selector, attribute, value) {
  document.querySelectorAll(selector).forEach(button => button.classList.toggle('active', button.dataset[attribute] === value));
}

function renderDiscover(scenario) {
  return `<div class="prompt-card">
    <label for="demo-query">你的旅行灵感</label>
    <textarea id="demo-query">${escapeHtml(scenario.query)}</textarea>
    <div class="fields">
      <div class="field"><label for="demo-origin">从哪里出发</label><select id="demo-origin"><option value="">请选择</option>${['上海','杭州','苏州','南京'].map(city => `<option ${city === (state.clarifiedOrigin || scenario.origin) ? 'selected' : ''}>${city}</option>`).join('')}</select></div>
      <div class="field"><label for="demo-budget">人均预算</label><input id="demo-budget" type="number" value="${scenario.budget}"></div>
      <div class="field"><label for="demo-days">天数</label><input id="demo-days" type="number" value="${scenario.days}"></div>
    </div>
    <div class="action-row"><button class="primary-button" type="button" data-action="run">生成可信灵感 →</button><span class="action-note">公开版仅运行三个合成引导场景</span></div>
    <p id="custom-warning" class="custom-warning" hidden>自由输入需要本地完整模式；本页不会把固定结果伪装成 AI 自由生成。</p>
  </div>
  <div class="intent-preview">
    <div><small>情绪目标</small><b>${scenario.intent[0]}</b></div>
    <div><small>氛围偏好</small><b>${scenario.intent[1]}</b></div>
    <div><small>活动线索</small><b>${scenario.intent[2]}</b></div>
  </div>`;
}

function renderClarify(scenario) {
  const origin = state.clarifiedOrigin || scenario.origin;
  if (origin) return `<div class="clarify-card"><div class="question-mark">✓</div><h3>出发城市已确认：${origin}</h3><p>预算和天数属于可降级信息，不再增加第二轮追问。</p><button class="primary-button" type="button" data-action="process">继续生成推荐 →</button></div>`;
  return `<div class="clarify-card"><div class="question-mark">?</div><h3>你从哪座城市出发？</h3><p>出发地会决定两天内是否真的可达，这是当前唯一阻断推荐的槽位。</p><div class="city-row">${['上海','杭州','苏州','南京'].map(city => `<button type="button" data-city="${city}">${city}</button>`).join('')}</div></div>`;
}

function renderTrace(scenario, active = 4) {
  const stages = [
    ['01','意图理解','感觉与限制'], ['02','候选召回','语义与关键词'], ['03','硬约束','预算 / 天数 / 交通'], ['04','证据门控','支持与限制']
  ];
  return `<div class="trace-panel"><div class="trace-head"><span>“${escapeHtml(scenario.query)}”</span><b>${active >= 4 ? 'COMPLETED' : 'PROCESSING'}</b></div>
    <div class="route-trace">${stages.map((item,index) => `<div class="route-stage ${index < active - 1 ? 'done' : ''} ${index === active - 1 ? 'active' : ''}"><i>${item[0]}</i><b>${item[1]}</b><small>${item[2]}</small></div>`).join('')}</div>
    <div class="trace-ledger"><div><strong>${scenario.flow[0]}</strong><small>合成候选</small></div><div><strong>${scenario.flow[1]}</strong><small>满足语义</small></div><div><strong>${scenario.flow[2]}</strong><small>通过约束</small></div><div><strong>${scenario.flow[3]}</strong><small>证据准入</small></div></div>
  </div>`;
}

function renderResultCard(key, index) {
  const item = destinations[key];
  return `<article class="result-card"><div class="result-head"><span class="rank">${index + 1}</span><div class="result-title"><h3>${item.name} <small>· ${item.city}</small></h3><p>${index === 0 ? '首选匹配' : '多样性补位'}</p></div><span class="match-score">${item.score}%</span></div>
    <p class="reason">${item.reason}</p><div class="fact-tags">${item.facts.map(fact => `<span>${fact}</span>`).join('')}</div>
    <button class="evidence-toggle" type="button" data-evidence="${key}" aria-expanded="false"><span>查看推荐依据</span><span>＋</span></button><div id="evidence-${key}" hidden><div class="evidence-detail">${item.evidence}</div><div class="evidence-detail warn">${item.caveat}</div></div>
    <div class="feedback-row"><button type="button" data-feedback="want">想去</button><button type="button" data-feedback="not">不感兴趣</button><button type="button" data-feedback="issue">信息有误</button></div></article>`;
}

function renderResults(scenario) {
  if (!scenario.results.length) return renderEmpty();
  return `<div class="results-list">${scenario.results.map(renderResultCard).join('')}</div>`;
}

function renderFeedback(scenario) {
  const first = destinations[scenario.results[0] || 'qinglan'];
  return `<div class="panel-card" style="padding:16px"><span class="section-label">反馈回流示例</span><h3 style="margin:0 0 8px;font:700 16px var(--serif)">${first.name}</h3><p style="color:var(--muted-fg);font-size:10px;line-height:1.7">“想去”进入北极星指标；“不感兴趣”用于排序诊断；“信息有误”进入数据治理队列。公开页面不会写入真实生产数据。</p><div class="feedback-row"><button type="button" data-feedback="want">想去</button><button type="button" data-feedback="not">不感兴趣</button><button type="button" data-feedback="issue">信息有误</button></div></div>`;
}

function renderLoading(scenario) {
  return `<div class="status-card"><div><div class="skeleton-stack"><div class="skeleton"></div><div class="skeleton"></div><div class="skeleton"></div></div><h3>正在核对候选与证据</h3><p>意图理解、候选召回、硬约束和证据门控按固定顺序执行。</p></div></div>`;
}
function renderEmpty() {
  return `<div class="status-card empty"><div><div class="status-icon">∅</div><h3>没有安全满足全部条件的结果</h3><p>当前预算与活动组合超出合成样例覆盖。建议先放宽预算，或减少一个必须活动。</p><button class="primary-button" style="margin-top:14px" type="button" data-action="reset">调整条件</button></div></div>`;
}
function renderError() {
  return `<div class="status-card error"><div><div class="status-icon">!</div><h3>推荐服务暂时不可用</h3><p>系统不会用未经核实的文案填补错误。你可以重试，或查看项目中的降级与恢复设计。</p><button class="primary-button" style="margin-top:14px" type="button" data-action="reset">返回输入</button></div></div>`;
}

function render() {
  const scenario = scenarios[state.scenario];
  const [kicker, title] = titles[state.view];
  viewKicker.textContent = kicker;
  viewTitle.textContent = title;
  scenarioTag.textContent = scenario.tag;
  setActive('.product-nav button[data-view]', 'view', state.view);
  setActive('#scenario-switcher button', 'scenario', state.scenario);
  setActive('#state-switcher button', 'state', state.forced);
  if (state.forced === 'loading') screen.innerHTML = renderLoading(scenario);
  else if (state.forced === 'empty') screen.innerHTML = renderEmpty();
  else if (state.forced === 'error') screen.innerHTML = renderError();
  else if (state.view === 'discover') screen.innerHTML = renderDiscover(scenario);
  else if (state.view === 'clarify') screen.innerHTML = renderClarify(scenario);
  else if (state.view === 'trace') screen.innerHTML = renderTrace(scenario);
  else if (state.view === 'results') screen.innerHTML = renderResults(scenario);
  else screen.innerHTML = renderFeedback(scenario);
}

document.getElementById('scenario-switcher').addEventListener('click', event => {
  const button = event.target.closest('[data-scenario]'); if (!button) return;
  state.scenario = button.dataset.scenario; state.view = 'discover'; state.forced = 'normal'; state.clarifiedOrigin = ''; render();
});
document.getElementById('state-switcher').addEventListener('click', event => {
  const button = event.target.closest('[data-state]'); if (!button) return;
  state.forced = button.dataset.state; render();
});
document.querySelector('.product-nav').addEventListener('click', event => {
  const button = event.target.closest('[data-view]'); if (!button) return;
  state.view = button.dataset.view; state.forced = 'normal'; render();
});
screen.addEventListener('click', event => {
  const action = event.target.closest('[data-action]');
  if (action) {
    if (action.dataset.action === 'run') {
      const scenario = scenarios[state.scenario];
      const query = document.getElementById('demo-query')?.value.trim();
      const origin = document.getElementById('demo-origin')?.value;
      if (query !== scenario.query) { document.getElementById('custom-warning').hidden = false; return; }
      if (!origin) { state.view = 'clarify'; render(); return; }
      state.clarifiedOrigin = origin; state.view = scenario.results.length ? 'trace' : 'results'; render();
    } else if (action.dataset.action === 'process') { state.view = 'trace'; render(); }
    else { state.view = 'discover'; state.forced = 'normal'; render(); }
  }
  const city = event.target.closest('[data-city]');
  if (city) { state.clarifiedOrigin = city.dataset.city; render(); }
  const toggle = event.target.closest('[data-evidence]');
  if (toggle) { const detail = document.getElementById(`evidence-${toggle.dataset.evidence}`); detail.hidden = !detail.hidden; toggle.setAttribute('aria-expanded', String(!detail.hidden)); toggle.lastElementChild.textContent = detail.hidden ? '＋' : '－'; }
  const feedback = event.target.closest('[data-feedback]');
  if (feedback) { feedback.parentElement.querySelectorAll('button').forEach(btn => btn.classList.remove('selected')); feedback.classList.add('selected'); feedback.textContent = feedback.dataset.feedback === 'want' ? '已标记想去 ✓' : feedback.dataset.feedback === 'not' ? '已记录不感兴趣 ✓' : '已进入纠错队列 ✓'; }
});

document.querySelectorAll('[data-jump]').forEach(button => button.addEventListener('click', () => {
  state.view = button.dataset.jump; state.forced = 'normal'; render();
  document.querySelector('.app-panel').scrollIntoView({behavior:'smooth', block:'start'});
}));
document.getElementById('demo-help').addEventListener('click', event => {
  const panel = document.getElementById('demo-help-panel'); panel.hidden = !panel.hidden; event.currentTarget.setAttribute('aria-expanded', String(!panel.hidden));
});

const sections = [...document.querySelectorAll('.case-section[id], .case-section h3[id]')];
const tocLinks = [...document.querySelectorAll('.case-toc a')];
if ('IntersectionObserver' in window) {
  const observer = new IntersectionObserver(entries => {
    entries.filter(entry => entry.isIntersecting).forEach(entry => tocLinks.forEach(link => link.classList.toggle('active', link.getAttribute('href') === `#${entry.target.id}`)));
  }, {rootMargin:'-25% 0px -65% 0px'});
  sections.forEach(section => observer.observe(section));
}

render();
