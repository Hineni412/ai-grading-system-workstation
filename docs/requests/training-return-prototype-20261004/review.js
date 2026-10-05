/* review.js — 待复核/待更新形态：逐题判定点列表 + 台账底栏 */

const DOT = { hit:['d-ok','达成'], miss:['d-no','未达成'], unread:['d-warn','无法辨认'] };
const uncLabel = p => p.unc === 'ai-unread' ? 'AI 无法辨认' : p.unc === 't-unc' ? '教师标为不确定' : 'AI 拿不准';

function ptRow(sub, q, p) {
  const pt = ptab(sub)[q + ':' + p];
  const pend = pt.st === null && !pt.locked;
  const seg = ['hit','miss','unread'].map(v =>
    `<button class="sg ${pt.st === v ? 'on' : ''}" type="button" data-act="seg" data-q="${q}" data-p="${p}" data-v="${v}">${SEG_TXT[v]}</button>`).join('');
  return `<div class="pt ${pend ? 'pend' : ''}" data-pt="${q}:${p}" tabindex="-1">
    <div class="ptx"><span class="ptt">${esc(QS[q].pts[p])}</span>
      <span class="aiev">AI：${esc(QS[q].ev[p])}</span>
      ${pt.note ? `<span class="ptnote">备注：${esc(pt.note)}</span>` : ''}</div>
    <div class="ptc"><span class="seg">${seg}</span>
      <span class="otag">${pt.locked ? '教师已锁定' : 'AI'}</span>
      <details class="pm"><summary>⋯</summary><div class="pmenu">
        <button type="button" data-act="punc" data-q="${q}" data-p="${p}">标为不确定</button>
        <input class="notein" data-note="${q}:${p}" placeholder="备注…" value="${esc(pt.note)}"></div></details></div>
    ${pend ? `<span class="unc">${uncLabel(pt)}</span>` : ''}</div>`;
}

// 全部判定点（可按"只看待复核"过滤到未定项）
function renderPts(sub, onlyPend) {
  const t = ptab(sub);
  return QS.map((qs, q) => {
    const pendN = Array.from({ length: QN[q] }, (_, p) => t[q + ':' + p]).filter(p => p.st === null && !p.locked).length;
    if (onlyPend && !pendN) return '';
    const dots = Array.from({ length: QN[q] }, (_, p) => {
      const pt = t[q + ':' + p];
      const [cls, txt] = pt.st ? DOT[pt.st] : ['d-pend', '待复核'];
      return `<span class="dot ${cls}" title="第 ${q + 1} 题 · ${esc(qs.pts[p])} · ${txt}"></span>`;
    }).join('');
    const got = Array.from({ length: QN[q] }, (_, p) => t[q + ':' + p]).filter(p => p.st === 'hit').length;
    const rows = Array.from({ length: QN[q] }, (_, p) => p)
      .filter(p => !onlyPend || (t[q + ':' + p].st === null && !t[q + ':' + p].locked))
      .map(p => ptRow(sub, q, p)).join('');
    return `<div class="qg"><div class="qh"><b>第 ${q + 1} 题</b><span class="dots">${dots}</span>
      <span class="qf">${got}/${QN[q]} 点</span></div>${rows}</div>`;
  }).join('');
}

// 台账底栏（吸底）：本份计数 + 主按钮 + 下一份 + 快捷键提示
function reviewFoot(sub) {
  const pend = pendOf(sub).length;
  return `<div class="lfoot"><div class="lfline">
      <span class="lfs">${pend ? `本份待复核 ${pend} 点` : '本份已全部确定'}</span>
      <button class="btn primary small" type="button" data-act="confirmUpdate">${pend ? `确认已完成的 ${detQ(sub)} 题并更新` : '确认并更新掌握度'}</button>
      <button class="btn ghost small" type="button" data-act="nextNeed">下一份 ›</button></div>
    <div class="lfhint">J/K 切换学生 · Tab 下一个待复核点 · 1/2/3 达成/未达成/无法辨认</div></div>`;
}

function renderReview(sub) {
  const pend = pendOf(sub).length;
  return `<div class="lhead"><span class="lht">达成 ${gotOf(sub)}/${TOTAL_PTS} 点 · ${pend ? `待复核 ${pend} 点` : '已全部确定'}</span>
      ${srcTag(SRC.ledger)}${srcTag(SRC.lock)}
      <label class="onlyp"><input type="checkbox" data-ch="onlyPend" ${S.onlyPend ? 'checked' : ''}> 只看待复核</label></div>
    <div class="lbody">
      ${sub.edited ? '<div class="editbanner">判定已修改，需重新确认并更新掌握度</div>' : ''}
      <details class="reqlog"><summary>请求记录 · 10 题 · 已请求 1 次 · 12,340 tokens</summary>
        <div class="rqd">模型 huanjing-1 · 判定 ${TOTAL_PTS} 点 · 用时 38s · tokens 12,340（输入 9,870 / 输出 2,470）</div></details>
      <div class="pts">${renderPts(sub, S.onlyPend)}</div></div>
    ${reviewFoot(sub)}`;
}
