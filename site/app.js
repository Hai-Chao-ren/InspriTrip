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
    tag: '场景 B', query: '周末想逛古镇、喝咖啡，预算别太高', origin: '', budget: '', days: 2,
    intent: ['怀旧松弛', '古朴本地', '古镇 / 咖啡'], flow: [6, 5, 4, 3], results: ['songxi', 'nanan', 'yunquan'], clarify: true
  },
  tight: {
    tag: '场景 C', query: '两天看海、泡温泉、露营，人均 200 元以内', origin: '上海', budget: 200, days: 2,
    intent: ['自由治愈', '自然', '看海 / 温泉 / 露营'], flow: [6, 0, 0, 0], results: [], clarify: false
  }
};

const CITY_GROUPS = {
  A:['阿坝','阿克苏','阿拉善','安康','安庆','鞍山','安顺','安阳'],
  B:['北京','白城','白山','白银','保定','宝鸡','包头','巴中','蚌埠','北海','本溪','滨州','亳州'],
  C:['重庆','成都','长沙','长春','常州','沧州','昌都','昌吉','潮州','承德','郴州','赤峰','池州','崇左','楚雄','滁州'],
  D:['大连','大庆','大同','丹东','德阳','德州','东莞','东营','达州','大理'],
  E:['鄂尔多斯','鄂州','恩施'],
  F:['福州','佛山','抚顺','抚州','阜阳','防城港'],
  G:['广州','贵阳','桂林','赣州','甘南','广安','广元','贵港','果洛','固原'],
  H:['杭州','哈尔滨','海口','合肥','呼和浩特','惠州','湖州','淮安','淮北','淮南','黄冈','黄山','黄石','衡水','衡阳','河池','河源','菏泽','贺州','汉中','邯郸','鹤壁','鹤岗','黑河','红河','葫芦岛'],
  J:['济南','嘉兴','吉林','江门','金华','晋城','晋中','荆门','荆州','景德镇','九江','酒泉','揭阳','济宁','佳木斯','焦作','锦州'],
  K:['昆明','开封','克拉玛依','喀什'],
  L:['兰州','拉萨','廊坊','丽江','连云港','临沂','洛阳','柳州','六安','娄底','泸州','乐山','聊城','辽阳','辽源','临汾','临夏','临沧','林芝','丽水','龙岩','漯河'],
  M:['绵阳','牡丹江','马鞍山','茂名','梅州','眉山'],
  N:['南京','南昌','南宁','南通','南阳','宁波','内江','宁德'],
  P:['平顶山','莆田','盘锦','攀枝花','萍乡','普洱','濮阳'],
  Q:['青岛','泉州','秦皇岛','齐齐哈尔','衢州','曲靖','黔东南','黔南','黔西南','庆阳','清远','钦州'],
  R:['日照','日喀则'],
  S:['上海','深圳','苏州','沈阳','石家庄','三亚','绍兴','汕头','汕尾','韶关','商洛','商丘','上饶','邵阳','十堰','石嘴山','双鸭山','朔州','四平','绥化','遂宁','宿迁','宿州'],
  T:['天津','太原','泰安','泰州','台州','唐山','天水','铁岭','通化','通辽','铜川','铜陵','铜仁','吐鲁番'],
  W:['武汉','无锡','温州','乌鲁木齐','潍坊','威海','芜湖','梧州','渭南','文山','乌海','武威'],
  X:['西安','厦门','西宁','徐州','许昌','咸阳','湘潭','襄阳','孝感','忻州','新乡','新余','信阳','兴安盟','锡林郭勒','西双版纳'],
  Y:['银川','烟台','扬州','宜昌','宜宾','义乌','延安','盐城','阳江','阳泉','伊春','伊犁','营口','永州','玉林','榆林','玉溪','岳阳','运城'],
  Z:['郑州','珠海','中山','镇江','舟山','湛江','肇庆','张家界','张家口','张掖','漳州','昭通','枣庄','株洲','淄博','自贡','遵义']
};
const CITY_ALPHABET = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ'.split('');
const POPULAR_CITIES = ['北京','上海','广州','深圳','杭州','成都','南京','重庆','武汉','西安','苏州','天津'];
const DEMO_CITY_COORDS = [
  ['上海',31.2304,121.4737], ['杭州',30.2741,120.1551], ['苏州',31.2989,120.5853],
  ['南京',32.0603,118.7969], ['北京',39.9042,116.4074], ['广州',23.1291,113.2644],
  ['深圳',22.5431,114.0579], ['成都',30.5728,104.0668], ['重庆',29.5630,106.5516],
  ['武汉',30.5928,114.3055], ['西安',34.3416,108.9398], ['天津',39.0842,117.2009]
];

const state = {
  scenario: 'sea', view: 'discover', forced: 'normal', origin: '', budget: '', days: '',
  originSource: '', locationStatus: 'idle', detectedOrigin: '', cityPickerOpen: false, pendingFields: []
};
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

function resetScenarioInputs() {
  const scenario = scenarios[state.scenario];
  state.origin = scenario.origin || '';
  state.budget = scenario.budget ?? '';
  state.days = scenario.days ?? '';
  state.originSource = scenario.origin ? 'preset' : '';
  state.locationStatus = 'idle';
  state.detectedOrigin = '';
  state.cityPickerOpen = false;
  state.pendingFields = [];
}

function missingRequiredFields() {
  const missing = [];
  if (!String(state.origin || '').trim()) missing.push('origin');
  if (state.budget === '' || state.budget === null || Number(state.budget) < 0) missing.push('budget');
  if (state.days === '' || state.days === null || Number(state.days) < 1) missing.push('days');
  return missing;
}

function locationCopy() {
  if (state.locationStatus === 'locating') return ['正在获取当前位置', '坐标只在浏览器内用于匹配演示城市', '定位中'];
  if (state.locationStatus === 'located' && state.origin) return [`已定位：${state.origin}`, '本地近似匹配，不上传坐标', '重新定位'];
  if (state.locationStatus === 'denied') return ['定位权限未开启', '可通过搜索或 A–Z 选择城市', '重试'];
  if (state.locationStatus === 'failed') return ['暂时无法获取位置', '可通过搜索或 A–Z 选择城市', '重试'];
  if (state.originSource === 'manual' && state.origin) return ['未使用当前位置', `当前手动选择：${state.origin}`, '定位'];
  return ['使用当前位置', '定位优先，也可以手动选择城市', '定位'];
}

function renderOriginControl(compact = false) {
  const [title, detail, action] = locationCopy();
  const selected = state.origin ? `<span class="origin-selected">${escapeHtml(state.origin)}出发 · ${state.originSource === 'location' ? '已定位' : '已选择'}</span>` : '';
  return `<div class="origin-control ${compact ? 'compact' : ''}">
    <button class="location-choice ${state.locationStatus === 'located' ? 'located' : ''}" type="button" data-action="locate">
      <span class="location-dot" aria-hidden="true">◎</span><span><b>${escapeHtml(title)}</b><small>${escapeHtml(detail)}</small></span><i>${escapeHtml(action)}</i>
    </button>
    <button class="city-choice" type="button" data-action="open-city"><span><b>${state.originSource === 'manual' && state.origin ? escapeHtml(state.origin) : '选择出发城市'}</b><small>搜索 · 热门城市 · A–Z</small></span><i>›</i></button>
    ${selected}
  </div>`;
}

function renderCityPickerDialog() {
  if (!state.cityPickerOpen) return '';
  const sections = Object.entries(CITY_GROUPS).map(([letter, cities]) => `
    <section class="city-group" id="city-section-${letter}" data-city-group="${letter}">
      <h4>${letter}</h4><div class="city-grid">${cities.map(city => `<button type="button" data-city="${city}" data-letter="${letter}">${city}</button>`).join('')}</div>
    </section>`).join('');
  return `<div class="city-picker-backdrop" role="presentation">
    <section class="city-picker-dialog" role="dialog" aria-modal="true" aria-labelledby="city-picker-title">
      <header><div><small>MANUAL ORIGIN</small><h3 id="city-picker-title">选择出发城市</h3></div><button type="button" data-action="close-city" aria-label="关闭城市选择">×</button></header>
      <label class="city-search"><span aria-hidden="true">⌕</span><input id="city-search" type="search" placeholder="输入中文城市名或首字母" autocomplete="off"></label>
      <div class="city-picker-body">
        <div class="city-picker-list" id="city-picker-list">
          ${state.detectedOrigin ? `<section class="city-group current-city"><h4>当前定位</h4><div class="city-grid"><button type="button" data-city="${escapeHtml(state.detectedOrigin)}" data-letter="">${escapeHtml(state.detectedOrigin)} · 当前</button></div></section>` : ''}
          <section class="city-group" data-city-group="HOT"><h4>热门城市</h4><div class="city-grid">${POPULAR_CITIES.map(city => `<button type="button" data-city="${city}" data-letter="HOT">${city}</button>`).join('')}</div></section>
          ${sections}
          <p class="city-empty" id="city-empty" hidden>没有找到这个城市，请输入中文城市名或首字母。</p>
        </div>
        <nav class="city-index" aria-label="城市首字母索引">${CITY_ALPHABET.map(letter => `<button type="button" data-city-letter="${letter}" ${CITY_GROUPS[letter] ? '' : 'disabled'}>${letter}</button>`).join('')}</nav>
      </div>
      <footer>公开 Demo 不上传或保存定位坐标；本地完整模式通过高德逆地理编码识别城市。</footer>
    </section>
  </div>`;
}

function nearestDemoCity(latitude, longitude) {
  let nearest = DEMO_CITY_COORDS[0];
  let best = Number.POSITIVE_INFINITY;
  DEMO_CITY_COORDS.forEach(item => {
    const distance = ((item[1] - latitude) ** 2) + ((item[2] - longitude) ** 2);
    if (distance < best) { best = distance; nearest = item; }
  });
  return nearest[0];
}

function requestDemoLocation() {
  state.locationStatus = 'locating';
  render();
  if (!navigator.geolocation) {
    state.locationStatus = 'failed';
    state.cityPickerOpen = true;
    render();
    setTimeout(() => document.getElementById('city-search')?.focus(), 0);
    return;
  }
  navigator.geolocation.getCurrentPosition(position => {
    const city = nearestDemoCity(position.coords.latitude, position.coords.longitude);
    state.origin = city;
    state.detectedOrigin = city;
    state.originSource = 'location';
    state.locationStatus = 'located';
    state.cityPickerOpen = false;
    render();
  }, error => {
    state.locationStatus = error && error.code === 1 ? 'denied' : 'failed';
    state.cityPickerOpen = true;
    render();
    setTimeout(() => document.getElementById('city-search')?.focus(), 0);
  }, {enableHighAccuracy:false, timeout:8000, maximumAge:600000});
}

function setActive(selector, attribute, value) {
  document.querySelectorAll(selector).forEach(button => button.classList.toggle('active', button.dataset[attribute] === value));
}

function renderDiscover(scenario) {
  return `<div class="prompt-card">
    <label for="demo-query">你的旅行灵感</label>
    <textarea id="demo-query">${escapeHtml(scenario.query)}</textarea>
    <div class="fields">
      <div class="field origin-field"><label>从哪里出发 <em>必填</em></label>${renderOriginControl()}</div>
      <div class="field"><label for="demo-budget">人均预算 <em>必填</em></label><input id="demo-budget" type="number" min="0" value="${state.budget}" placeholder="例如 1000" aria-required="true"></div>
      <div class="field"><label for="demo-days">天数 <em>必填</em></label><input id="demo-days" type="number" min="1" value="${state.days}" placeholder="例如 2" aria-required="true"></div>
    </div>
    <div class="action-row"><button class="primary-button" type="button" data-action="run">生成可信灵感 →</button><span class="action-note">三项硬条件可从自然语言或控件获得；缺失时一次问全</span></div>
    <p id="custom-warning" class="custom-warning" hidden>自由输入需要本地完整模式；本页不会把固定结果伪装成 AI 自由生成。</p>
  </div>
  <div class="intent-preview">
    <div><small>情绪目标</small><b>${scenario.intent[0]}</b></div>
    <div><small>氛围偏好</small><b>${scenario.intent[1]}</b></div>
    <div><small>活动线索</small><b>${scenario.intent[2]}</b></div>
  </div>${renderCityPickerDialog()}`;
}

function renderClarify(scenario) {
  const fields = state.pendingFields.length ? state.pendingFields : missingRequiredFields();
  if (!fields.length) return `<div class="clarify-card"><div class="question-mark">✓</div><h3>三项硬条件已经确认</h3><p>${escapeHtml(state.origin)}出发 · 人均 ${state.budget} 元 · ${state.days} 天。系统现在可以进入事实过滤与排序。</p><button class="primary-button" type="button" data-action="process">继续生成推荐 →</button></div>${renderCityPickerDialog()}`;
  const stillMissing = missingRequiredFields();
  const fieldNames = fields.map(field => ({origin:'出发城市', budget:'人均预算', days:'出行天数'}[field]));
  const budgetOptions = [500, 1000, 2000, 3000];
  const dayOptions = [1, 2, 3, 4];
  return `<div class="clarify-card clarify-required"><div class="question-mark">?</div><h3>还差 ${fieldNames.length} 个硬条件，一次确认完成</h3><p>需要补齐${fieldNames.join('、')}，否则系统无法执行预算、天数与可达性过滤。</p>
    <div class="clarify-required-fields">
      ${fields.includes('origin') ? `<div class="clarify-field"><b>出发城市 <em>必填</em></b>${renderOriginControl(true)}</div>` : ''}
      ${fields.includes('budget') ? `<div class="clarify-field"><b>人均预算 <em>必填</em></b><div class="required-chips">${budgetOptions.map(value => `<button type="button" data-clarify-budget="${value}" class="${Number(state.budget) === value ? 'selected' : ''}">${value === 3000 ? '3000+' : value} 元</button>`).join('')}</div></div>` : ''}
      ${fields.includes('days') ? `<div class="clarify-field"><b>出行天数 <em>必填</em></b><div class="required-chips">${dayOptions.map(value => `<button type="button" data-clarify-days="${value}" class="${Number(state.days) === value ? 'selected' : ''}">${value === 4 ? '4 天+' : `${value} 天`}</button>`).join('')}</div></div>` : ''}
    </div>
    <button class="primary-button clarify-submit" type="button" data-action="process" ${stillMissing.length ? 'disabled' : ''}>确认条件并开始推荐 →</button>
  </div>${renderCityPickerDialog()}`;
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
  state.scenario = button.dataset.scenario; state.view = 'discover'; state.forced = 'normal'; resetScenarioInputs(); render();
});
document.getElementById('state-switcher').addEventListener('click', event => {
  const button = event.target.closest('[data-state]'); if (!button) return;
  state.forced = button.dataset.state; render();
});
document.querySelector('.product-nav').addEventListener('click', event => {
  const button = event.target.closest('[data-view]'); if (!button) return;
  state.view = button.dataset.view; state.forced = 'normal';
  if (state.view === 'clarify') state.pendingFields = missingRequiredFields();
  render();
});
screen.addEventListener('click', event => {
  const action = event.target.closest('[data-action]');
  if (action) {
    if (action.dataset.action === 'run') {
      const scenario = scenarios[state.scenario];
      const query = document.getElementById('demo-query')?.value.trim();
      const budgetValue = document.getElementById('demo-budget')?.value ?? '';
      const daysValue = document.getElementById('demo-days')?.value ?? '';
      state.budget = budgetValue === '' ? '' : Number(budgetValue);
      state.days = daysValue === '' ? '' : Number(daysValue);
      if (query !== scenario.query) { document.getElementById('custom-warning').hidden = false; return; }
      const missing = missingRequiredFields();
      if (missing.length) { state.pendingFields = missing; state.view = 'clarify'; render(); return; }
      state.pendingFields = []; state.view = scenario.results.length ? 'trace' : 'results'; render();
    } else if (action.dataset.action === 'process') {
      if (missingRequiredFields().length) return;
      state.pendingFields = []; state.view = 'trace'; render();
    } else if (action.dataset.action === 'locate') {
      requestDemoLocation(); return;
    } else if (action.dataset.action === 'open-city') {
      state.cityPickerOpen = true; render(); setTimeout(() => document.getElementById('city-search')?.focus(), 0); return;
    } else if (action.dataset.action === 'close-city') {
      state.cityPickerOpen = false; render(); return;
    } else if (action.dataset.action === 'reset') {
      state.view = 'discover'; state.forced = 'normal'; render();
    }
  }
  const city = event.target.closest('[data-city]');
  if (city) {
    state.origin = city.dataset.city;
    state.originSource = 'manual';
    state.locationStatus = 'manual';
    state.cityPickerOpen = false;
    render();
  }
  const budget = event.target.closest('[data-clarify-budget]');
  if (budget) { state.budget = Number(budget.dataset.clarifyBudget); render(); }
  const days = event.target.closest('[data-clarify-days]');
  if (days) { state.days = Number(days.dataset.clarifyDays); render(); }
  const cityLetter = event.target.closest('[data-city-letter]');
  if (cityLetter) document.getElementById(`city-section-${cityLetter.dataset.cityLetter}`)?.scrollIntoView({behavior:'smooth', block:'start'});
  const toggle = event.target.closest('[data-evidence]');
  if (toggle) { const detail = document.getElementById(`evidence-${toggle.dataset.evidence}`); detail.hidden = !detail.hidden; toggle.setAttribute('aria-expanded', String(!detail.hidden)); toggle.lastElementChild.textContent = detail.hidden ? '＋' : '－'; }
  const feedback = event.target.closest('[data-feedback]');
  if (feedback) { feedback.parentElement.querySelectorAll('button').forEach(btn => btn.classList.remove('selected')); feedback.classList.add('selected'); feedback.textContent = feedback.dataset.feedback === 'want' ? '已标记想去 ✓' : feedback.dataset.feedback === 'not' ? '已记录不感兴趣 ✓' : '已进入纠错队列 ✓'; }
});

screen.addEventListener('input', event => {
  if (event.target.id !== 'city-search') return;
  const query = event.target.value.trim();
  const upper = query.toUpperCase();
  let matches = 0;
  screen.querySelectorAll('.city-group').forEach(group => {
    let groupMatches = 0;
    group.querySelectorAll('[data-city]').forEach(button => {
      const visible = !query || button.dataset.city.includes(query) || (upper.length === 1 && button.dataset.letter === upper);
      button.hidden = !visible;
      if (visible) { groupMatches += 1; matches += 1; }
    });
    group.hidden = groupMatches === 0;
  });
  const empty = document.getElementById('city-empty');
  if (empty) empty.hidden = matches > 0;
});

document.querySelectorAll('[data-jump]').forEach(button => button.addEventListener('click', () => {
  state.view = button.dataset.jump; state.forced = 'normal'; render();
  document.querySelector('.app-panel').scrollIntoView({behavior:'smooth', block:'start'});
}));
document.getElementById('demo-help').addEventListener('click', event => {
  const panel = document.getElementById('demo-help-panel'); panel.hidden = !panel.hidden; event.currentTarget.setAttribute('aria-expanded', String(!panel.hidden));
});
document.addEventListener('keydown', event => {
  if (event.key === 'Escape' && state.cityPickerOpen) {
    state.cityPickerOpen = false;
    render();
  }
});

const sections = [...document.querySelectorAll('.case-section[id], .case-section h3[id]')];
const tocLinks = [...document.querySelectorAll('.case-toc a')];
if ('IntersectionObserver' in window) {
  const observer = new IntersectionObserver(entries => {
    entries.filter(entry => entry.isIntersecting).forEach(entry => tocLinks.forEach(link => link.classList.toggle('active', link.getAttribute('href') === `#${entry.target.id}`)));
  }, {rootMargin:'-25% 0px -65% 0px'});
  sections.forEach(section => observer.observe(section));
}

resetScenarioInputs();
render();
