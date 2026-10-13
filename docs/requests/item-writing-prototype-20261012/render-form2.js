/* 命题训练原型 - 作答表单 步骤 5–8（含模拟识别与说明草稿生成） */
(function () {
  'use strict';

  function band(d) { return d < 4 ? '基础' : d < 7 ? '中档' : '提高'; }
  function nav() {
    return '<div class="iw-nav"><button class="btn" id="iw-prev">上一步</button>' +
      '<button class="btn primary" id="iw-next">' + (window.IW.state.step < 8 ? '下一步' : '去评价') + '</button></div>';
  }
  function markedStem(w) {
    var esc = window.IW.esc, html = esc(w.stem || '');
    (w.ocr && w.ocr.uncertain || []).forEach(function (u) {
      html = html.split(esc(u)).join('<mark>' + esc(u) + '</mark>');
    });
    return html;
  }

  function step5() {
    var IW = window.IW, esc = IW.esc, w = IW.state.work;
    var ocr = window.ITEM_OCR_DEMO;
    var banner = '';
    if (w.ocr) {
      banner = '<div class="iw-banner">识别结果待核对：以下片段把握较低 ' +
        w.ocr.uncertain.map(function (u) { return '<span class="unc">' + esc(u) + '</span>'; }).join('、') +
        '。<br>原型模拟本机识别；识别不可用时请手动录入。</div>' +
        '<div class="iw-preview">' + markedStem(w) + '</div>';
    }
    var opts = '';
    if (w.questionType === '选择题') {
      var rows = ['A', 'B', 'C', 'D'].map(function (L, i) {
        return '<div class="row"><input type="radio" name="correctOption" data-b="correctOption" data-num ' +
          'value="' + i + '"' + (w.correctOption === i ? ' checked' : '') + '> ' + L + '．' +
          '<input type="text" data-o="' + i + '" data-b="options" value="' + esc(w.options[i]) + '"></div>';
      }).join('');
      opts = '<div class="iw-f"><label>选项 <span class="sub">（单选：点左侧圆点标正确项）</span></label>' +
        '<div class="iw-opts">' + rows + '</div></div>';
    }
    var img = IW.state.imagePreview
      ? '<img class="iw-thumb" src="' + IW.state.imagePreview + '" alt="配图预览">'
      : '<span class="sub">未配图；原型不保存图片</span>';
    return '<div class="iw-f"><label>题干 <span class="sub">（纯文本；正式版支持公式编辑）</span></label>' +
      '<textarea data-b="stem" id="iw-stem" style="min-height:110px">' + esc(w.stem) + '</textarea></div>' +
      banner + opts +
      '<div class="iw-f"><label>配图</label><input type="file" id="iw-img" accept="image/*">' + img + '</div>' +
      '<div class="iw-f"><button class="btn" id="iw-ocr" type="button">上传图片或文档识别</button>' +
      '<span class="sub">示例将模拟识别《' + esc(ocr.file) + '》</span></div>' + nav();
  }

  function step6() {
    var IW = window.IW, esc = IW.esc, w = IW.state.work;
    var parts = w.parts.map(function (p, i) {
      var rows = p.steps.map(function (s, j) {
        return '<div class="row"><input type="text" placeholder="步骤内容" data-ps="' + i + ':' + j +
          '" data-col="0" data-b="s" value="' + esc(s[0]) + '">' +
          '<input type="number" min="0" placeholder="分" data-ps="' + i + ':' + j +
          '" data-col="1" data-b="s" value="' + (s[1] == null ? '' : s[1]) + '">' +
          '<button class="btn" data-rm-step="' + i + ':' + j + '">删</button></div>';
      }).join('');
      var ss = p.steps.reduce(function (a, s) { return a + (Number(s[1]) || 0); }, 0);
      var ok = ss === Number(p.score);
      return '<div class="iw-part"><div class="hd">第（' + (i + 1) + '）问 · 该问 ' +
        (p.score == null ? '—' : p.score) + ' 分</div>' +
        '<div class="iw-f"><label>参考答案</label><textarea data-p="' + i + '" data-b="answer">' +
        esc(p.answer) + '</textarea></div>' +
        '<div class="iw-f"><label>评分步骤</label>' + rows +
        '<div><button class="btn" data-add-step="' + i + '">+ 加一步</button>' +
        '<span class="sum-line iw-ssum ' + (ok ? 'ok' : 'bad') + '" data-i="' + i +
        '" style="margin-left:12px">步骤合计 ' + ss + ' / 该问 ' +
        (p.score == null ? '—' : p.score) + ' 分</span></div></div></div>';
    }).join('');
    return parts +
      '<div class="iw-f"><label>其他解法</label><textarea data-b="otherSolutions">' +
      esc(w.otherSolutions) + '</textarea></div>' + nav();
  }

  function step7() {
    var IW = window.IW, esc = IW.esc, w = IW.state.work, O = IW.O;
    var errs = w.errors.map(function (e, i) {
      var cls = O.errorClasses.map(function (c) {
        return '<option' + (e[1] === c ? ' selected' : '') + '>' + esc(c) + '</option>';
      }).join('');
      return '<div class="iw-part"><div class="row">' +
        '<input type="text" placeholder="错法描述" data-e="' + i + '" data-col="0" data-b="e" value="' + esc(e[0]) + '">' +
        '<select data-e="' + i + '" data-col="1" data-b="e"><option value="">错误大类</option>' + cls + '</select>' +
        '<button class="btn" data-rm-err="' + i + '">删</button></div>' +
        '<div class="row"><input type="text" placeholder="成因" data-e="' + i +
        '" data-col="2" data-b="e" value="' + esc(e[2] || '') + '"></div></div>';
    }).join('');
    return '<div class="iw-f"><label>预估难度 <span class="iw-diff-val" id="iw-diff-val">' +
      w.difficulty + '</span><span class="dband ' +
      (w.difficulty < 4 ? 'basic' : w.difficulty < 7 ? 'mid' : 'hard') + '" id="iw-diff-band">' +
      band(w.difficulty) + '</span></label>' +
      '<input type="range" class="iw-diff" min="1" max="10" step="1" data-b="difficulty" value="' + w.difficulty + '"></div>' +
      '<div class="iw-f"><label>预估得分率 %</label>' +
      '<input type="number" min="0" max="100" data-b="predictedRate" value="' +
      (w.predictedRate == null ? '' : w.predictedRate) + '"></div>' +
      '<div class="iw-f"><label>典型错误预设</label>' + errs +
      '<div><button class="btn" id="iw-add-err">+ 加一条错误预设</button></div></div>' + nav();
  }

  function step8() {
    var IW = window.IW, esc = IW.esc, w = IW.state.work;
    var boxes = IW.O.talkPoints.map(function (t) {
      return '<label><input type="checkbox" data-b="talkChecked" value="' + esc(t) + '"' +
        (w.talkChecked.indexOf(t) >= 0 ? ' checked' : '') + '>' + esc(t) + '</label>';
    }).join(' ');
    return '<div class="iw-f"><button class="btn" id="iw-draft" type="button">根据前面各项生成说明草稿</button></div>' +
      '<div class="iw-f"><label>命题说明</label><textarea data-b="statement" style="min-height:130px">' +
      esc(w.statement) + '</textarea></div>' +
      '<div class="iw-f"><label>说题四要素检查</label><div class="iw-chips">' + boxes + '</div></div>' + nav();
  }

  window.renderForm58 = function () {
    return [step5, step6, step7, step8][window.IW.state.step - 5]();
  };

  function draft(w) {
    var O = window.IW.O;
    var qs = w.parts.map(function (p, i) {
      return '第（' + (i + 1) + '）问' + (p.ask ? p.ask.slice(0, 24) : '（设问未填）') +
        '（' + (p.solo || '未定SOLO') + '）';
    }).join('；');
    return '本题以' + (w.contextKind || '未填情境') + '为情境，材料运用方式：' + (w.materialUse || '未填') +
      '。考查意图：' + (w.intent || '未填') + '。核心素养：' + (w.competencies.join('、') || '未选') +
      '。共设 ' + w.parts.length + ' 问：' + qs + '。预估难度' + band(w.difficulty) +
      '（' + w.difficulty + '），预估得分率 ' + (w.predictedRate == null ? '未填' : w.predictedRate + '%') + '。';
  }

  window.bindForm58 = function () {
    var IW = window.IW, w = IW.state.work;
    var ocr = document.getElementById('iw-ocr');
    if (ocr) ocr.addEventListener('click', function () {
      if (IW.lib && IW.lib.sw('ocrOff')) {
        IW.showDlg('<div class="ops"><p>识别不可用，请手动录入。</p>' +
          '<button class="btn primary" id="iw-ocr-off-ok">知道了</button></div>');
        document.getElementById('iw-ocr-off-ok').addEventListener('click', IW.hideDlg);
        return;
      }
      IW.showDlg('正在本机识别…');
      setTimeout(function () {
        w.stem = window.ITEM_OCR_DEMO.text;
        w.ocr = { uncertain: window.ITEM_OCR_DEMO.uncertain.slice() };
        IW.saveWork(); IW.hideDlg(); IW.render();
      }, 1000);
    });
    var stem = document.getElementById('iw-stem');
    if (stem) stem.addEventListener('input', function () {
      var pv = document.querySelector('.iw-preview');
      if (pv) pv.innerHTML = markedStem(w);
    });
    var img = document.getElementById('iw-img');
    if (img) img.addEventListener('change', function () {
      var f = img.files && img.files[0];
      if (!f) return;
      var r = new FileReader();
      r.onload = function () { IW.state.imagePreview = r.result; IW.render(); };
      r.readAsDataURL(f);
    });
    var rng = document.querySelector('.iw-diff');
    if (rng) rng.addEventListener('input', function () {
      document.getElementById('iw-diff-val').textContent = rng.value;
      var b = document.getElementById('iw-diff-band');
      var d = Number(rng.value);
      b.textContent = band(d);
      b.className = 'dband ' + (d < 4 ? 'basic' : d < 7 ? 'mid' : 'hard');
    });
    document.querySelectorAll('[data-add-step]').forEach(function (b) {
      b.addEventListener('click', function () {
        w.parts[Number(b.dataset.addStep)].steps.push(['', null]);
        IW.saveWork(); IW.render();
      });
    });
    document.querySelectorAll('[data-rm-step]').forEach(function (b) {
      b.addEventListener('click', function () {
        var ps = b.dataset.rmStep.split(':').map(Number);
        w.parts[ps[0]].steps.splice(ps[1], 1);
        IW.saveWork(); IW.render();
      });
    });
    var ae = document.getElementById('iw-add-err');
    if (ae) ae.addEventListener('click', function () {
      w.errors.push(['', '', '']); IW.saveWork(); IW.render();
    });
    document.querySelectorAll('[data-rm-err]').forEach(function (b) {
      b.addEventListener('click', function () {
        w.errors.splice(Number(b.dataset.rmErr), 1); IW.saveWork(); IW.render();
      });
    });
    var dr = document.getElementById('iw-draft');
    if (dr) dr.addEventListener('click', function () {
      w.statement = draft(w); IW.saveWork(); IW.render();
    });
    document.getElementById('iw-prev').addEventListener('click', function () {
      IW.state.step--; IW.render();
    });
    document.getElementById('iw-next').addEventListener('click', function () {
      if (IW.state.step < 8) { IW.state.step++; IW.render(); }
      else { IW.state.screen = 'eval'; IW.render(); }
    });
  };
})();
