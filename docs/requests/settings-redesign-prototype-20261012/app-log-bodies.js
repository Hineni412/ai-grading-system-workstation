/* 设置页改版原型 · 调用记录的虚构正文生成 */
'use strict';
window.ProtoLogBodies = (function () {
  var D;
  var PURPOSE = { recognition: '答题卡', grading: '答卷', bank: '题目', report: '数据', legacy: '材料' };
  var PROMPTS = {
    '姓名识别': '识别答卷姓名栏的手写姓名，只返回 JSON：{"name":"","confidence":0-1}。',
    '客观题识别': '识别客观题填涂结果，只返回 JSON：{"1":"A","2":"B",…}，未填涂记为 null。',
    'AI 批改': '按给定评分标准批改这道解答题的作答照片，逐判定点给分，只返回 JSON。',
    '训练回收判定': '判定这份训练回收作答，按判定点返回达成/未达成，只返回 JSON。',
    '题库标注与判定点': '为下面的题目抽取判定点并标注知识点，只返回 JSON。',
    '知识点关联': '为下面的题目关联知识点标签与技能标签，只返回 JSON。',
    '章节题型整理': '把下面的题目按章节与题型整理，只返回 JSON。',
    '评分标准生成': '为这道解答题生成评分标准（判定点 + 分值 + 常见扣法），只返回 JSON。',
    'AI 补答案': '为下面的题目补写参考答案与关键步骤，只返回 JSON。',
    '错因整理': '汇总本场考试的常见错因并归类，只返回 JSON。',
    '班级报告': '依据本场考试数据生成班级学情报告正文。',
    '个人报告': '依据这名学生的作答数据生成个人学情报告正文。',
    'AI 组卷（旧记录）': '按给定蓝图组卷，只返回 JSON。',
    '班主任工作台（旧记录）': '生成班级周报摘要正文。',
  };
  function stu(i) { return D.classes[i % 2].students[(i * 7 + 3) % 40]; }

  function requestBody(c) {
    var userContent = [{ type: 'text', text: PROMPTS[c.label] || ('完成「' + c.label + '」任务，只返回 JSON。') }];
    for (var i = 1; i <= c.images; i++) userContent.push({ type: 'image_attachment', id: 'att_' + i });
    return JSON.stringify({
      model: c.model,
      protocol: c.protocol,
      messages: [
        { role: 'system', content: '你是阅卷系统的 AI 助手。输出严格符合要求的格式，不要寒暄。' },
        { role: 'user', content: userContent },
      ],
      temperature: 0.2,
      max_output_tokens: 4096,
      meta: { feature: c.label, exam: '第四周学情反馈', class: '9 班' },
    }, null, 2);
  }

  function attachments(c) {
    var list = [];
    for (var i = 1; i <= c.images; i++) {
      list.push({
        id: 'att_' + i,
        purpose: PURPOSE[c.kind] + '第 ' + i + ' 页',
        mime: 'image/jpeg',
        bytes: (380 + i * 47) + ' KB',
        sha: 'sha256:' + Array(16 + 1).join((i * 7 + 3).toString(16)) + '…',
      });
    }
    return list;
  }

  function responseText(c) {
    if (c.outcome === 'pending') return '（请求尚未返回）';
    if (c.error && c.error.cat === '网络') return '（未收到返回正文）';
    if (c.error && c.error.http === 502) return '502 Bad Gateway\n\nupstream connect error or disconnect/reset before headers';
    if (c.error && c.error.http === 429) return JSON.stringify({ error: { type: 'rate_limit_error', message: 'rate limit exceeded, retry after 20s' } }, null, 2);
    if (c.error && c.error.cat === '解析') return '{"results":[{"q":1,"answer":"A"},{"q":2,"answer":"B"},{"q":3,"answer":';
    var s = stu(2), s2 = stu(5);
    var bodies = {
      '姓名识别': { name: s.name, student_code: s.code, confidence: 0.97 },
      '客观题识别': { answers: { '1': 'A', '2': 'C', '3': 'B', '4': 'D', '5': 'A' }, uncertain: ['3'] },
      'AI 批改': { question: '12', score: 7, full: 10, points: [{ name: '建立方程', got: true }, { name: '求解正确', got: true }, { name: '单位与作答', got: false }] },
      '训练回收判定': { student: s2.name, points_hit: 4, points_total: 6, detail: [{ point: '配方变形', hit: true }, { point: '判别式', hit: false }] },
      '班级报告': { markdown: '## 第四周学情反馈 · 班级报告\n\n本次共 48 人参加，平均分 78.4。主要薄弱点集中在二次函数应用题…' },
      '个人报告': { markdown: '## ' + s.name + ' · 个人报告\n\n总分 82，班级前 25%。建议加强函数综合题…' },
    };
    var body = bodies[c.label] || { ok: true, task: c.label, items: 12, elapsed_ms: c.ms };
    return JSON.stringify(body, null, 2);
  }

  function parsedBody(c) {
    if (c.outcome === 'pending') return '（尚无解析结果）';
    if (c.outcome === 'failure') {
      return JSON.stringify({ ok: false, stage: 'response_parse', reason: c.error.cat + '：' + c.error.msg }, null, 2);
    }
    return JSON.stringify({
      ok: true,
      parse_status: 'parsed',
      validation: { issues: 0, repaired: c.attempt > 1 ? ['trim_trailing_comma'] : [] },
      summary: { feature: c.label, fields: 6, chars: c.resp },
    }, null, 2);
  }

  return {
    init: function (data) { D = data; },
    requestBody: requestBody,
    attachments: attachments,
    responseText: responseText,
    parsedBody: parsedBody,
  };
})();
