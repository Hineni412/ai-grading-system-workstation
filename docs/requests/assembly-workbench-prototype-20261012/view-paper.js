/* 卷面与题号轨道：整卷预览、整理与导出 A/B 共用渲染 */
(function () {
  const S = () => PROTO.S;
  const esc = s => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  const TT = { '选择题': 'tt--c', '填空题': 'tt--f', '解答题': 'tt--s' };
  const CN = ['一', '二', '三', '四', '五', '六'];

  function imgph(label) { return `<div class="imgph">【图】${esc(label)}</div>`; }

  function stemHTML(q, forSheet) {
    const opts = q.options ? `<div class="sheet__opts">${q.options.map(o => `<span>${esc(o)}</span>`).join('')}</div>` : '';
    return `<div class="sheet__stem">${esc(q.stem)}</div>${q.img ? imgph(q.img) : ''}${opts}`;
  }

  function answerHTML(e) {
    const { S } = PROTO;
    if (S.mode === 'student') return '';
    const q = e.q;
    if (S.ansMode === 'answer') {
      const open = S.expAns.has(q.id);
      return `<div class="sheet__answer"><div class="ans-line">答案：${esc(q.answer)} <button type="button" class="sheet__more" onclick="PROTO.toggleAns('${q.id}')">${open ? '收起解析' : '看解析'}</button></div>${open ? `<div class="ana">${esc(q.analysis)}</div>` : ''}</div>`;
    }
    const long = q.analysis.length > 110;
    const open = S.expAns.has(q.id);
    const cls = long && !open ? 'ana clamp clamp--3' : 'ana';
    return `<div class="sheet__answer"><div class="ans-line">答案：${esc(q.answer)}</div><div class="${cls}">${esc(q.analysis)}</div>${long ? `<button type="button" class="sheet__more" onclick="PROTO.toggleAns('${q.id}')">${open ? '收起' : '展开'}</button>` : ''}</div>`;
  }

  function metaLine(e) {
    const pt = PROTO.point(e.pointId);
    return `<p class="sheet__meta-line">${e.q.type} · 难度 ${e.q.diff.toFixed(1)}${e.source ? ' · ' + esc(e.source) : ''}${pt ? ' · ' + esc(pt.name) : ''}</p>`;
  }

  /* 分节：sequential=一组无标题；grouped_by_type=按题型；sections=两个手动节 */
  function sheetSections() {
    const items = PROTO.orderedQuestions();
    if (PROTO.S.layout === 'grouped_by_type') {
      const names = { '选择题': '选择题', '填空题': '填空题', '解答题': '解答题' };
      const order = ['选择题', '填空题', '解答题'];
      return order.filter(t => items.some(e => e.q.type === t))
        .map((t, i) => ({ title: CN[i] + '、' + names[t], items: items.filter(e => e.q.type === t) }));
    }
    if (PROTO.S.layout === 'sections') {
      const half = Math.ceil(items.length / 2);
      return [{ title: '一、' + (PROTO.S.sectionNames || ['基础巩固'])[0], items: items.slice(0, half) },
              { title: '二、' + (PROTO.S.sectionNames || ['', '综合提升'])[1], items: items.slice(half) }];
    }
    return [{ title: '', items }];
  }

  /* 卷面（可拖拽排序、教师版答案、题号轨道联动；opts.drawer 为抽屉紧凑版） */
  function sheetHTML(opts) {
    const S = PROTO.S;
    const editable = opts && opts.editable !== false;
    const drawer = !!(opts && opts.drawer);
    const metaOn = opts && opts.metaKey ? !!S[opts.metaKey] : S.showMeta;
    const swap = editable && opts && opts.swap;
    let n = 0;
    const secs = sheetSections().map(sec =>
      (sec.title ? `<h3 class="sheet__section">${esc(sec.title)}</h3>` : '') +
      sec.items.map(e => {
        n++;
        const tools = editable ? `<div class="sheet__tools"><button type="button" title="上移" onclick="PROTO.moveBy('${e.qid}',-1)">↑</button><button type="button" title="下移" onclick="PROTO.moveBy('${e.qid}',1)">↓</button>${swap ? `<button type="button" title="换一题" onclick="PROTO.toast('原型未实现换一题')">换一题</button>` : ''}<button type="button" title="移出试卷" onclick="PROTO.remove('${e.qid}')">移出</button></div>` : '';
        const handle = editable ? `<span class="sheet__handle drag-handle" aria-hidden="true">⋮⋮</span>` : '';
        return `<div class="sheet__q" data-qid="${e.qid}" id="sq-${e.qid}" ${editable ? 'draggable="true"' : ''}>
          <span class="sheet__num">${n}.</span>${handle}${tools}
          ${stemHTML(e.q)}
          ${metaOn ? metaLine(e) : ''}
          ${answerHTML(e)}
        </div>`;
      }).join('')
    ).join('');
    const head = drawer ? '' :
      `<header><h2 class="sheet__title">${esc(S.title || '未命名试卷')}</h2>
      ${S.subtitle ? `<p class="sheet__meta">${esc(S.subtitle)}</p>` : ''}
      <p class="sheet__meta">姓名：<b></b>　班级：<b></b>　日期：<b></b></p></header>`;
    return `<article class="sheet ${opts && opts.embed ? 'sheet--embed' : ''} ${drawer ? 'sheet--drawer' : ''}" data-dnd="sheet">
      ${head}${secs}
    </article>`;
  }

  function railHTML() {
    const items = PROTO.orderedQuestions();
    return `<nav class="rail" aria-label="题号导航" data-dnd="rail">${items.map((e, i) =>
      `<button type="button" class="rail__chip ${TT[e.q.type]}" data-qid="${e.qid}" draggable="true" title="第 ${i + 1} 题 · ${e.q.type}">${i + 1}</button>`
    ).join('')}</nav>`;
  }

  /* 渲染 轨道 + 卷面 到容器 */
  function renderSheetWrap(el, opts) {
    el.innerHTML = `<div class="sheet-wrap ${opts && opts.embed ? 'sheet-wrap--embed' : ''} ${opts && opts.drawer ? 'sheet-wrap--drawer' : ''}">${railHTML()}${sheetHTML(opts)}</div>`;
    PROTO.dnd(el.querySelector('[data-dnd="rail"]'), '.rail__chip');
    if (!opts || opts.editable !== false) PROTO.dnd(el.querySelector('[data-dnd="sheet"]'), '.sheet__q');
    el.querySelectorAll('.rail__chip').forEach(ch => ch.addEventListener('click', () => {
      const t = el.querySelector('#sq-' + ch.dataset.qid);
      if (t) t.scrollIntoView({ behavior: 'smooth', block: 'center' });
    }));
    watchInView(el);
  }

  /* 高亮当前视口内题目的题号：每个容器一个 observer，
     抽屉内以 .basket__scroll 为 root，互不影响 */
  function watchInView(el) {
    if (el.__io) el.__io.disconnect();
    const scroller = el.closest('.basket__scroll') || document.querySelector('.main__scroll');
    if (typeof IntersectionObserver === 'undefined' || !scroller) return;
    el.__io = new IntersectionObserver(entries => {
      entries.forEach(en => {
        const chip = el.querySelector('.rail__chip[data-qid="' + en.target.dataset.qid + '"]');
        if (chip && en.isIntersecting) {
          el.querySelectorAll('.rail__chip.is-current').forEach(c => c.classList.remove('is-current'));
          chip.classList.add('is-current');
        }
      });
    }, { root: scroller, threshold: 0.5 });
    el.querySelectorAll('.sheet__q').forEach(b => el.__io.observe(b));
  }

  /* 教师版控件（预览页、导出方案 A、试卷篮抽屉共用；opts.metaKey 换绑状态键） */
  function modeControlsHTML(opts) {
    const S = PROTO.S;
    const metaKey = (opts && opts.metaKey) || 'showMeta';
    const seg = (os, cur, act, label) =>
      `<div class="app-segmented app-segmented--small" role="group" aria-label="${label}">${os.map(([v, l]) =>
        `<button type="button" aria-pressed="${cur === v}" onclick="PROTO.${act}('${v}')">${l}</button>`).join('')}</div>`;
    let h = seg([['student', '学生版'], ['teacher', '教师版']], S.mode, 'setMode', '版本');
    if (S.mode === 'teacher') h += seg([['answer', '只看答案'], ['analysis', '答案+解析']], S.ansMode, 'setAnsMode', '解析展示');
    h += `<label class="meta-toggle"><input type="checkbox" ${S[metaKey] ? 'checked' : ''} onchange="PROTO.set({${metaKey}:this.checked})">显示题目信息</label>`;
    return h;
  }

  Object.assign(PROTO, { esc, stemHTML, renderSheetWrap, sheetHTML, railHTML, modeControlsHTML, sheetSections });
})();
