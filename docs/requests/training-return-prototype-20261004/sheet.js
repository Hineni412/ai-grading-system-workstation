/* sheet.js — 中栏：答卷影像（内联 SVG 模拟 A4 扫描页） */

const STEM_SHORT = [
  'Rt△ABC 中 a=3、b=4，求斜边 c', '化简 √9 =', '点 P(3,-2) 关于 x 轴的对称点',
  'y=2x-1 与 y 轴的交点', '下列各数中的无理数', '补充全等条件：AB=DE，∠B=∠E',
  'y=-2x+3 的图象经过的象限', 'Rt△ABC：a=6，b=8，求斜边 c',
  '过 (1,-1)、(3,3) 求一次函数解析式', '证明 △ABC≌△DEF（已知 AB=DE、∠B=∠E、BC=EF）',
];
const ANS = [
  ['5'], ['3'], ['(3, 2)'], ['(0, -1)'], ['√2'], ['BC=EF'], ['一、二、四'],
  ['a²+b²=c²', '36+64=100', 'c=10'],
  ['y=kx+b', 'k=2，b=-3', '∴ y=2x-3'],
  ['AB=DE，∠B=∠E，BC=EF', '∴△ABC≌△DEF（SAS）', '证毕'],
];
const Q_PAGE = p => (p === 1 ? [0,1,2,3,4] : [5,6,7,8,9]); // 第 1 页 1-5 题，其后 6-10 题

// 一页 A4 扫描件（210:297 内联 SVG；手写体用倾斜脚本字体兜底）
// banner：异常页时在页框顶部、第一题之前加一条警示横条，不遮挡内容
function pageSVG(sub, pg, label, banner) {
  const head = `<text x="12" y="15" class="shead">${esc(label || `个人训练卷 · ${sub.name} · ${sub.ver} · 第 ${pg}/${sub.pages} 页`)}</text>
    <rect x="184" y="7" width="15" height="15" class="idcode"/>
    <text x="186.5" y="13" class="idc">页面</text><text x="186.5" y="19" class="idc">身份码</text>`
    + (banner ? `<rect x="6" y="24" width="198" height="13" class="aband"/>
      <text x="12" y="32.5" class="abandt">${esc(banner)}</text>` : '');
  let y = banner ? 46 : 30, body = '';
  if (pg > 2) { // 第三页：附页草稿
    body = `<rect x="14" y="${y}" width="182" height="240" class="abox"/>
      <text x="18" y="${y+8}" class="stm">附页 · 草稿</text>
      <text x="20" y="${y+30}" class="hw" transform="rotate(-2 20 ${y+30})">c² = a² + b² …</text>
      <text x="60" y="${y+70}" class="hw" transform="rotate(1 60 ${y+70})">y = 2x − 3</text>
      <text x="30" y="${y+120}" class="hw" transform="rotate(-1 30 ${y+120})">SAS ≌</text>`;
  } else {
    Q_PAGE(pg).forEach(q => {
      const written = QN[q] > 1, bh = written ? 34 : 12, sh = written ? 46 : 24;
      body += `<text x="14" y="${y}" class="qno">${q + 1}.</text>
        <text x="24" y="${y}" class="stm">${esc(STEM_SHORT[q])}</text>
        <rect x="14" y="${y + 4}" width="182" height="${bh}" class="abox"/>`;
      ANS[q].forEach((a, i) => {
        body += `<text x="${18 + i * 2}" y="${y + 11 + i * 10}" class="hw" transform="rotate(${i % 2 ? 1 : -1} 18 ${y + 11 + i * 10})">${esc(a)}</text>`;
      });
      y += sh;
    });
  }
  return `<svg viewBox="0 0 210 297" class="a4" xmlns="http://www.w3.org/2000/svg">
    <rect x="1" y="1" width="208" height="295" class="pagebox"/>${head}${body}</svg>`;
}

// 中栏：影像头（姓名/版本/页数 + 页签 + 放大查看）+ 当前页内容
function renderSheet() {
  const sel = S.sel;
  if (sel.type === 'anom') {
    const a = W.anoms.find(x => x.id === sel.id);
    if (!a) return '';
    return `<div class="sheetHead"><span class="shname">上传文件第 ${a.page} 页 · 异常页</span>
        <span class="shgap"></span>
        <button class="btn ghost small" type="button" data-t="将放大查看 该页扫描件">放大查看</button></div>
      <div class="sheetBody"><div class="scanwrap anom">
        ${pageSVG({ name:'', ver:'', pages:1 }, a.page % 2 ? 2 : 1, `上传文件第 ${a.page} 页`, `异常页 · ${a.issue}`)}
      </div></div>`;
  }
  const sub = W.subs.find(s => s.id === sel.id);
  if (!sub) return '';
  const tabs = Array.from({ length: sub.pages }, (_, i) =>
    `<button class="pgtab ${S.pg === i + 1 ? 'on' : ''}" type="button" data-act="pg" data-pg="${i + 1}">第${i + 1}页</button>`).join('');
  const body = sub.missPage === S.pg
    ? `<div class="scanwrap"><div class="misstile">缺第 ${sub.missPage} 页</div></div>`
    : `<div class="scanwrap">${pageSVG(sub, S.pg)}</div>`;
  return `<div class="sheetHead"><span class="shname">${esc(sub.name)} · ${sub.ver} · ${sub.pages} 页</span>
      <span class="pgtabs">${tabs}</span><span class="shgap"></span>
      <button class="btn ghost small" type="button" data-t="将放大查看 第 ${S.pg} 页">放大查看</button></div>
    <div class="sheetBody">${body}</div>`;
}
