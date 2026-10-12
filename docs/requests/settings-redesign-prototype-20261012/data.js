/* 设置页改版原型 · 虚构演示数据（无任何真实学生数据） */
'use strict';
window.PROTO_DATA = (function () {
  function mulberry32(a) {
    return function () {
      a |= 0; a = (a + 0x6D2B79F5) | 0;
      var t = Math.imul(a ^ (a >>> 15), 1 | a);
      t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
  }
  var rnd = mulberry32(20261012);

  /* ---------- 学生名单（虚构） ---------- */
  var SURNAMES = '王李张刘陈杨赵黄周吴徐孙马朱胡郭何林罗郑梁谢宋唐许韩冯邓曹彭曾肖田董潘袁蔡蒋余杜'.split('');
  var GIVEN_A = '伟芳娜敏静秀强磊军洋勇艳杰涛明超霞平刚桂华玉萍红子雨欣浩宇梓诗思志国建文海晓佳乐安慕清望亦承景知若'.split('');
  var GIVEN_B = '华英杰平安然轩涵怡萱远栋斌燕梅峰琪宁白舒阳辰曦航舟墨初夏青岚桐楠澄溪昀晗昱晟铭诚'.split('');
  var usedNames = {};
  function makeName() {
    for (;;) {
      var s = SURNAMES[Math.floor(rnd() * SURNAMES.length)];
      var g = rnd() < 0.22
        ? GIVEN_A[Math.floor(rnd() * GIVEN_A.length)]
        : GIVEN_A[Math.floor(rnd() * GIVEN_A.length)] + GIVEN_B[Math.floor(rnd() * GIVEN_B.length)];
      var n = s + g;
      if (!usedNames[n]) { usedNames[n] = 1; return n; }
    }
  }
  function makeClass(cls, count, codePrefix) {
    var list = [];
    for (var i = 1; i <= count; i++) {
      list.push({ code: codePrefix + String(i).padStart(2, '0'), name: makeName(), cls: cls });
    }
    return list;
  }
  var classes = [
    { name: '9 班', students: makeClass('9 班', 48, '202509') },
    { name: '10 班', students: makeClass('10 班', 43, '202510') },
  ];

  /* ---------- AI 调用记录（虚构） ---------- */
  // kind: recognition 识别 / grading 批改与训练判定 / bank 题库资料与评分标准 / report 报告与补答案 / legacy 旧记录
  var calls = [
    { id: 'c01', day: '今天', date: '10-12', time: '14:03:21', kind: 'recognition', label: '姓名识别', outcome: 'success', model: 'antigravity/gemini-3.8-flash', ms: 12400, images: 3, attempt: 1, protocol: 'chat_completions', host: '127.0.0.1:3425', resp: 1842 },
    { id: 'c02', day: '今天', date: '10-12', time: '14:02:48', kind: 'recognition', label: '客观题识别', outcome: 'success', model: 'antigravity/gemini-3.8-flash', ms: 9800, images: 2, attempt: 1, protocol: 'responses', host: '127.0.0.1:3425', resp: 2311 },
    { id: 'c03', day: '今天', date: '10-12', time: '13:58:10', kind: 'grading', label: 'AI 批改', outcome: 'success', model: 'antigravity/gemini-3.8-flash', ms: 18200, images: 1, attempt: 2, protocol: 'chat_completions', host: '127.0.0.1:3425', resp: 3420, retry: { idx: 1, limit: 2, willRetry: false, delay: 4000 } },
    { id: 'c04', day: '今天', date: '10-12', time: '13:47:55', kind: 'grading', label: 'AI 批改', outcome: 'failure', model: 'antigravity/gemini-3.8-flash', ms: 120000, images: 1, attempt: 3, protocol: 'chat_completions', host: '127.0.0.1:3425', resp: 0, retry: { idx: 2, limit: 2, willRetry: false, delay: 8000 }, error: { cat: '网络', ex: 'ReadTimeout', http: 0, msg: '等待响应超过 120 秒，连接被服务端关闭。已按设置重试 2 次，仍未成功。' } },
    { id: 'c05', day: '今天', date: '10-12', time: '13:41:02', kind: 'bank', label: '题库标注与判定点', outcome: 'success', model: 'antigravity/gemini-3.8-pro', ms: 22600, images: 0, attempt: 1, protocol: 'chat_completions', host: '127.0.0.1:3425', resp: 5120 },
    { id: 'c06', day: '今天', date: '10-12', time: '13:30:14', kind: 'bank', label: '评分标准生成', outcome: 'success', model: 'antigravity/gemini-3.8-pro', ms: 15100, images: 0, attempt: 1, protocol: 'responses', host: '127.0.0.1:3425', resp: 2870 },
    { id: 'c07', day: '今天', date: '10-12', time: '11:26:40', kind: 'report', label: '班级报告', outcome: 'failure', model: 'antigravity/gemini-3.8-pro', ms: 3120, images: 0, attempt: 1, protocol: 'chat_completions', host: 'apic1.ohmycdn.com', resp: 212, retry: { idx: 0, limit: 2, willRetry: true, delay: 2000 }, error: { cat: '限流', ex: 'RateLimitError', http: 429, msg: '服务端返回 429：当前每分钟请求数已达上限，请稍后重试或降低并发。' } },
    { id: 'c08', day: '今天', date: '10-12', time: '11:25:31', kind: 'report', label: '班级报告', outcome: 'pending', model: 'antigravity/gemini-3.8-pro', ms: 0, images: 0, attempt: 1, protocol: 'chat_completions', host: 'apic1.ohmycdn.com', resp: 0 },
    { id: 'c09', day: '今天', date: '10-12', time: '09:58:12', kind: 'report', label: 'AI 补答案', outcome: 'success', model: 'antigravity/gemini-3.8-flash', ms: 8400, images: 0, attempt: 1, protocol: 'chat_completions', host: '127.0.0.1:3425', resp: 1644 },
    { id: 'c10', day: '今天', date: '10-12', time: '09:12:03', kind: 'grading', label: '训练回收判定', outcome: 'success', model: 'antigravity/gemini-3.8-flash', ms: 11000, images: 2, attempt: 1, protocol: 'responses', host: '127.0.0.1:3425', resp: 2034 },

    { id: 'c11', day: '昨天', date: '10-11', time: '19:20:44', kind: 'grading', label: 'AI 批改', outcome: 'success', model: 'antigravity/gemini-3.8-flash', ms: 13900, images: 2, attempt: 1, protocol: 'chat_completions', host: '127.0.0.1:3425', resp: 2988 },
    { id: 'c12', day: '昨天', date: '10-11', time: '18:55:30', kind: 'bank', label: '知识点关联', outcome: 'success', model: 'antigravity/gemini-3.8-pro', ms: 16700, images: 0, attempt: 1, protocol: 'chat_completions', host: '127.0.0.1:3425', resp: 4210 },
    { id: 'c13', day: '昨天', date: '10-11', time: '17:42:18', kind: 'recognition', label: '客观题识别', outcome: 'failure', model: 'antigravity/gemini-3.8-flash', ms: 7600, images: 2, attempt: 1, protocol: 'responses', host: '127.0.0.1:3425', resp: 1090, retry: { idx: 0, limit: 2, willRetry: true, delay: 2000 }, error: { cat: '解析', ex: 'JSONDecodeError', http: 200, msg: '返回正文不是合法 JSON：Expecting value: line 1 column 128 (char 127)。' } },
    { id: 'c14', day: '昨天', date: '10-11', time: '17:40:02', kind: 'recognition', label: '客观题识别', outcome: 'success', model: 'antigravity/gemini-3.8-flash', ms: 8100, images: 2, attempt: 2, protocol: 'responses', host: '127.0.0.1:3425', resp: 2452, retry: { idx: 1, limit: 2, willRetry: false, delay: 2000 } },
    { id: 'c15', day: '昨天', date: '10-11', time: '16:15:49', kind: 'report', label: '个人报告', outcome: 'success', model: 'antigravity/gemini-3.8-pro', ms: 19500, images: 0, attempt: 1, protocol: 'chat_completions', host: '127.0.0.1:3425', resp: 6620 },
    { id: 'c16', day: '昨天', date: '10-11', time: '15:03:37', kind: 'bank', label: '章节题型整理', outcome: 'success', model: 'antigravity/gemini-3.8-pro', ms: 12400, images: 0, attempt: 1, protocol: 'chat_completions', host: '127.0.0.1:3425', resp: 3180 },
    { id: 'c17', day: '昨天', date: '10-11', time: '11:48:26', kind: 'grading', label: 'AI 批改', outcome: 'failure', model: 'antigravity/gemini-3.8-flash', ms: 2340, images: 1, attempt: 3, protocol: 'chat_completions', host: 'apic1.ohmycdn.com', resp: 198, retry: { idx: 2, limit: 2, willRetry: false, delay: 4000 }, error: { cat: '限流', ex: 'RateLimitError', http: 429, msg: '服务端返回 429：触发每分钟请求上限。已重试 2 次仍被限流。' } },
    { id: 'c18', day: '昨天', date: '10-11', time: '10:02:11', kind: 'report', label: '错因整理', outcome: 'pending', model: 'antigravity/gemini-3.8-pro', ms: 0, images: 0, attempt: 1, protocol: 'chat_completions', host: '127.0.0.1:3425', resp: 0 },

    { id: 'c19', day: '10月10日', date: '10-10', time: '17:31:52', kind: 'grading', label: 'AI 批改', outcome: 'success', model: 'antigravity/gemini-3.8-flash', ms: 15600, images: 1, attempt: 2, protocol: 'chat_completions', host: '127.0.0.1:3425', resp: 3120, retry: { idx: 1, limit: 2, willRetry: false, delay: 4000 } },
    { id: 'c20', day: '10月10日', date: '10-10', time: '16:20:08', kind: 'bank', label: '题库标注与判定点', outcome: 'success', model: 'antigravity/gemini-3.8-pro', ms: 21100, images: 0, attempt: 1, protocol: 'chat_completions', host: '127.0.0.1:3425', resp: 4760 },
    { id: 'c21', day: '10月10日', date: '10-10', time: '15:44:39', kind: 'legacy', label: 'AI 组卷（旧记录）', outcome: 'success', model: 'gemini-2.5-flash', ms: 9800, images: 0, attempt: 1, protocol: 'chat_completions', host: 'apic1.ohmycdn.com', resp: 1890 },
    { id: 'c22', day: '10月10日', date: '10-10', time: '15:10:05', kind: 'legacy', label: '班主任工作台（旧记录）', outcome: 'failure', model: 'gemini-2.5-pro', ms: 4200, images: 0, attempt: 1, protocol: 'chat_completions', host: 'apic1.ohmycdn.com', resp: 0, retry: { idx: 0, limit: 0, willRetry: false, delay: 0 }, error: { cat: '服务', ex: 'HTTPError', http: 502, msg: '网关返回 502 Bad Gateway：上游服务暂时不可用。' } },
    { id: 'c23', day: '10月10日', date: '10-10', time: '14:02:27', kind: 'bank', label: '评分标准生成', outcome: 'success', model: 'antigravity/gemini-3.8-pro', ms: 14300, images: 0, attempt: 1, protocol: 'responses', host: '127.0.0.1:3425', resp: 2690 },
    { id: 'c24', day: '10月10日', date: '10-10', time: '09:35:16', kind: 'grading', label: '训练回收判定', outcome: 'success', model: 'antigravity/gemini-3.8-flash', ms: 10200, images: 2, attempt: 1, protocol: 'responses', host: '127.0.0.1:3425', resp: 1760 },
  ];

  var kindGroups = [
    { key: '', label: '全部' },
    { key: 'recognition', label: '识别' },
    { key: 'grading', label: '批改与训练判定' },
    { key: 'bank', label: '题库资料与评分标准' },
    { key: 'report', label: '报告与补答案' },
    { key: 'legacy', label: '旧记录' },
  ];

  /* ---------- AI 服务（虚构） ---------- */
  var accounts = [
    { name: 'local', host: '127.0.0.1:3425', key: true, dft: true, running: 3, queued: 0 },
    { name: 'ohmygpt', host: 'apic1.ohmycdn.com', key: true, dft: false, running: 0, queued: 0 },
  ];
  var tasks = [
    { key: 'grading', title: '批改试卷', features: ['姓名识别', '客观题识别', 'AI 批改'], account: 'local', model: 'antigravity/gemini-3.8-flash' },
    { key: 'bank', title: '题库资料、评分标准与报告', features: ['题库标注与判定点', '知识点关联', '章节题型整理', '评分标准生成', 'AI 补答案', '错因整理', '班级与个人报告', '训练回收批改'], account: 'local', model: 'antigravity/gemini-3.8-pro' },
  ];

  /* ---------- 数据与空间（虚构） ---------- */
  var space = {
    total: '4.8 GB',
    categories: [
      { label: '学生原卷', value: '483.1 MB', color: '#4d949d', pct: 8.7 },
      { label: '批注图', value: '103.3 MB', color: '#9db4b8', pct: 1.9 },
      { label: '题库资料', value: '2.0 GB', color: '#c9a86a', pct: 36.8 },
      { label: '报告与导出', value: '1.3 GB', color: '#8c9bb5', pct: 23.9 },
      { label: '备份', value: '225.9 MB', color: '#a98bb0', pct: 4.1 },
      { label: '数据库', value: '418.3 MB', color: '#b2b8ad', pct: 7.5 },
      { label: '其他', value: '955.2 MB', color: 'var(--color-border-strong)', pct: 17.1 },
    ],
    legacy: '旧批注图 103.3 MB · 已不再使用',
  };

  var exams = [
    { id: 1, name: '第一周学情反馈', date: '09-05', status: '已完成', statusTone: 'success', scan: 0, pages: '96.4 MB', orig: '已释放扫描文件', origTone: 'neutral', freed: 96.4, canRelease: false, canClear: true },
    { id: 2, name: '第二周学情反馈', date: '09-12', status: '已完成', statusTone: 'success', scan: '148.2 MB', pages: '92.1 MB', orig: '完整', origTone: 'success', freed: 240.3, canRelease: true, canClear: true },
    { id: 3, name: '第三周学情反馈', date: '09-19', status: '已完成', statusTone: 'success', scan: '165.7 MB', pages: '88.3 MB', orig: '完整', origTone: 'success', freed: 254.0, canRelease: true, canClear: true },
    { id: 4, name: '第四周学情反馈', date: '09-26', status: '已完成', statusTone: 'success', scan: '172.4 MB', pages: '90.6 MB', orig: '完整', origTone: 'success', freed: 263.0, canRelease: true, canClear: true },
    { id: 5, name: '第五周学情反馈', date: '10-03', status: '已完成', statusTone: 'success', scan: 0, pages: 0, orig: '已清除', origTone: 'neutral', freed: 0, canRelease: false, canClear: false },
  ];

  var backups = [
    { time: '10-11 19:20', reason: '手动备份', size: '45.2 MB' },
    { time: '10-10 08:30', reason: '更新前', size: '44.8 MB' },
    { time: '10-08 21:14', reason: '导入前', size: '44.1 MB' },
    { time: '10-05 19:02', reason: '手动备份', size: '43.6 MB' },
    { time: '10-02 07:55', reason: '更新前', size: '42.9 MB' },
    { time: '09-28 20:40', reason: '考试前', size: '41.7 MB' },
    { time: '09-21 19:36', reason: '手动备份', size: '40.2 MB' },
    { time: '09-14 18:51', reason: '恢复前', size: '39.8 MB' },
  ];

  return {
    classes: classes,
    calls: calls,
    kindGroups: kindGroups,
    accounts: accounts,
    tasks: tasks,
    space: space,
    exams: exams,
    backups: backups,
  };
})();
