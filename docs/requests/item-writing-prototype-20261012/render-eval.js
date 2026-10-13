/* 命题训练原型 - 评价屏：自评、AI 评价模拟、结果（雷达/对照/维度卡/来源原题/版本/转入题库） */
(function () {
  'use strict';
  var STEP_NAMES = ['题目分析', '独立解题', '量表评分'];

  function dimScore(d, lvl) { return d.weight * lvl / 4; }
  function band(d) { return d < 4 ? '基础' : d < 7 ? '中档' : '提高'; }
  function aiTotal() {
    return Math.round(window.ITEM_RUBRIC.reduce(function (a, d) {
      return a + dimScore(d, window.ITEM_SAMPLE_REVIEW.dims[d.key].level); }, 0));
  }

  function selfHTML() {
    var IW = window.IW, esc = IW.esc, w = IW.state.work;
    var dims = window.ITEM_RUBRIC.map(function (d) {
      var lv = w.selfEval[d.key];
      var btns = d.levels.map(function (t, i) {
        return '<button type="button" class="lvlbtn' + (lv === i + 1 ? ' on' : '') +
          '" data-dim="' + d.key + '" data-lv="' + (i + 1) + '"><b>水平 ' + (i + 1) + '</b>' +
          esc(t) + '</button>';
      }).join('');
      return '<div class="dim"><div class="hd">' + esc(d.name) +
        '<span class="w">权重 ' + d.weight + '</span></div>' +
        '<div class="lvl-row">' + btns + '</div></div>';
    }).join('');
    return '<div class="card"><header><h2>自评（可选）</h2>' +
      '<span class="hint">先自己选水平，再看 AI 评价</span></header>' +
      '<div class="body iw-self">' + dims +
      '<div><button class="btn" id="iw-self-sample">载入示例自评</button></div></div></div>';
  }

  function radarSVG(dims, aiLv, selfLv) {
    var cx = 165, cy = 150, R = 105, n = dims.length;
    var pt = function (i, r) {
      var a = -Math.PI / 2 + i * 2 * Math.PI / n;
      return (cx + r * Math.cos(a)).toFixed(1) + ',' + (cy + r * Math.sin(a)).toFixed(1);
    };
    var s = '';
    for (var g = 1; g <= 4; g++) {
      var rr = R * g / 4, ring = [];
      for (var i = 0; i < n; i++) ring.push(pt(i, rr));
      s += '<polygon points="' + ring.join(' ') + '" fill="none" stroke="#dfe4e1"/>';
    }
    for (i = 0; i < n; i++) {
      var a = -Math.PI / 2 + i * 2 * Math.PI / n;
      s += '<line x1="' + cx + '" y1="' + cy + '" x2="' + (cx + R * Math.cos(a)).toFixed(1) +
        '" y2="' + (cy + R * Math.sin(a)).toFixed(1) + '" stroke="#dfe4e1"/>';
      var lr = R + 16, lx = cx + lr * Math.cos(a), ly = cy + lr * Math.sin(a);
      var anchor = Math.abs(Math.cos(a)) < 0.3 ? 'middle' : Math.cos(a) > 0 ? 'start' : 'end';
      s += '<text x="' + lx.toFixed(1) + '" y="' + (ly + 3).toFixed(1) + '" text-anchor="' + anchor +
        '" font-size="10" fill="#59656b">' + dims[i].name + '</text>';
    }
    var ai = [], sf = [];
    for (i = 0; i < n; i++) {
      ai.push(pt(i, R * (aiLv[dims[i].key] || 0) / 4));
      sf.push(pt(i, R * (selfLv[dims[i].key] || 0) / 4));
    }
    s += '<polygon points="' + ai.join(' ') + '" fill="rgba(23,107,117,.25)" stroke="#176b75" stroke-width="1.6"/>';
    if (Object.keys(selfLv).length) {
      s += '<polygon points="' + sf.join(' ') + '" fill="none" stroke="#d9822b" stroke-width="1.6" stroke-dasharray="5,3"/>';
    }
    return '<svg viewBox="0 0 330 300" width="100%" style="max-width:420px" role="img" aria-label="九维雷达图">' + s + '</svg>';
  }

  function resultsHTML() {
    var IW = window.IW, esc = IW.esc, w = IW.state.work;
    var R = window.ITEM_SAMPLE_REVIEW, dims = window.ITEM_RUBRIC;
    var total = aiTotal();

    var solving = '<table class="items"><thead><tr><th>小问</th><th>模型解答</th><th>与你的答案</th></tr></thead><tbody>' +
      R.solving.map(function (s, i) {
        var bad = s.verdict !== '一致';
        var mine = w.parts[i] ? w.parts[i].answer : '';
        return '<tr class="' + (bad ? 'mismatch' : '') + '"><td>' + esc(s.part) + '</td><td>' + esc(s.model) +
          '</td><td><div>' + esc(mine) + '</div><span class="verd ' + (bad ? 'bad' : 'ok') + '">' +
          esc(s.verdict) + '</span></td></tr>';
      }).join('') + '</tbody></table>';

    var a = R.analysis;
    var cmp = [
      ['知识点', w.sections.join('、') || '未填', a.knowledge, w.sections.indexOf(a.knowledge) >= 0],
      ['情境类别', w.contextKind || '未填', a.context, w.contextKind === a.context],
      ['预估难度', String(w.difficulty) + '（' + band(w.difficulty) + '）',
        a.difficulty.join(' / ') + '（' + band(a.difficulty.reduce(function (x, y) { return x + y; }, 0) / a.difficulty.length) + '）',
        band(w.difficulty) === band(a.difficulty.reduce(function (x, y) { return x + y; }, 0) / a.difficulty.length)],
      ['各问 SOLO', w.parts.map(function (p) { return p.solo || '—'; }).join('、'), a.solo.join('、'),
        w.parts.every(function (p, i) { return p.solo === a.solo[i]; })],
    ];
    var cmpHTML = '<table class="items"><thead><tr><th>项目</th><th>我的声明</th><th>题目分析</th><th>判定</th></tr></thead><tbody>' +
      cmp.map(function (r) {
        return '<tr class="' + (r[3] ? '' : 'mismatch') + '"><td>' + esc(r[0]) + '</td><td>' + esc(r[1]) +
          '</td><td>' + esc(r[2]) + '</td><td><span class="verd ' + (r[3] ? 'ok' : 'bad') + '">' +
          (r[3] ? '一致' : '不一致') + '</span></td></tr>';
      }).join('') + '</tbody></table>';

    var cards = dims.map(function (d) {
      var rd = R.dims[d.key], mine = w.selfEval[d.key], gap = '';
      if (mine != null && Math.abs(mine - rd.level) >= 2) {
        gap = ' <span class="gap">相差 ' + Math.abs(mine - rd.level) + ' 级</span>';
      }
      return '<div class="iw-dim"><div class="hd">' + esc(d.name) +
        '<span class="sc">' + Math.round(dimScore(d, rd.level) * 10) / 10 + ' / ' + d.weight + '</span></div>' +
        '<div>AI 水平 ' + rd.level + ' / 4 <span class="lvl-d">' + esc(d.levels[rd.level - 1]) + '</span></div>' +
        (mine != null ? '<div class="self-l">自评 水平 ' + mine + gap + '</div>' : '') +
        '<div class="iw-quote">' + esc(rd.quote) + '</div>' +
        '<div class="adv">' + esc(rd.advice) + '</div>' +
        '<div><button class="btn" data-fix="' + d.step + '">去修改</button></div></div>';
    }).join('');

    var src = IW.material(w.material) || {};
    var srcText = src.source_text ? IW.lib.stripHtml(src.source_text) : '合成材料，无来源原题';
    var srcBlock = '<div class="src-block"><div class="sh">来源原题</div>' +
      '<div class="sb">' + esc(srcText.slice(0, 300)) + (srcText.length > 300 ? '…' : '') + '</div>' +
      '<div class="sn">创新性维度会比较作品与来源原题的相似度（原型不计算）。</div></div>';

    var rev = IW.lib.review(w.material);
    var transfer = '';
    if (rev && !rev.transferred) {
      transfer = '<button class="btn primary" id="iw-transfer" style="margin-top:10px">转入题库</button>';
    } else if (rev && rev.transferred) {
      transfer = '<div class="hint" style="margin-top:10px">已转入题库（演示题号 ' + esc(rev.transferred) + '）</div>';
    }

    var vers = IW.lib.reviews(w.material).map(function (v) {
      return '<span class="iw-ver">第 ' + v.version + ' 版 · <b>' + v.total + '</b> 分' +
        (v.transferred ? ' · 已转入题库' : '') + '</span>';
    }).join('');
    if (!vers && w.sample) {
      vers = window.ITEM_SAMPLE_HISTORY.map(function (v) {
        return '<span class="iw-ver">第 ' + v.version + ' 版 · <b>' + v.total + '</b> 分' +
          '<div class="dt">' + esc(v.date) + ' ' + esc(v.note) + '</div></span>';
      }).join('');
    }
    vers += '<span class="hint">修改后再次评价生成新版本，可对比各维度变化（原型未实现对比）</span>';

    var aiPts = {};
    dims.forEach(function (d) { aiPts[d.key] = R.dims[d.key].level; });
    var label = w.sample ? '示例评价，非模型输出' : '演示结果，与本作品内容无关';

    return '<div class="card"><header><h2>评价结果</h2>' +
      '<span class="hint">' + esc(label) + '</span></header><div class="body">' +
      '<div class="iw-total"><span class="big">' + total + '</span><span class="sub">分（满分100；维度得分 = 权重 × 水平 ÷ 4）</span></div>' +
      '<div class="iw-flex" style="display:flex;gap:20px;flex-wrap:wrap;align-items:flex-start">' +
      '<div>' + radarSVG(dims, aiPts, w.selfEval) +
      '<div class="hint">实线：AI 水平' + (Object.keys(w.selfEval).length ? '；橙虚线：自评' : '') + '</div></div>' +
      '<div style="flex:1;min-width:280px"><h3 style="margin:0 0 6px;font-size:14px">独立解题</h3>' + solving +
      '<h3 style="margin:12px 0 6px;font-size:14px">题目分析与声明对照</h3>' + cmpHTML + '</div></div>' +
      srcBlock + transfer + '</div></div>' +
      '<h3 style="margin:4px 0 8px;font-size:15px">维度详情</h3><div class="iw-dims">' + cards + '</div>' +
      '<div class="card" style="margin-top:12px"><header><h2>版本</h2></header>' +
      '<div class="body iw-vers">' + vers + '</div></div>';
  }

  function progHTML() {
    var p = IW.state.evalProg;
    if (p) {
      return '<div class="eval-prog"><div class="tt" style="margin-bottom:6px">AI 评价进行中</div>' +
        STEP_NAMES.map(function (n, i) {
          var cls = i + 1 < p.step ? 'done' : i + 1 === p.step ? 'run' : '';
          return '<div class="ep ' + cls + '">' + (cls === 'done' ? '✓ ' : cls === 'run' ? '… ' : '· ') +
            '第 ' + (i + 1) + ' 步 ' + n + '</div>';
        }).join('') + '</div>';
    }
    if (IW.state.evalFail) {
      var f = IW.state.evalFail;
      return '<div class="eval-prog"><div class="ep fail">评价失败于第 ' + f.at + ' 步「' + f.name +
        '」。</div><div class="ep">已完成：' + f.done.join('、') + '。</div>' +
        '<div class="ep">失败或结果不确定时不自动重发；可到“调用记录”核对（原型无该页）。</div>' +
        '<button class="btn primary" id="iw-retry">重新评价</button></div>';
    }
    return '';
  }

  window.renderEval = function () {
    var IW = window.IW, w = IW.state.work;
    var back = '<div><button class="btn" id="iw-back">返回作答</button></div>';
    var aiBar = '<div class="card"><header><h2>AI 评价</h2>' +
      '<span class="hint">题目分析、独立解题、量表评分；每次调用前确认费用</span></header>' +
      '<div class="body"><button class="btn primary" id="iw-ai">AI 评价</button></div></div>';
    return back + selfHTML() + aiBar + progHTML() + (IW.state.reviewing ? resultsHTML() : '');
  };

  function confirmEval(onOk) {
    var IW = window.IW;
    IW.showDlg('<div>预计 ' + window.ITEM_SAMPLE_REVIEW.requests +
      ' 次模型请求：题目分析、独立解题、量表评分。确认后发送；失败或结果不确定时不自动重发。</div>' +
      '<div class="ops"><button class="btn" id="iw-ai-no">取消</button>' +
      '<button class="btn primary" id="iw-ai-ok">确认（原型显示示例结果）</button></div>');
    document.getElementById('iw-ai-no').addEventListener('click', IW.hideDlg);
    document.getElementById('iw-ai-ok').addEventListener('click', function () { IW.hideDlg(); onOk(); });
  }
  function runEval() {
    var IW = window.IW, w = IW.state.work;
    IW.state.evalFail = null; IW.state.evalProg = { step: 1 }; IW.render();
    var i = 0;
    (function next() {
      i++;
      if (IW.lib.sw('failStep2') && i === 2) {
        IW.state.evalProg = null;
        IW.state.evalFail = { at: 2, name: STEP_NAMES[1], done: ['题目分析'] };
        IW.render(); return;
      }
      if (i > STEP_NAMES.length) {
        IW.state.evalProg = null; IW.state.reviewing = true;
        var rev = IW.lib.review(w.material);
        if (!rev) {
          IW.lib.pushReview(w.material, { version: w.version || 1, total: aiTotal(), at: Date.now() });
        } else if (w.dirty) {
          w.version = (w.version || 1) + 1;
          IW.lib.pushReview(w.material, { version: w.version, total: aiTotal(), at: Date.now() });
          w.dirty = false;
        }
        IW.saveWork(); IW.render(); return;
      }
      IW.state.evalProg.step = i;
      IW.render();
      setTimeout(next, 1000);
    })();
  }

  window.bindEval = function () {
    var IW = window.IW, w = IW.state.work;
    document.getElementById('iw-back').addEventListener('click', function () {
      IW.state.screen = 'work'; IW.state.reviewing = false; IW.state.evalFail = null; IW.render();
    });
    document.querySelectorAll('.lvlbtn').forEach(function (b) {
      b.addEventListener('click', function () {
        w.selfEval[b.dataset.dim] = Number(b.dataset.lv);
        b.parentNode.querySelectorAll('.lvlbtn').forEach(function (x) { x.classList.remove('on'); });
        b.classList.add('on');
        IW.saveWork();
      });
    });
    var ss = document.getElementById('iw-self-sample');
    if (ss) ss.addEventListener('click', function () {
      w.selfEval = JSON.parse(JSON.stringify(window.ITEM_SAMPLE_SELF));
      IW.saveWork(); IW.render();
    });
    var ai = document.getElementById('iw-ai');
    if (ai) ai.addEventListener('click', function () { confirmEval(runEval); });
    var rt = document.getElementById('iw-retry');
    if (rt) rt.addEventListener('click', function () { confirmEval(runEval); });
    var tr = document.getElementById('iw-transfer');
    if (tr) tr.addEventListener('click', function () {
      IW.showDlg('<div>将写入题库试卷《命题训练作品》，之后会出现在题库与组卷中。</div>' +
        '<div class="ops"><button class="btn" id="iw-tr-no">取消</button>' +
        '<button class="btn primary" id="iw-tr-ok">确认转入</button></div>');
      document.getElementById('iw-tr-no').addEventListener('click', IW.hideDlg);
      document.getElementById('iw-tr-ok').addEventListener('click', function () {
        var tid = '#9' + String(100 + IW.lib.reviews(w.material).length);
        IW.lib.markTransferred(w.material, tid);
        IW.hideDlg(); IW.render();
      });
    });
    document.querySelectorAll('[data-fix]').forEach(function (b) {
      b.addEventListener('click', function () {
        IW.state.screen = 'work'; IW.state.step = Number(b.dataset.fix); IW.render();
      });
    });
  };
})();
