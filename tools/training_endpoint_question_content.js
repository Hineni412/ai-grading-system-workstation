/* Read question bodies only when visible; keep them in browser memory, never in the report. */
(() => {
  'use strict';
  const cache = new Map();
  const escape = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;'}[c]));
  const inline = segments => (segments || []).map(segment => {
    const text = escape(segment.text);
    const marked = segment.underline ? `<u>${text}</u>` : text;
    return (segment.line_break ? '<br>' : '') + (segment.superscript ? `<sup>${marked}</sup>` : segment.subscript ? `<sub>${marked}</sub>` : marked);
  }).join('');
  function blockHtml(block) {
    // HTML is the existing domain reader's controlled Word projection, not raw source HTML.
    if (block.html) return `<div class="stem-block">${block.html}</div>`;
    const content = block.kind === 'table'
      ? `<table>${(block.rows || []).map(row => `<tr>${row.cells.map(cell => `<td>${inline(cell.segments)}</td>`).join('')}</tr>`).join('')}</table>`
      : inline(block.segments) || escape(block.text);
    return `<div class="stem-block">${content}${(block.asset_urls || []).map(url => `<img src="${escape(url)}" alt="题目配图" loading="lazy">`).join('')}</div>`;
  }
  function typeset(root) {
    if (window.katex) root.querySelectorAll('.qm[data-latex]').forEach(node => {
      try {
        node.innerHTML = window.katex.renderToString(node.dataset.latex, {
          throwOnError: true, displayMode: node.classList.contains('qm--display'),
        });
      } catch { /* Keep the domain reader's linear fallback. */ }
    });
    if (window.renderMathInElement) window.renderMathInElement(root, {
      delimiters: [{left:'$$',right:'$$',display:true}, {left:'\\[',right:'\\]',display:true},
        {left:'$',right:'$',display:false}, {left:'\\(',right:'\\)',display:false}],
      ignoredClasses: ['qm'], throwOnError: false,
    });
    root.querySelectorAll('img').forEach(image => {
      image.addEventListener('error', () => {
        const note = document.createElement('span');
        note.className = 'content-error';
        note.textContent = '这张题目配图暂时无法读取。';
        image.replaceWith(note);
      }, {once:true});
    });
  }
  function read(id) {
    if (!cache.has(id)) cache.set(id, fetch(`/question-content/${id}`, {cache:'no-store'})
      .then(response => { if (!response.ok) throw new Error('read failed'); return response.json(); })
      .catch(error => { cache.delete(id); throw error; }));
    return cache.get(id);
  }
  function visible(node) {
    for (let parent = node.parentElement; parent; parent = parent.parentElement) {
      if (parent.hidden || (parent.tagName === 'DETAILS' && !parent.open)) return false;
    }
    return true;
  }
  function load(root = document) {
    root.querySelectorAll('[data-content-id]').forEach(node => {
      if (node.dataset.contentState || !visible(node)) return;
      node.dataset.contentState = 'loading';
      read(node.dataset.contentId).then(content => {
        if (!node.isConnected) return;
        node.innerHTML = `<div class="question-kind">${escape(content.question_type || '题目')}</div>`
          + (content.blocks.length ? content.blocks.map(blockHtml).join('') : `<div class="stem-block">${escape(content.text)}</div>`);
        node.dataset.contentState = 'loaded';
        typeset(node);
      }).catch(() => {
        if (!node.isConnected) return;
        node.dataset.contentState = 'error';
        node.innerHTML = '<span class="content-error">题干暂时无法读取。</span> <button type="button" class="content-retry">重试</button>';
      });
    });
  }
  document.addEventListener('toggle', event => { if (event.target.open) load(event.target); }, true);
  document.addEventListener('click', event => {
    const retry = event.target.closest('.content-retry');
    if (retry) { const node = retry.closest('[data-content-id]'); delete node.dataset.contentState; load(node.parentElement); return; }
    const image = event.target.closest('.question-body img');
    if (!image || !image.src) return;
    let viewer = document.getElementById('question-image-viewer');
    if (!viewer) {
      viewer = document.createElement('dialog');
      viewer.id = 'question-image-viewer';
      viewer.innerHTML = '<form method="dialog"><button>关闭配图</button></form><img alt="题目配图原图">';
      document.body.append(viewer);
      viewer.addEventListener('click', e => { if (e.target === viewer) viewer.close(); });
    }
    viewer.querySelector('img').src = image.src;
    viewer.showModal();
  });
  window.ReportQuestionContent = {load};
})();
