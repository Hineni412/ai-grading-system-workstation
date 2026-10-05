/* ledger.js — 右栏：判定点台账，按所选答卷/异常页状态切换形态 */

const lHead = (t, src) => `<div class="lhead"><span class="lht">${t}</span>${src || ''}</div>`;
const lBody = h => `<div class="lbody">${h}</div>`;

// 异常页选中：匹配表单（匹配到 / 页码 / 三个处理按钮）
function anomLedger(a) {
  const miss = W.subs.find(s => s.status === 'missing');
  const def = W.subs.find(s => s.id === S.anSub) || miss || W.subs[0];
  const opts = W.subs.map(s =>
    `<option value="${s.id}" ${s.id === def.id ? 'selected' : ''}>${esc(s.name)} · ${s.ver}</option>`).join('');
  return `${lHead(`异常页 · 上传文件第 ${a.page} 页`, srcTag('上传文件页 ↔ submissions 手动归组'))}
    ${lBody(`<div class="anf"><div class="anissue">${esc(a.issue)}</div>
      <label class="anlab">匹配到 <select class="ansel" data-ch="anSub">${opts}</select></label>
      <label class="anlab">页码 <input class="anpg" type="number" min="1" max="${def.pages}" value="${def.missPage || 1}" data-ch="anPage"></label>
      <div class="anb">
        <button class="btn primary small" type="button" data-act="anMatch">人工匹配</button>
        <button class="btn small" type="button" data-act="anReplace">明确替换该页</button>
        <button class="btn ghost small" type="button" data-act="anIgnore">忽略此页</button></div></div>`)}`;
}

// 已完成形态：两个台账页签（掌握度变化 / 逐点结果）
function doneLedger(sub) {
  const tabs = `<div class="ltabs">
      <button class="ltab ${S.dtab === 'mastery' ? 'on' : ''}" type="button" data-act="dtab" data-v="mastery">掌握度变化</button>
      <button class="ltab ${S.dtab === 'points' ? 'on' : ''}" type="button" data-act="dtab" data-v="points">逐点结果</button></div>`;
  if (S.dtab === 'points') {
    return `${lHead(`${esc(sub.name)} · 达成 ${gotOf(sub)}/${TOTAL_PTS} 点`, srcTag(SRC.ledger) + srcTag(SRC.lock))}${tabs}
      ${lBody(`${sub.edited ? '<div class="editbanner">判定已修改，需重新确认并更新掌握度</div>' : ''}
        <div class="pts">${renderPts(sub, false)}</div>`)}`;
  }
  const rows = MASTERY.map(m =>
    `<tr><td>${esc(m.goal)}</td><td>${esc(m.before)}</td><td class="aft">${esc(m.after)}</td></tr>`).join('');
  return `${lHead(`${esc(sub.name)} · 达成 ${gotOf(sub)}/${TOTAL_PTS} 点 · 已完成`, srcTag(SRC.feedback))}${tabs}
    ${lBody(`<table class="mt"><thead><tr><th>训练目标</th><th>训练前</th><th>训练后</th></tr></thead><tbody>${rows}</tbody></table>
    <details class="chsum"><summary>章节与小节汇总（3 项）</summary>
      ${CHAP_SUM.map(c => `<div class="csrow">${esc(c)}</div>`).join('')}</details>
    <div class="nextr"><span>下一轮补练：已生成草稿</span>
      <button class="btn small" type="button" data-t="将打开 下一轮补练草稿（个人训练卷 · 第 4 周）">打开下一轮草稿</button></div>
    <details class="chsum"><summary>更正</summary>
      <button class="btn danger small" type="button" data-act="withdraw">撤回本次证据</button></details>`)}`;
}

function renderLedger() {
  const sel = S.sel;
  if (sel.type === 'anom') {
    const a = W.anoms.find(x => x.id === sel.id);
    return a ? anomLedger(a) : '';
  }
  const sub = W.subs.find(s => s.id === sel.id);
  if (!sub) return '';
  switch (sub.status) {
    case 'missing':
      return `${lHead(`缺少第 ${sub.missPage} 页`, srcTag('submission.expected_pages vs 已归组页'))}
        ${lBody(`<div class="lmsg">补传扫描件，或在异常页中匹配到这一份</div>
        <div class="lact"><button class="btn ghost small" type="button" data-act="cancelSub">取消这份提交</button></div>`)}`;
    case 'pending':
      return `${lHead(`整卷 ${QS.length} 题 · ${TOTAL_PTS} 个判定点`, srcTag(SRC.judge))}
        ${lBody(`<div class="lmsg">将发送本份训练答卷、题目和判定点，调用模型判定</div>
        <div class="lact"><button class="btn primary small" type="button" data-act="judgeOne">判定这一份（1 次模型请求）</button></div>`)}`;
    case 'judging':
      return `${lHead('判定中', srcTag(SRC.judge))}
        ${lBody(`<div class="skel"></div><div class="skel"></div><div class="skel w60"></div>
        <div class="lmsg">后台正在判定，页面会自动查询结果</div>`)}`;
    case 'failed':
      return `${lHead('判定失败', srcTag(SRC.judge))}
        ${lBody(`<div class="lmsg">模型返回无法解析，已有结果保持不变</div>
        <div class="lact"><button class="btn small" type="button" data-act="retryOne">教师确认后重试一次（1 次请求）</button></div>`)}`;
    case 'review': case 'update':
      return renderReview(sub);
    case 'done':
      return doneLedger(sub);
    case 'canceled':
      return `${lHead('已取消')}${lBody('<div class="lmsg">这一份没有计入本次回收</div>')}`;
  }
  return '';
}
