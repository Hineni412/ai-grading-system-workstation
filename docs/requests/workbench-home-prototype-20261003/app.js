/* app.js — 壳、原型控制、提示 */
const S = { demo: 'done' };

let toastTimer = null;
function toast(msg) {
  const t = $('#toast');
  t.textContent = msg;
  t.classList.add('on');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.remove('on'), 2600);
}

// 页头问候语（按当前小时）
function greeting() {
  const h = new Date().getHours();
  if (h < 6) return '晚上好';
  if (h < 11) return '早上好';
  if (h < 13) return '中午好';
  if (h < 18) return '下午好';
  return '晚上好';
}
$('#hdrMeta').textContent = `${greeting()} · 10月3日 星期六 · 八年级上册`;

// 所有可点击元素：按钮/链接只弹出原型提示
document.addEventListener('click', e => {
  const b = e.target.closest('[data-t]');
  if (!b) return;
  if (b.tagName === 'A') e.preventDefault();
  toast('原型：' + b.dataset.t);
});

// 原型控制
$$('#protoCtl input[name=ctlstate]').forEach(r => r.addEventListener('change', e => {
  S.demo = e.target.value;
  renderAll();
}));
$('#ctlSrc').addEventListener('change', e => {
  document.body.classList.toggle('showsrc', e.target.checked);
});
$('#pcToggle').addEventListener('click', () => $('#protoCtl').classList.toggle('open'));

renderAll();
