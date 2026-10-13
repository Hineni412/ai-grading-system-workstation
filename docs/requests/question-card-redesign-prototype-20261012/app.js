/* 原型交互：渲染收起/展开两种卡片状态，工具条与卡片头部均可切换 */
(function () {
'use strict';
var Q = window.QB_QUESTIONS, FIG = window.QB_FIG;
var FREQ_TIP = '同主要关键步骤、同难度档的题出现在该阶段同源卷组中的比例';
var SRC_EXAM = '本学期考试中该原题的班级得分率（有效最终分合计 ÷ 满分合计）';
var SRC_TYPE = '与左侧相同范围的主题型群体掌握度与四档人数';
var SRC_STUCK = '该主题型下、本题直接关联的关键步骤的独立失分人数与反复卡住人数（反复卡点：同一学生同一关键步骤在至少 2 道不同小问独立失分）';
/* 标签维度：key、行标签、配色 class */
var DIMS = [
  ['form', '作答形式', 'd-form'], ['diff', '难度', 'd-diff'], ['typeMain', '主题型', 'd-main'],
  ['typeSub', '次题型', 'd-sub'], ['chapter', '章节', 'd-ch'], ['kp', '知识点', 'd-kp'],
  ['ability', '能力', 'd-ab'], ['idea', '数学思想', 'd-idea'], ['method', '解题方法', 'd-method'],
  ['model', '数学模型', 'd-model'], ['special', '特殊考法', 'd-sp']
];
/* 3 张收起 + 1 张展开；第6题同时给出展开态示例，便于与收起态对比 */
var CARDS = [
  { q: 'q6', state: 'collapsed' },
  { q: 'q13', state: 'collapsed' },
  { q: 'q11', state: 'collapsed' },
  { q: 'q6', state: 'expanded', tag: '第 6 题·展开态' }
];

function rc(v) { return v == null ? 'v-grey' : v < 40 ? 'v-red' : v < 60 ? 'v-amber' : v < 80 ? 'v-teal' : 'v-green'; }
function fillCls(v) { return 'fill-' + rc(v).slice(2); }
function segInner(dist) {
  var t = 0; dist.forEach(function (n) { t += n; }); t = t || 1;
  return dist.map(function (n, i) {
    return '<i class="s' + (i + 1) + '" style="width:' + (n / t * 100).toFixed(1) + '%"></i>';
  }).join('');
}
function stemHTML(q) {
  var fig = q.fig ? '<span class="stem-fig">' + FIG[q.fig] + '</span>' : '';
  var opts = q.options ? '<div class="options">' + q.options.map(function (o, i) {
    return '<span>' + 'ABCD'[i] + '. ' + o + '</span>';
  }).join('') + '</div>' : '';
  return '<div class="stem">' + fig + '<p>' + q.stem + '</p>' + opts + '</div>';
}

/* ===== 考情一行（收起/展开共用） ===== */
function ilineHTML(q, withBtn) {
  var e = q.exam, t = q.type, s = q.stuck, f = q.freq;
  var h = '<span class="ii" title="' + SRC_EXAM + '"><span class="pre">本校</span> '
    + (e ? '考过' + e.count + '次 <b class="' + rc(e.rate) + '">' + e.rate + '%</b>' : '<span class="dim">未考</span>') + '</span>'
    + '<span class="ii" title="' + SRC_TYPE + '"><span class="pre">题型掌握</span> <b class="' + rc(t.rate) + '">' + t.rate + '%</b>'
    + ' <span class="dim">需关注' + t.need + '/' + t.total + '</span><span class="seg">' + segInner(t.dist) + '</span></span>'
    + '<span class="ii" title="' + SRC_STUCK + '"><span class="pre">卡点</span> '
    + (s ? '<b class="v-amber">' + s.step + ' ' + s.repeated + '人反复</b>' + (s.common ? '<span class="badge">共性</span>' : '')
      : '<span class="dim">无反复卡点</span>') + '</span>'
    + '<span class="ii" title="' + FREQ_TIP + '"><span class="pre">考频</span> '
    + (f ? '<b class="' + rc(f.final) + '">期末' + f.final + '%</b> <b class="' + rc(f.mid) + '">期中' + f.mid + '%</b>'
      : '<span class="dim">暂无考频</span>') + '</span>';
  if (withBtn) h += '<button class="xbtn" data-act="detail">考情详情 ▸</button>';
  return '<div class="iline">' + h + '</div>';
}

/* ===== 考情详情（展开卡内可折叠的三列小格） ===== */
function idetailHTML(q) {
  var c1 = '<div><div class="h">本校作答</div>'
    + (q.examRows && q.examRows.length
      ? q.examRows.map(function (r) {
          return '<div class="exam-row"><span>' + r.ref + '</span><b class="' + rc(r.rate) + '">' + r.rate + '%</b></div>'
            + '<div class="rate-bar"><i class="' + fillCls(r.rate) + '" style="width:' + r.rate + '%"></i></div>';
        }).join('')
      : '<div class="row dim">本学期同源卷组中未出现该原题</div>') + '</div>';
  var t = q.type;
  var c2 = '<div><div class="h">本题型学情 · ' + q.typeMain + '</div>'
    + '<div class="row"><b class="' + rc(t.rate) + '">掌握度 ' + t.rate + '%</b></div>'
    + '<div class="segbar">' + segInner(t.dist) + '</div>'
    + '<div class="legend">明显薄弱 ' + t.dist[0] + ' · 还不稳 ' + t.dist[1] + ' · 较稳定 ' + t.dist[2] + ' · 证据不足 ' + t.dist[3] + '</div></div>';
  var c3 = '<div><div class="h">卡在哪一步（本题关键步骤）</div>'
    + (q.stuckRows || []).slice(0, 3).map(function (s) {
      return '<div class="row">' + s.step + ' · <span class="dim">独立失分 ' + s.indep + '</span> · '
        + (s.repeated ? '<b class="v-amber">反复' + s.repeated + '人</b>' : '<span class="dim">反复0人</span>')
        + (s.common ? '<span class="badge">共性</span>' : '') + '</div>';
    }).join('') + '</div>';
  return '<div class="idetail">' + c1 + c2 + c3 + '</div>';
}

/* ===== 收起态标签行：作答形式 · 难度 · 主题型 · 知识点(≤2, +N) ===== */
function tagsRowHTML(q) {
  var h = '<span class="tag d-form">' + q.form + '</span><span class="tag d-diff">难度 ' + q.diff + '</span>'
    + '<span class="tag d-main">' + q.typeMain + '</span>';
  (q.kp || []).slice(0, 2).forEach(function (k) { h += '<span class="tag d-kp">' + k + '</span>'; });
  if ((q.kp || []).length > 2) h += '<span class="tag d-kp">+' + (q.kp.length - 2) + '</span>';
  return '<div class="tags">' + h + '</div>';
}

/* ===== 展开卡右栏：题目标签面板（每维度一行） ===== */
function tagpanelHTML(q) {
  var rows = '';
  DIMS.forEach(function (d) {
    var key = d[0], lab = d[1], cls = d[2];
    if (key === 'diff') {
      rows += '<div class="tprow"><span class="tl">难度</span><span class="tvs"><span class="tag d-diff">' + q.diff
        + '</span><span class="meter"><i style="width:' + (q.diff * 10) + '%"></i></span></span></div>';
      return;
    }
    var vals = q[key];
    if (typeof vals === 'string') vals = vals ? [vals] : [];
    vals = (vals || []).filter(function (v) { return v && v !== '无'; });
    if (!vals.length) return;
    rows += '<div class="tprow"><span class="tl">' + lab + '</span><span class="tvs">'
      + vals.map(function (v) { return '<span class="tag ' + cls + '">' + v + '</span>'; }).join('') + '</span></div>';
  });
  return '<aside class="tagpanel"><h4>题目标签</h4>' + rows + '</aside>';
}
function collapsedHTML(q) {
  return '<header class="qcard-head">'
    + '<button class="qcard-title" data-act="toggle">' + q.no + '</button>'
    + '<span class="qcard-source">' + q.source + '</span>'
    + '<span class="head-acts"><label class="select-lab"><input type="checkbox"> 选入</label>'
    + '<button class="chev" data-act="toggle" title="展开">▾</button></span></header>'
    + '<div class="qcard-body">' + stemHTML(q) + tagsRowHTML(q) + ilineHTML(q, false) + '</div>'
    + '<footer class="qcard-foot"><small>判定点 ' + q.points.length + '</small>'
    + '<span class="foot-acts"><button class="btn">展开标注</button><button class="btn">相似题</button>'
    + '<button class="btn-sec">加入试卷篮</button></span></footer>';
}

function pointsHTML(q) {
  return '<h4>判定点（评分依据）<small>· ' + q.points.length + ' 个 · 全部质检通过</small></h4>'
    + q.points.map(function (p, i) {
      return '<div class="pt-row"><span class="pt-num">' + (i + 1) + '</span><div>'
        + '<div class="t">' + p.t + '</div><div class="d">' + p.d + '</div>'
        + '<div class="ks">' + (p.ks || []).map(function (k) { return '<span class="tag d-kp">' + k + '</span>'; }).join('')
        + (p.pre ? '<span class="pre">前置知识 · ' + p.pre + '</span>' : '') + '</div></div></div>';
    }).join('');
}

function wrongsHTML(q) {
  return '<h4>本题典型错法</h4>' + q.wrongs.map(function (w) {
    return '<div class="wrong-row"><span class="opt">' + w.o + '</span>'
      + '<div class="wc"><div class="t">' + w.t + '</div><div class="d">' + w.d + '</div>'
      + '<div class="m">' + w.m + '</div></div>'
      + '<span class="row-acts"><button>调整</button><button>驳回</button></span></div>';
  }).join('');
}

function expandedHTML(q) {
  return '<header class="qcard-head">'
    + '<button class="qcard-title" data-act="toggle">' + q.no + '</button>'
    + '<span class="qcard-source">' + q.source + '</span>'
    + '<span class="head-acts"><button class="btn-sec">加入试卷篮</button>'
    + '<button class="btn">相似题</button><button class="btn">打开原卷</button>'
    + '<details class="menu"><summary>编辑 ▾</summary><div class="menu-pop">'
    + '<a>修改题型</a><a>编辑判定点</a><a>编辑标签与小节</a></div></details>'
    + '<button class="btn" data-act="toggle">收起 ▲</button></span></header>'
    + '<div class="qcard-body x">' + ilineHTML(q, true) + idetailHTML(q)
    + '<div class="sec-a"><div>' + stemHTML(q)
    + '<details class="answer"><summary>答案与解析 ▸</summary><div class="a-body">' + q.answer + '</div></details></div>'
    + tagpanelHTML(q) + '</div>'
    + '<div class="sec-c"><div>' + pointsHTML(q) + '</div><div>' + wrongsHTML(q) + '</div></div></div>';
}

/* ===== 装配 ===== */
var list = document.getElementById('card-list');
var tbar = document.getElementById('tb-btns');
function renderCard(card) {
  var q = Q[card.q];
  card.el.className = 'qcard' + (card.state === 'expanded' ? ' is-expanded' : '');
  card.el.innerHTML = card.state === 'expanded' ? expandedHTML(q) : collapsedHTML(q);
  card.btn.textContent = (card.tag ? card.tag : q.no) + '：' + (card.state === 'expanded' ? '收起' : '展开');
  if (card.state === 'expanded' && /[?&]detail=1/.test(location.search)) setDetail(card.el, true);
}
function setDetail(cardEl, open) {
  var d = cardEl.querySelector('.idetail'), b = cardEl.querySelector('[data-act="detail"]');
  if (d) d.classList.toggle('open', open);
  if (b) b.textContent = '考情详情 ' + (open ? '▾' : '▸');
}
function toggle(card) {
  card.state = card.state === 'expanded' ? 'collapsed' : 'expanded';
  renderCard(card);
}
CARDS.forEach(function (card) {
  card.el = document.createElement('section');
  list.appendChild(card.el);
  card.btn = document.createElement('button');
  card.btn.addEventListener('click', function () { toggle(card); });
  tbar.appendChild(card.btn);
  renderCard(card);
});
list.addEventListener('click', function (e) {
  var b = e.target.closest('[data-act]');
  if (!b) return;
  var el = b.closest('.qcard');
  if (b.dataset.act === 'detail') {
    var d = el.querySelector('.idetail');
    setDetail(el, d && !d.classList.contains('open'));
    return;
  }
  if (b.dataset.act !== 'toggle') return;
  for (var i = 0; i < CARDS.length; i++) if (CARDS[i].el === el) { toggle(CARDS[i]); break; }
});
})();
