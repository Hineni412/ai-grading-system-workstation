/* Review papers from paired question sets, using native easy-to-hard presentation. */
const fs = require('node:fs');
const path = require('node:path');
const {spawnSync} = require('node:child_process');
const {chromium} = require('../frontend/node_modules/@playwright/test');
const root = path.resolve(__dirname, '..');
const args = process.argv.slice(2);
let sampleLabel = '较低-01', individualOnly = false, afterOnly = false;
let reportDirectory = path.join(root, 'output/test_training_endpoint_comparison_20261003');
let outputDirectory = null, baseUrl = 'http://127.0.0.1:12029';
for (let index = 0; index < args.length; index++) {
  if (args[index] === '--individual-only') individualOnly = true;
  else if (args[index] === '--after-only') {afterOnly = true; individualOnly = true;}
  else if (args[index] === '--student' && args[index + 1] && !args[index + 1].startsWith('--')) sampleLabel = args[++index];
  else if (args[index] === '--report-directory' && args[index + 1]) reportDirectory = path.resolve(root, args[++index]);
  else if (args[index] === '--output-directory' && args[index + 1]) outputDirectory = path.resolve(root, args[++index]);
  else if (args[index] === '--base-url' && args[index + 1]) baseUrl = args[++index];
  else throw new Error('Use --student <anonymous label> and optionally --individual-only');
}
if (!/^http:\/\/127\.0\.0\.1:\d+$/.test(baseUrl)) throw new Error('Only the loopback report service is supported');
if (outputDirectory && !outputDirectory.startsWith(path.join(root, 'output/pdf') + path.sep)) throw new Error('PDF output must stay within output/pdf');
const report = JSON.parse(fs.readFileSync(path.join(reportDirectory, 'comparison.json'), 'utf8'));
const scope = report.scopes.find(scope => scope.chapters.join('+') === '1+2');
const student = scope.papers.find(paper => paper.label === sampleLabel);
if (!student) throw new Error('The selected paired sample is unavailable');
const groups = individualOnly ? [] : scope.groups.map(groups => groups.find(group => group.members.includes(student.label)));
if (groups.some(group => !group)) throw new Error('The selected paired group sample is unavailable');
const level = ['low', 'lower_middle', 'upper_middle', 'high', 'unknown'][student.tier];
const cohort = ['低分', '中下', '中上', '高分', '成绩资料不足'][student.tier];
const oldPersonal = student.before.items.map(item => item.id), newPersonal = student.after.items.map(item => item.id);
const suffix = sampleLabel.endsWith('-01') ? '' : '_' + sampleLabel.split('-').at(-1);
const out = outputDirectory || path.join(root, `output/pdf/training_endpoint_${level}_sample_20261003${suffix}`);
let specs = [
  {name:`初版_${cohort}学生个人卷.pdf`, version:'初版', kind:'个人卷', ids:oldPersonal, other:newPersonal},
  {name:`终版_${cohort}学生个人卷.pdf`, version:'终版', kind:'个人卷', ids:newPersonal, other:oldPersonal},
];
if (afterOnly) specs = [{...specs[1],name:`完善后_${cohort}学生个人卷.pdf`,version:'完善后'}];
if (!individualOnly) specs.push(
  {name:`初版_${cohort}学生所在小组卷.pdf`, version:'初版', kind:'小组卷', ids:groups[0].question_ids, other:groups[1].question_ids, group:groups[0]},
  {name:`终版_${cohort}学生所在小组卷.pdf`, version:'终版', kind:'小组卷', ids:groups[1].question_ids, other:groups[0].question_ids, group:groups[1]},
);
const ids = [...new Set(specs.flatMap(spec => spec.ids))];
const metadataRead = spawnSync(path.join(root, 'runtime/python/python.exe'), ['-X','utf8','-B','-c',
  `import json,sys\nfrom pathlib import Path\nfrom tools.experiment_training_fit import readonly_runtime\nfrom question_bank.recommendation.personalized import PersonalizedRecommendationModule\nr=Path.cwd()/'user_data'\nwith readonly_runtime():\n s=PersonalizedRecommendationModule(db_path=r/'databases/question_bank.db',data_root=r)\n q,_,_=s._source_snapshot(question_ids=json.loads(sys.stdin.read()))\n print(json.dumps({str(x['question_id']):{'number':x['question_number'],'type':x['question_type'],'difficulty':x['difficulty'],'skills':[k for k in x['stable_keys'] if k.startswith('sk_')]} for x in q},ensure_ascii=False))`],
  {cwd:root,input:JSON.stringify(ids),encoding:'utf8'});
if (metadataRead.status !== 0) throw new Error('Read-only question metadata is unavailable');
const metadata = JSON.parse(metadataRead.stdout);
if (ids.some(id => !metadata[id])) throw new Error('A paired question is missing from the current bank');
const frozenDifficulties = new Map(scope.papers.flatMap(paper => ['before','after'].flatMap(version =>
  paper[version].items.map(item => [item.id,item.difficulty]))));
for (const id of ids) if (frozenDifficulties.has(id) && frozenDifficulties.get(id)!==metadata[id].difficulty) {
  throw new Error('Current question difficulty differs from the paired experiment');
}
for (const spec of specs) {
  spec.selectionIds = [...spec.ids];
  // Native _order_practice_items uses stable numeric ascending order, independent of type.
  spec.ids = [...spec.ids].sort((left,right) => metadata[left].difficulty - metadata[right].difficulty);
  const seen = new Set();
  for (const id of spec.ids) for (const skill of metadata[id].skills) {
    if (seen.has(skill)) throw new Error('The sample exceeds its one-question-per-standard-skill limit');
    seen.add(skill);
  }
}
const escape = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;'}[c]));
const css = `
@page{size:A4;margin:16mm 16mm 18mm}
*{box-sizing:border-box}body{margin:0;color:#172536;background:#fff;font:11.5pt/1.65 "Microsoft YaHei","SimSun",sans-serif}
header{padding-bottom:5mm;border-bottom:1.2px solid #243a51;margin-bottom:6mm}h1{font-size:20pt;line-height:1.4;margin:0 0 3mm;font-weight:650}.subtitle{font-size:10pt;color:#485666}.notes{font-size:9pt;line-height:1.7;color:#485666;margin:2mm 0 0}
.question{break-inside:avoid;margin:0 0 4mm;padding:0 0 2.5mm;border-bottom:0.5px solid #ccd3db}.question-head{display:flex;justify-content:space-between;gap:5mm;font-size:10pt;color:#485666;line-height:1.5;margin-bottom:2mm}.question-head strong{font-size:12pt;color:#172536}.badge{font-size:9pt;color:#3d4f62}.question-body{min-width:0;overflow-wrap:anywhere}.question-kind{display:none}.stem-block{white-space:pre-wrap;margin:1.6mm 0}.qm{white-space:nowrap}.qm--display{display:block;text-align:center;margin:2mm 0}.katex{font-size:1.05em}.question-body img{display:inline-block;width:auto;height:auto;max-width:125mm;max-height:45mm;object-fit:contain;vertical-align:middle;margin:2mm 4mm 2mm 0}.question-body table{border-collapse:collapse;font-size:11pt;max-width:100%;white-space:normal}.question-body td{border:0.5px solid #aab7c6;padding:2mm 3mm}.answer-space{height:14mm}.content-error{color:#a22}
`;
function html(spec) {
  const memberLine = spec.group ? `${spec.group.members.length} 人；${Object.entries(spec.group.tier_counts).map(([tier,n]) => `${tier} ${n} 人`).join('，')}` : `${cohort}层样本：${student.label}`;
  const change = spec.version === '初版' ? '移除' : '新增';
  const audited = spec.version === '初版' ? student.before.items : student.after.items;
  const purposes = new Map(audited.map(item => [item.id, item.targets?.length ? '补弱关联' : item.original_purposes?.length && item.original_purposes.every(purpose => purpose==='new') ? '未测目标新练习' : '其他练习']));
  const remediationCount = audited.filter(item => item.targets?.length).length;
  const newCount = audited.filter(item => item.original_purposes?.length && item.original_purposes.every(purpose => purpose==='new')).length;
  return `<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><title>${escape(spec.version+spec.kind)}</title><link rel="stylesheet" href="/vendor/katex/katex.min.css"><style>${css}</style></head><body>
  <header><h1>第一、二章训练卷 · ${spec.version}${spec.kind}</h1><div class="subtitle">${escape(memberLine)} · ${spec.ids.length} 题</div>
  <p class="notes">范围：勾股定理与实数。沿用实验选题，按正式组卷规则从易到难排序，同难度保持选题先后。原卷分值仅在本 PDF 中隐藏。</p>
  ${spec.group?'':`<p class="notes">本卷 ${remediationCount} 道补弱关联题、${newCount} 道未测目标新练习${spec.ids.length-remediationCount-newCount?`、${spec.ids.length-remediationCount-newCount} 道其他练习`:''}。未测目标暂无有效直接作答证据，不认定为薄弱，不计入失分需要覆盖。</p>`}
  <p class="notes">标记“${change}”的题，是相对于另一版本${spec.kind}发生变化的题；不表示一对一换题。${spec.group?'该组是样本学生实际所属的小组，包含其他水平的学生。':''}</p></header>
  ${spec.ids.map((id,index) => `<section class="question" data-question-id="${id}"><div class="question-head"><strong>第 ${index+1} 题 · ${escape(metadata[id].type)}</strong><span class="badge">${spec.other.includes(id)?'保留':change} · ${escape(purposes.get(id) || '')} · 难度 ${metadata[id].difficulty} · 题库 #${id}</span></div><div class="question-body" data-content-id="${id}"></div>${metadata[id].type==='解答题'?'<div class="answer-space"></div>':''}</section>`).join('')}
  </body></html>`;
}

(async () => {
  fs.mkdirSync(out, {recursive:true});
  const browser = await chromium.launch({headless:true});
  const checks = [];
  try {
    const page = await browser.newPage();
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.goto(baseUrl + '/training-comparison.html');
    await page.waitForFunction(() => window.ReportQuestionContent && window.katex);
    for (const spec of specs) {
      await page.setContent(html(spec));
      await page.evaluate(() => window.ReportQuestionContent.load(document));
      await page.waitForFunction(() => [...document.querySelectorAll('[data-content-id]')].every(node => node.dataset.contentState === 'loaded'));
      const hiddenScores = await page.evaluate(meta => {
        function removeStart(node, count) {
          const walker = document.createTreeWalker(node, NodeFilter.SHOW_TEXT);
          let text;
          while (count && (text = walker.nextNode())) {
            const consumed = Math.min(count, text.textContent.length);
            text.textContent = text.textContent.slice(consumed);
            count -= consumed;
          }
        }
        let hidden = 0;
        for (const node of document.querySelectorAll('[data-content-id]')) {
          node.querySelector('.question-kind')?.remove();
          const number = String(meta[node.dataset.contentId].number).replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
          const prefix = node.textContent.match(new RegExp('^\\s*' + number + '\\s*[.．、]\\s*'));
          if (prefix) removeStart(node, prefix[0].length);
          const score = node.textContent.match(/^\s*[（(]\s*\d+(?:\.\d+)?\s*分\s*[）)]\s*/);
          if (score) {removeStart(node, score[0].length); hidden++;}
          // Keep adjacent option labels separate in the printed presentation.
          if (meta[node.dataset.contentId].type === '选择题') {
            for (const block of node.querySelectorAll('.stem-block')) {
              if (!/^\s*A[.．、]/.test(block.textContent)) continue;
              const walker = document.createTreeWalker(block, NodeFilter.SHOW_TEXT);
              let text;
              while ((text = walker.nextNode())) {
                if (!text.parentElement.closest('.qm,.katex'))
                  text.textContent = text.textContent.replace(/([BCD][.．、])/g, '\u00a0\u00a0$1');
              }
            }
          }
          node.querySelectorAll('img').forEach(image => image.loading='eager');
        }
        return hidden;
      }, metadata);
      await page.waitForFunction(() => [...document.images].every(image => image.complete && image.naturalWidth > 0));
      await page.evaluate(() => document.fonts.ready);
      const check = await page.evaluate(() => ({
        ids:[...document.querySelectorAll('.question')].map(node => Number(node.dataset.questionId)),
        images:document.images.length,
        formulas:document.querySelectorAll('.qm[data-latex]').length,
        rendered:document.querySelectorAll('.qm .katex').length,
        sourceScorePrefixes:[...document.querySelectorAll('.question-body')].filter(node => /^\s*[（(]\s*\d+(?:\.\d+)?\s*分/.test(node.textContent)).length,
        errors:document.querySelectorAll('.content-error').length,
      }));
      if (JSON.stringify(check.ids)!==JSON.stringify(spec.ids) || check.errors || check.sourceScorePrefixes || check.formulas!==check.rendered || errors.length) throw new Error('Paper content validation failed');
      await page.pdf({path:path.join(out,spec.name),format:'A4',printBackground:true,preferCSSPageSize:true,
        displayHeaderFooter:true,headerTemplate:'<span></span>',footerTemplate:`<div style="font-size:8px;width:100%;text-align:center;color:#64748b;font-family:Arial">${escape(spec.version+spec.kind)} · <span class="pageNumber"></span> / <span class="totalPages"></span></div>`});
      checks.push({file:spec.name,version:spec.version,kind:spec.kind,...check,hiddenScores,
        selection_ids:spec.selectionIds,difficulties:spec.ids.map(id => metadata[id].difficulty),repeated_standard_skills:[],
        changed:spec.ids.filter(id => !spec.other.includes(id)),groupMembers:spec.group?.members.length || null});
    }
    const manifest = {student:student.label,scope:scope.chapters,versions:report.versions,
      source:'paired experiment question sets; native stable easy-to-hard order; current read-only bank content',model_requests:0,database_writes:0,
      personalCoverage:{before:student.before.covered.length,after:student.after.covered.length,needs:student.current_need_count},papers:checks};
    const json = JSON.stringify(manifest,null,2);
    if (Buffer.byteLength(json)>15000) throw new Error('Manifest write too large');
    fs.writeFileSync(path.join(out,'sample-manifest.json'),json);
    console.log(JSON.stringify({directory:out,papers:checks}));
  } finally {await browser.close();}
})().catch(error => {console.error(error.message);process.exitCode=1;});
