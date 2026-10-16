window.P = {
  groups: {
    ai: { title: '可补分析与匹配', short: '补一次 AI', owner: 'AI 处理', icon: 'spark', tone: 'ai', desc: '尚未完成，或没有可核验的匹配结论。', action: '查看补齐清单' },
    waiting: { title: '待题型／标准整理', short: '先整理，再匹配', owner: '整理题型或标准', icon: 'layers', tone: 'waiting', desc: '已有明确的无匹配结论，重复补打无意义。', action: '查看整理原因' },
    manual: { title: '需核对原题', short: '先修正题源', owner: '教师处理', icon: 'file', tone: 'manual', desc: '题目条件不全，先核对再继续分析。', action: '核对原题' }
  },
  icons: {
    spark: '<path d="m12 3 2.4 6.6L21 12l-6.6 2.4L12 21l-2.4-6.6L3 12l6.6-2.4L12 3Z"/>',
    layers: '<path d="m3 7 9-4 9 4-9 4-9-4Zm0 5 9 4 9-4M3 17l9 4 9-4"/>',
    file: '<path d="M14 3H5v18h14V8l-5-5Zm0 0v5h5M8 12h8m-8 4h5"/>',
    check: '<path d="m5 12 4 4 10-10"/>',
    arrow: '<path d="M5 12h14m-6-6 6 6-6 6"/>',
    clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
    info: '<circle cx="12" cy="12" r="9"/><path d="M12 11v6m0-10v1"/>',
    close: '<path d="m6 6 12 12M6 18 18 6"/>'
  },
  icon(name) { return '<svg aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">' + (this.icons[name] || this.icons.info) + '</svg>'; },
  esc(value) { return String(value).replace(/[&<>"']/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch])); },
  state: { variant: new URLSearchParams(location.search).get('variant') || 'A', scene: new URLSearchParams(location.search).get('scenario') || 'demo', group: 'ai', selected: null, checked: new Set(), done: 0, items: [] },
  makeItems(scene) {
    const titles = ['利用边角关系证明三角形全等', '由折叠确定线段长度', '利用垂直平分线求周长', '判断两组三角形是否全等', '构造辅助线证明角相等', '利用等腰三角形求角度', '动点条件下的三角形面积', '从图形变换寻找等量关系', '证明旋转前后的线段关系', '三角形中的最短路径', '多个三角形的面积关系', '开放条件下的几何证明'];
    const count = scene === 'current' ? 12 : 8;
    const items = Array.from({length: count}, (_, i) => ({ id: 'A' + String(i + 1).padStart(2, '0'), title: titles[i], chapter: scene === 'current' ? '八上 · 综合示例' : '第一章 · 三角形的证明', group: 'ai', kind: scene === 'current' ? 'legacy' : i < 3 ? 'analysis' : 'match', source: '合成试卷 ' + String.fromCharCode(65 + i % 3) + ' · 第 ' + (i + 5) + ' 题' }));
    if (scene !== 'current') for (let i = 0; i < 7; i++) items.push({id: 'W0' + (i + 1), title: titles[i + 4], chapter: '第一章 · 三角形的证明', group: 'waiting', kind: i < 5 ? 'no_match' : 'standard', source: '合成试卷 ' + String.fromCharCode(65 + i % 3) + ' · 第 ' + (i + 8) + ' 题'});
    for (let i = 0; i < (scene === 'current' ? 1 : 3); i++) items.push({id: 'M0' + (i + 1), title: ['缺少图中标注的几何证明', '题干缺失一项已知条件', '题目图片中的长度无法辨认'][i], chapter: scene === 'current' ? '八上 · 综合示例' : '第一章 · 三角形的证明', group: 'manual', kind: 'source', source: '合成试卷 C · 第 ' + (18 + i) + ' 题'});
    return items;
  },
  reason(item) {
    return {legacy: '历史记录只有“未归类”标记，缺少匹配结论，不能视为已确认无匹配。', analysis: '上次分析未完成，尚未形成完整的判定点与基础标签。', match: '基础标签已完成，但尚未完成与现有题型的匹配。', no_match: '已有明确的匹配结论：当前候选题型没有覆盖本题的主要解题要求。', standard: '题意可以识别，但当前知识点词表没有覆盖这一要求，需要先核对或整理标准。', source: '原题缺少必要条件，继续分析无法得到可靠的判定依据。'}[item.kind];
  },
  shortReason(item) { return {legacy:'缺少可核验的匹配结论',analysis:'基础分析未完成',match:'尚未匹配已有题型',no_match:'已有题型无匹配',standard:'知识点词表未覆盖',source:'原题缺少必要条件'}[item.kind]; },
  next(item) { return {legacy:'补一次已有题型匹配',analysis:'补基础分析，再匹配题型',match:'补一次已有题型匹配',no_match:'加入本章题型整理',standard:'核对或补充知识标准',source:'核对并补足原题条件'}[item.kind]; },
  count(group) { return this.state.items.filter(q => q.group === group).length; },
  currentItem() { return this.state.items.find(q => q.id === this.state.selected) || this.state.items.find(q => q.group === this.state.group); }
};
if (!['A','B','C'].includes(P.state.variant)) P.state.variant = 'A';
if (!['demo','current'].includes(P.state.scene)) P.state.scene = 'demo';
