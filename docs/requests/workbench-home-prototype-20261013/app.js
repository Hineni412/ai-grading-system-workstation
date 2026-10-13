/* 工作台首页改版原型 · 渲染与切换（file:// 可直接打开，普通 script，无模块） */
(function () {
'use strict';
var D = window.WB_PROTO;
var q = new URLSearchParams(location.search);
var V = ['now', 'l1', 'l1plus'].indexOf(q.get('v')) >= 0 ? q.get('v') : 'now';
var S = q.get('s') && D.scenarios[q.get('s')] ? q.get('s') : 'done';
var W = q.get('w') === 'narrow' ? 'narrow' : 'wide';
var sc = D.scenarios[S];
var examById = {};
D.exams.forEach(function (e) { examById[e.id] = e; });
var cur = examById[sc.current];
var isLatest = sc.current === D.exams[0].id;
var p = sc.progress, r = sc.review, a = sc.anomalies;
var gradeDone = p.matched > 0 && p.graded + p.failed === p.matched && p.failed === 0;
var allDone = gradeDone && r.items === 0;
var plus = V === 'l1plus';

function badge(tone, label) { return '<span class="badge ' + tone + '">' + label + '</span>'; }
function pct(x, y) { return y ? Math.min(100, Math.round(x / y * 100)) : 0; }

/* ===== 待办行（忠实复刻 workbench-home.ts 的 buildTodoRows 规则） ===== */
function buildTodos() {
  var need = [], cont = [];
  function row(badgeTone, badgeLabel, title, fact, action, go) {
    return { tone: badgeTone, label: badgeLabel, title: title, fact: fact, action: action, go: go };
  }
  var trainRows = [];
  (sc.training || []).forEach(function (it) {
    if (it.scanPages > 0) trainRows.push(row('success', '训练', '核对 ' + it.scanPages + ' 页训练卷扫描', it.draft, '去核对', '训练卷扫描核对'));
    if (it.reviewCount > 0) trainRows.push(row('success', '训练', '复核 ' + it.draft + ' 判定 ' + it.reviewCount + ' 份',
      '不确定、无法辨认、缺失判定点或判定失败，仍需教师处理', '去复核', '训练复核'));
    if (it.publishCount > 0) trainRows.push(row('success', '训练', '发布 ' + it.draft + ' 证据 ' + it.publishCount + ' 份',
      '判定已完成，当前修订的训练证据尚未全部发布', '去发布', '训练发布'));
  });
  /* 本原型各场景考试配置均已完成、已有答卷，故无「完成考试配置 / 上传答卷」行 */
  if (a.unmatched > 0 || a.scanIssues > 0 || a.failed > 0) {
    var fact = [a.unmatched && a.unmatched + ' 份答卷未匹配学生',
      a.scanIssues && a.scanIssues + ' 名学生扫描页有问题',
      a.failed && a.failed + ' 份批改失败'].filter(Boolean).join('，');
    need.push(row('info', '考试', '处理答卷异常', fact, '处理异常', '批改执行页'));
  }
  var gradingActive = sc.jobs.length > 0;
  if (p.matched > 0 && p.graded + p.failed < p.matched && !gradingActive) {
    need.push(row('info', '考试', '继续批改', '已批改 ' + p.graded + '/' + p.matched + ' 份', '继续批改', '批改执行页'));
  }
  if (r.items > 0) {
    need.push(row('info', '考试', '复核 ' + r.items + ' 项评分', '涉及 ' + r.questions + ' 道题', '开始复核', '考试批改页'));
  }
  need = need.concat(trainRows);
  if (p.graded > 0) {
    cont.push(row('info', '考试', '看卷 10 分钟', cur.name + ' · 已批改 ' + p.graded + '/' + p.matched + ' 份', '去看卷', '成绩中心 · 看卷'));
  }
  if (sc.reports.stale > 0) {
    cont.push(row('info', '考试', '个人报告：' + sc.reports.stale + ' 人需重新生成', sc.reports.current + ' 人已是最新', '去查看', '成绩中心 · 个人报告'));
  }
  if (allDone) {
    cont.push(row('ai', '组卷', '按失分题组讲义', '班级组卷默认依据最近两场考试，列出得分率低于 70% 的题', '去组卷', '组卷工作台'));
  }
  return { need: need, cont: cont };
}

/* ===== 五步进度（复刻 buildExamSteps） ===== */
function examSteps() {
  var prepareDone = p.total > 0 && a.unmatched === 0 && a.scanIssues === 0;
  var reviewDone = gradeDone && r.items === 0;
  return [
    { label: '考试配置', status: 'done', hint: '已完成' },
    { label: '答卷与预检', status: prepareDone ? 'done' : p.total > 0 ? 'in_progress' : 'todo',
      hint: !p.total ? '待上传' : prepareDone ? p.matched + ' 份已匹配' : p.matched + '/' + p.total + ' 份已匹配' },
    { label: '批改', status: gradeDone ? 'done' : (p.matched > 0 || p.failed > 0) ? 'in_progress' : 'todo',
      hint: !p.matched ? '—' : p.graded + '/' + p.matched + (p.failed ? ' · 失败 ' + p.failed : '') },
    { label: '复核', status: reviewDone ? 'done' : r.items > 0 ? 'in_progress' : 'todo',
      hint: r.items > 0 ? r.items + ' 项待复核' : reviewDone ? '0 项' : '等待批改' },
    { label: '成绩与讲评', status: gradeDone && reviewDone ? 'done' : 'todo',
      hint: gradeDone && reviewDone ? '可查看' : '复核后可查看' }
  ];
}
function stepsHTML() {
  var steps = examSteps();
  var currentIdx = 0;
  for (var i = 0; i < steps.length; i++) if (steps[i].status !== 'done') { currentIdx = i; break; } else currentIdx = i;
  if (steps.every(function (s) { return s.status === 'done'; })) currentIdx = steps.length - 1;
  var h = '<ol class="steps">';
  steps.forEach(function (st, i) {
    if (i > 0) h += '<li class="step-conn' + (steps[i - 1].status === 'done' ? ' is-done' : '') + '"></li>';
    var node = st.status === 'done' ? '<i class="step-node"><i class="tick">✓</i></i>'
      : st.status === 'in_progress' ? '<i class="step-node"><i class="dot"></i></i>'
      : '<i class="step-node">' + (i + 1) + '</i>';
    h += '<li class="step is-' + st.status + (i === currentIdx ? ' is-current' : '') + '">'
      + '<button type="button" class="step-btn" data-go="步骤：' + st.label + '" title="' + st.hint + '">'
      + node + '<span class="step-label">' + st.label + '</span><span class="step-hint">' + st.hint + '</span></button></li>';
  });
  return h + '</ol>';
}

function todoRow(rw, variant, arrow) {
  return '<div class="todo-row">' + badge(rw.tone, rw.label)
    + '<div class="todo-copy"><strong>' + rw.title + '</strong><p>' + rw.fact + '</p></div>'
    + '<button type="button" class="btn ' + variant + '" data-go="' + rw.go + '">' + rw.action + (arrow ? ' →' : '') + '</button></div>';
}
function jobRow(j) {
  var pc = Math.round(j.progress * 100);
  return '<div class="todo-row run-row">' + badge('neutral', '任务')
    + '<div class="todo-copy"><strong>' + j.name + '</strong><p>' + j.detail + '</p></div>'
    + '<span class="wbar run"><i style="width:' + pc + '%"></i></span><span class="num">' + pc + '%</span></div>';
}

/* ===== 待处理面板 ===== */
function panelNow() {
  var t = buildTodos();
  var h = '<header class="p-head"><h2>待处理</h2><span>' + (t.need.length + t.cont.length) + ' 项</span></header>';
  if (sc.jobs.length) h += '<h3 class="g-title">正在运行</h3>' + sc.jobs.map(jobRow).join('');
  if (t.need.length) {
    h += '<h3 class="g-title">需要处理</h3>' + t.need.map(function (rw, i) { return todoRow(rw, i === 0 ? 'primary' : 'secondary'); }).join('');
  }
  if (t.cont.length) {
    h += '<h3 class="g-title">可以继续</h3>' + t.cont.map(function (rw) { return todoRow(rw, 'ghost', true); }).join('');
  }
  if (!sc.jobs.length && !t.need.length && !t.cont.length) h += '<p class="quiet">今天没有待处理的事项</p>';
  return h;
}
function panelL1() {
  var need = buildTodos().need;
  var h = '<header class="p-head"><h2>需要处理</h2><span>' + need.length + ' 项</span></header>';
  if (sc.jobs.length) h += '<h3 class="g-title">正在运行</h3>' + sc.jobs.map(jobRow).join('');
  if (need.length) h += need.map(function (rw, i) { return todoRow(rw, i === 0 ? 'primary' : 'secondary'); }).join('');
  else h += '<p class="all-clear"><span class="ok-dot">✓</span>当前没有需要处理的事项</p>';
  return h;
}
/* ===== 当前考试卡 ===== */
function reportLine() {
  if (!sc.reports.stale) return '';
  return '<p class="report-line">个人报告 <b>' + sc.reports.stale + ' 人</b>需重新生成 · '
    + '<button type="button" class="linkbtn" data-go="成绩中心 · 个人报告">去查看</button></p>';
}
function cardNow() {
  return '<header class="p-head"><h2>当前考试</h2></header>'
    + '<p class="exam-name">' + cur.name + '</p>'
    + '<p class="meta">' + cur.date + ' · ' + p.total + ' 份答卷</p>'
    + stepsHTML()
    + '<button type="button" class="btn ghost foot-link" data-go="成绩中心">成绩中心 →</button>';
}
function cardL1() {
  var h = '<header class="p-head"><h2>当前考试</h2></header>'
    + '<p class="exam-name">' + cur.name + '</p>'
    + '<p class="meta">' + cur.date + ' · ' + p.total + ' 份答卷</p>';
  if (allDone) {
    h += '<div class="done-line"><span class="ok-dot">✓</span>已全部完成 · ' + p.matched + ' 份答卷已批改</div>'
      + '<details class="steps-more"><summary>查看步骤</summary>' + stepsHTML() + '</details>'
      + reportLine()
      + '<div class="card-acts"><button type="button" class="btn primary" data-go="成绩中心">成绩中心</button>'
      + (isLatest && p.graded > 0 ? '<button type="button" class="btn secondary" data-go="成绩中心 · 看卷">看卷 10 分钟</button>' : '')
      + (isLatest ? '<button type="button" class="btn secondary" data-go="组卷工作台">按失分题组讲义</button>' : '')
      + '</div>';
  } else {
    h += stepsHTML() + reportLine()
      + '<div class="card-acts"><button type="button" class="btn ghost foot-link" data-go="成绩中心">成绩中心 →</button>'
      + (isLatest && p.graded > 0 ? '<button type="button" class="btn ghost foot-link" data-go="成绩中心 · 看卷">看卷 10 分钟 →</button>' : '')
      + '</div>';
  }
  return h;
}

/* ===== 本学期学情卡（三版式一致） ===== */
function insightCard() {
  var d = D.insight;
  return '<header class="p-head"><h2>本学期学情</h2></header>'
    + '<p class="meta">' + d.term + ' · ' + d.scope + '</p>'
    + '<dl class="insight-stats"><div><dt>有作答证据</dt><dd>' + d.evidence.got + '<small>/' + d.evidence.total + ' ' + d.evidence.unit + '</small></dd></div>'
    + '<div><dt>需关注' + d.evidence.unit + '</dt><dd>' + d.attention + '<small>项</small></dd></div></dl>'
    + '<p class="insight-cap">优先关注</p>'
    + d.top.map(function (n, i) {
      return '<button type="button" class="target" data-go="学情 · ' + n.name + '"><span class="rank">' + (i + 1) + '</span>'
        + '<span class="t-name"><strong>' + n.name + '</strong><small>' + n.loc + '</small></span>'
        + '<span class="t-count"><strong>' + n.need + '/' + n.total + '</strong><small>人需关注</small></span></button>';
    }).join('')
    + '<p class="insight-cap def">' + d.note + '</p>'
    + '<p class="insight-src">' + d.source + '</p>'
    + '<button type="button" class="btn ghost foot-link" data-go="知识与训练 · 学情总览">查看学情 →</button>';
}

/* ===== 最近考试表 ===== */
var STATUS = { completed: ['success', '已完成'], grading: ['info', '批改中'], created: ['neutral', '待开始'], failed: ['danger', '有失败记录'] };
function tableNow() {
  var h = '<header class="p-head"><h2>最近考试</h2><span>近 5 场</span></header>'
    + '<table class="app-table exams"><thead><tr><th>考试</th><th>创建日期</th><th class="num">答卷</th><th class="num">批改</th><th>状态</th><th></th></tr></thead><tbody>';
  sc.rows.forEach(function (rw) {
    var e = examById[rw.id], isCur = rw.id === sc.current;
    h += '<tr' + (isCur ? ' class="is-current"' : '') + '>'
      + '<td class="ex-name"><button type="button" data-go="切换考试：' + e.name + '">' + e.name + '</button>' + (isCur ? badge('neutral', '当前') : '') + '</td>'
      + '<td>' + e.date + '</td><td class="num">' + e.papers + ' 份</td>'
      + '<td class="num"><span class="tprog"><span class="wbar"><i style="width:' + pct(rw.graded, rw.matched) + '%"></i></span><span>' + rw.graded + '/' + rw.matched + '</span></span></td>'
      + '<td><div class="ex-status">' + badge(STATUS[rw.status][0], STATUS[rw.status][1]) + (rw.review > 0 ? badge('warning', '有待复核') : '') + '</div></td>'
      + '<td>' + (isCur ? '' : '<button type="button" class="btn ghost" data-go="成绩中心 · ' + e.name + '">看成绩</button>') + '</td></tr>';
  });
  return h + '</tbody></table>';
}
function tableL1() {
  var RATE_TIP = '有效得分合计 ÷ 满分合计；不同考试难度不同，只作参考';
  var h = '<header class="p-head"><h2>最近考试</h2><span>近 5 场</span></header>'
    + '<table class="app-table exams"><thead><tr><th>考试</th><th>日期</th><th class="num">答卷</th>'
    + (plus ? '<th class="num" title="' + RATE_TIP + '">平均得分率<span class="th-note">样例数据</span></th>' : '')
    + '<th>进度</th><th></th></tr></thead><tbody>';
  sc.rows.forEach(function (rw, i) {
    var e = examById[rw.id], isCur = rw.id === sc.current;
    var rate = '';
    if (plus) {
      if (i === sc.rows.length - 1) rate = '<span class="rate-none">—</span>';
      else {
        var diff = e.avgRate - examById[sc.rows[i + 1].id].avgRate;
        rate = '<span class="rate">' + e.avgRate + '%</span>'
          + (diff > 0 ? '<span class="rate-d up">↑' + diff + '</span>'
            : diff < 0 ? '<span class="rate-d down">↓' + (-diff) + '</span>'
            : '<span class="rate-d flat">±0</span>');
      }
      rate = '<td class="num" title="' + RATE_TIP + '">' + rate + '</td>';
    }
    var prog = rw.status === 'completed'
      ? '<span class="ok">✓ 已完成</span>'
      : '<span class="tprog"><span class="wbar"><i style="width:' + pct(rw.graded, rw.matched) + '%"></i></span><span>' + rw.graded + '/' + rw.matched + '</span></span>';
    h += '<tr' + (isCur ? ' class="is-current"' : '') + '>'
      + '<td class="ex-name"><button type="button" data-go="切换考试：' + e.name + '">' + e.name + '</button></td>'
      + '<td>' + e.date + '</td><td class="num">' + e.papers + ' 份</td>' + rate
      + '<td><div class="prog-cell">' + prog + (rw.review > 0 ? badge('warning', '待复核 ' + rw.review) : '') + '</div></td>'
      + '<td><button type="button" class="btn ghost" data-go="成绩中心 · ' + e.name + '">看成绩</button></td></tr>';
  });
  return h + '</tbody></table>';
}

/* ===== 装配 ===== */
var shell = document.getElementById('shell');
shell.className = 'shell w-' + W + ' v-' + V;
document.getElementById('ph-meta').textContent = D.greeting + ' · ' + D.today + ' · ' + D.term;
var sbStatus = document.getElementById('sb-status');
sbStatus.textContent = sc.rows.filter(function (rw) { return rw.id === sc.current; })[0].status === 'grading' ? '批改中' : '已完成';
sbStatus.className = 'sb-exam-status ' + (sc.rows.filter(function (rw) { return rw.id === sc.current; })[0].status === 'grading' ? 'run' : 'done');
document.getElementById('sb-exam-name').textContent = cur.name;
document.getElementById('sb-exam-meta').textContent = D.term + ' · ' + cur.date;
document.getElementById('todo-panel').innerHTML = V === 'now' ? panelNow() : panelL1();
document.getElementById('wb-side').innerHTML =
  '<section class="panel wb-current">' + (V === 'now' ? cardNow() : cardL1()) + '</section>'
  + '<section class="panel wb-insight">' + insightCard() + '</section>';
document.getElementById('recent-panel').innerHTML = V === 'now' ? tableNow() : tableL1();

/* ===== 底部工具条 ===== */
var GROUPS = [
  ['版式', 'v', [['now', '现状'], ['l1', '第1档整理'], ['l1plus', '第1档+得分率']], V],
  ['场景', 's', [['done', '全部完成'], ['todo', '有必办'], ['grading', '正在批改'], ['old', '以前考试']], S],
  ['宽度', 'w', [['wide', '1440 宽'], ['narrow', '1100 窄屏']], W]
];
var tb = document.getElementById('toolbar');
var tbH = '<span class="tb-label">原型工具条</span>';
GROUPS.forEach(function (g) {
  tbH += '<span class="tb-group"><em>' + g[0] + '</em>' + g[2].map(function (opt) {
    var np = new URLSearchParams(location.search);
    np.set('v', V); np.set('s', S); np.set('w', W); np.set(g[1], opt[0]);
    return '<button type="button" class="' + (g[3] === opt[0] ? 'on' : '') + '" data-url="?' + np.toString() + '">' + opt[1] + '</button>';
  }).join('') + '</span>';
});
tb.innerHTML = tbH;
tb.addEventListener('click', function (e) {
  var b = e.target.closest('[data-url]');
  if (b) location.search = b.getAttribute('data-url');
});

/* ===== 操作按钮提示（不跳转） ===== */
var toastEl = document.getElementById('toast'), toastTimer = null;
document.addEventListener('click', function (e) {
  var b = e.target.closest('[data-go]');
  if (!b) return;
  toastEl.textContent = '原型：将进入 ' + b.getAttribute('data-go');
  toastEl.classList.add('show');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(function () { toastEl.classList.remove('show'); }, 1600);
});
})();
