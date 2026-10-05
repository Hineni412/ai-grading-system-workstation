/* views.js — 各面板渲染（待处理 / 最近考试 / 当前考试 / 本学期学情） */

const $ = (s, r) => (r || document).querySelector(s);
const $$ = (s, r) => Array.from((r || document).querySelectorAll(s));
const esc = s => String(s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

const BADGE = { '考试': 'info', '训练': 'ok', '题库': 'ai', '任务': 'gray' };
const badge = mod => `<span class="bd ${BADGE[mod] || 'gray'}">${mod}</span>`;
const srcTag = s => `<span class="srctag">${esc(s)}</span>`;
const pHead = (t, cnt, src) =>
  `<div class="ph"><h2>${t}</h2>${cnt ? `<span class="cnt">${cnt}</span>` : ''}${src ? srcTag(src) : ''}</div>`;

// ---- 待处理 ----
function renderTodo(d) {
  const n = d.run.length + d.need.length + d.cont.length;
  let h = pHead('待处理', `${n} 项`);
  if (!n) return h + `<div class="quiet">今天没有待处理的事项</div>`;

  if (d.run.length) {
    h += `<div class="subh">正在运行</div>` + d.run.map(r => `
      <div class="trow run">${badge(r.mod)}<span class="tnm">${esc(r.title)}</span>
        <span class="pbar"><i style="width:${r.pct}%"></i></span><span class="pct">${r.pct}%</span>
        <span class="stage">${esc(r.stage)}</span>${srcTag(r.src)}
        <button class="btn ghost small" type="button" data-t="将进入 ${r.toast}">${r.act}</button>
      </div>`).join('');
  }
  if (d.need.length) {
    h += `<div class="subh">需要处理</div>` + d.need.map((r, i) => `
      <div class="trow">${badge(r.mod)}<span class="tt"><span class="tnm">${esc(r.title)}</span>
        <span class="tft">${esc(r.fact)}${srcTag(r.src)}</span></span>
        <button class="${i === 0 ? 'btn primary' : 'btn small'}" type="button" data-t="将进入 ${r.toast}">${r.act}</button>
      </div>`).join('');
  }
  if (d.cont.length) {
    h += `<div class="subh">可以继续</div>` + d.cont.map(r => `
      <div class="trow">${badge(r.mod)}<span class="tt"><span class="tnm">${esc(r.title)}</span>
        <span class="tft">${esc(r.fact)}${srcTag(r.src)}</span></span>
        <button class="btn ghost small" type="button" data-t="将进入 ${r.toast}">${r.act} →</button>
      </div>`).join('');
  }
  return h;
}

// ---- 最近考试 ----
function renderExams(d) {
  let h = pHead('最近考试', '近 5 场', SRC.wk);
  if (d.recent === 'fail') {
    return h + `<div class="quiet">最近考试暂时无法读取</div>
      <div class="qrow"><button class="btn small" type="button" data-t="将重新加载 最近考试">重新加载</button></div>`;
  }
  if (!d.recent.length) {
    return h + `<div class="quiet">还没有考试</div>
      <div class="qrow"><button class="btn small" type="button" data-t="将进入 考试配置 · 新建考试">新建考试</button></div>`;
  }
  return h + `<table class="xt"><thead><tr>
      <th>考试</th><th>日期</th><th>班级</th><th>批改</th><th>复核</th><th>状态</th><th></th>
    </tr></thead><tbody>` + d.recent.map(r => {
      const [a, b] = r.graded.split('/').map(Number);
      const pg = Math.round(a / b * 100);
      const name = r.cur
        ? `<span class="xname">${esc(r.name)}</span> <span class="bd gray">当前</span>`
        : `<button class="xlink" type="button" data-t="将切换当前考试为 ${esc(r.name)}">${esc(r.name)}</button>`;
      return `<tr class="${r.cur ? 'cur' : ''}">
        <td>${name}</td><td>${r.date}</td><td>${r.cls}</td>
        <td><span class="gbar"><i style="width:${pg}%"></i></span>${r.graded}</td>
        <td class="rv${r.warn ? ' warn' : ''}">${r.review}</td>
        <td><span class="bd ${r.tone}">${r.status}</span></td>
        <td><button class="link" type="button" data-t="将进入 ${r.toast}">${r.act}</button></td>
      </tr>`;
    }).join('') + `</tbody></table>`;
}

// ---- 当前考试 ----
function renderExam(d) {
  let h = pHead('当前考试', '', SRC.wk);
  if (d.exam === 'fail') {
    return h + `<div class="quiet">考试概况暂时无法读取</div>
      <div class="qrow"><button class="btn small" type="button" data-t="将重新加载 考试概况">重新加载</button></div>`;
  }
  if (!d.exam) {
    return h + `<div class="quiet">尚未选择考试</div>
      <div class="qrow">
        <button class="btn primary small" type="button" data-t="将进入 考试配置 · 新建考试">新建考试</button>
        <button class="link" type="button" data-t="将打开 选择已有考试">选择已有考试</button>
      </div>`;
  }
  return h + `<div class="exname">${esc(d.exam.name)}</div>
    <div class="exmeta">${d.exam.meta}</div>
    <div class="steps">` + d.steps.map(s => `
      <button class="step ${s.st}" type="button" data-t="将进入 ${s.toast}">
        <span class="sdot">${s.st === 'done' ? '✓' : ''}</span>
        <span class="sl">${s.label}</span><span class="ss">${s.status}</span>
      </button>`).join('') + `</div>
    <div class="pfoot"><button class="link" type="button" data-t="将进入 成绩中心">成绩中心 →</button></div>`;
}

// ---- 本学期学情 ----
function renderSkills(d) {
  let h = pHead('本学期学情', '', SRC.skills);
  if (!d.skills.length) return h + `<div class="quiet">本学期还没有学情证据</div>`;
  return h + `<div class="pmeta">${d.smeta}</div>` + d.skills.map(s => `
    <div class="srow">
      <div class="sl1"><span class="sn">${esc(s.name)} <span class="sch">${esc(s.chap)}</span></span>
        <span class="sw2">${s.w}/${s.t} 人明显薄弱</span></div>
      <div class="hbar"><i style="width:${(s.w / s.t * 100).toFixed(1)}%"></i></div>
    </div>`).join('') +
    `<div class="sev">${d.sev}</div>
    <div class="pfoot"><button class="link" type="button" data-t="将进入 知识与训练 · 学情总览">学情总览 →</button></div>`;
}

function renderAll() {
  const d = STATES[S.demo];
  $('#examcard').innerHTML = `<div class="el">当前考试</div>
    <div class="en">${esc(d.side.name)}</div><div class="es">${esc(d.side.line)}</div>`;
  $('#banner').hidden = S.demo !== 'error';
  $('#pTodo').innerHTML = renderTodo(d.todo);
  $('#pExams').innerHTML = renderExams(d);
  $('#pExam').innerHTML = renderExam(d);
  $('#pSkills').innerHTML = renderSkills(d);
}
