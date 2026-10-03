/* data.js — 合成数据（种子固定，不连接真实接口）
   成绩中心 · 个人报告：3 场考试 × 91 名学生 × 报告状态 */
const PR = (() => {
  let seed = 20261013;
  function rnd() {
    seed |= 0; seed = (seed + 0x6D2B79F5) | 0;
    let t = Math.imul(seed ^ (seed >>> 15), 1 | seed);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  }
  const ri = (a, b) => a + Math.floor(rnd() * (b - a + 1));
  const clamp = (v, a, b) => Math.min(b, Math.max(a, v));

  const EXAMS = [
    { id: 'e2', name: '第二周学情反馈', date: '9 月 6 日' },
    { id: 'e3', name: '第三周学情反馈', date: '9 月 13 日' },
    { id: 'e4', name: '第四周学情反馈', date: '9 月 20 日', current: true },
  ];
  const STATUS = { done: '已生成', stale: '需重新生成', none: '未生成', absent: '缺考' };

  const SURNAMES = '柏 常 池 迟 储 戴 樊 费 甘 耿 关 杭 纪 简 解 柯 蓝 雷 路 梅 苗 倪 欧 裴 戚 冉 邵 时 舒 汤 滕 韦 翁 席 项 邢 荀 颜 应 郁 喻 湛 章 甄 卓 邹 祖 艾 薄 岑 单 房 葛 华 季 康 乐 毛 南 宁 庞'.split(' ');
  const GIVENS = '一诺 亦凡 子墨 宇航 雨桐 欣怡 梓涵 诗涵 欣妍 可欣 语桐 梦瑶 俊杰 浩然 明轩 子轩 思远 博文 天佑 沐宸 奕辰 若汐 艺涵 若曦 煜城 鹤轩 瑾瑜 皓轩 擎宇 志泽 睿渊 弘文 哲瀚 雨泽 楷瑞 建辉 晋鹏 天磊 绍辉 泽洋 鑫鹏 博涛 苑杰 黎昕 胤祥 梦洁 凌薇 美莲 雅静 雪丽 依娜 雅芙 雨婷 晟涵 梦舒 秀影 海莲 歆蕾 书瑶 慕晴 希蓝 芮涵 婧琪 璟雯 诗茵 静璇 婕珍 沐卉 琪涵 佳琦 雪慧 淑颖 乐姗 玥怡 芸熙 钰彤 天瑜 婧宜 彦妮 馨蕊 静香 笑薇 曼玉'.split(' ');

  const students = [];
  const used = new Set();
  let code = 20250901;
  for (const cls of ['9班', '10班']) {
    const n = cls === '9班' ? 46 : 45;
    if (cls === '10班') code = 20251001;
    for (let i = 0; i < n; i++) {
      let name;
      do { name = SURNAMES[ri(0, SURNAMES.length - 1)] + GIVENS[ri(0, GIVENS.length - 1)]; }
      while (used.has(name));
      used.add(name);
      students.push({
        id: 'stu' + students.length, code: String(code++), name, cls,
        ability: clamp(0.7 + (rnd() + rnd() - 1) * 0.22, 0.4, 0.97),
      });
    }
  }

  // 每场每人：分数、缺考；再算班内名次
  for (const ex of EXAMS) {
    for (const st of students) {
      const absent = rnd() < (ex.current ? 0 : 0.02);
      const score = absent ? null : clamp(Math.round(st.ability * 100 + (rnd() - 0.5) * 16 + (ex.id === 'e4' ? 2 : ex.id === 'e3' ? 1 : 0)), 18, 100);
      st['sc_' + ex.id] = score;
      st['abs_' + ex.id] = absent;
    }
    for (const cls of ['9班', '10班']) {
      const grp = students.filter(s => s.cls === cls && s['sc_' + ex.id] != null)
        .sort((a, b) => b['sc_' + ex.id] - a['sc_' + ex.id]);
      grp.forEach((st, i) => st['rk_' + ex.id] = i + 1);
    }
  }

  // 当前场报告状态：3 缺考(沿用 absent) + 6 未生成 + 12 需重新生成 + 其余已生成
  // 用确定性洗牌决定
  const order = students.map((s, i) => i);
  for (let i = order.length - 1; i > 0; i--) { const j = Math.floor(rnd() * (i + 1));[order[i], order[j]] = [order[j], order[i]]; }
  const absentSet = new Set(students.filter(s => s.abs_e4).map(s => s.id));
  let nNone = 6, nStale = 12;
  for (const st of students) {
    for (const ex of EXAMS) {
      if (st['abs_' + ex.id]) { st['st_' + ex.id] = 'absent'; continue; }
      if (!ex.current) { st['st_' + ex.id] = rnd() < 0.06 ? 'stale' : 'done'; continue; }
      st['st_' + ex.id] = 'done';
    }
  }
  for (const idx of order) {
    const st = students[idx];
    if (st.st_e4 !== 'done') continue;
    if (nNone > 0) { st.st_e4 = 'none'; nNone--; }
    else if (nStale > 0) { st.st_e4 = 'stale'; nStale--; }
  }
  // 当前场强制 3 人缺考（原型示例数）
  let forced = 0;
  for (const idx of order) {
    if (forced >= 3) break;
    const st = students[idx];
    if (st.st_e4 !== 'done') continue;
    st.abs_e4 = true; st.sc_e4 = null; st.st_e4 = 'absent';
    delete st.rk_e4;
    forced++;
  }

  // 知识点池（用于报告文本）
  const KP = ['勾股定理的证明', '用勾股定理求边长', '展开曲面求最短路', '区分平方根与算术根',
    '二次根式有意义的条件', '估算无理数的大小', '立方根的概念', '实数与数轴',
    '识别勾股数', '化简二次根式', '勾股定理的逆定理', '算术平方根'];
  const QERR = ['把斜边当成直角边代入', '开方时漏掉负根', '根号内为负仍继续运算', '估算区间取错',
    '未化成最简二次根式', '逆定理条件记反', '单位换算出错', '审题漏看"不小于"'];

  // 每生每场题目得分（18 题）
  const QN = 18;
  function questions(st, ex) {
    const qs = [];
    let s = 20261013 + parseInt(st.code.slice(-4)) + ex.id.charCodeAt(1) * 97;
    const r = () => { s = (s * 1103515245 + 12345) % 2147483648; return s / 2147483648; };
    for (let i = 1; i <= QN; i++) {
      const full = i <= 4 ? 3 : i <= 10 ? 4 : i <= 14 ? 6 : 8;
      const p = clamp(st.ability + (r() - 0.5) * 0.35, 0, 1);
      const got = st['abs_' + ex.id] ? null : p > 0.75 ? full : p > 0.42 ? Math.round(full * (0.3 + r() * 0.4)) : 0;
      qs.push({ n: i, full, got, kp: KP[Math.floor(r() * KP.length)] });
    }
    return qs;
  }

  function rankDelta(st) {
    const a = st.rk_e3, b = st.rk_e4;
    return (a != null && b != null) ? a - b : null; // 正=进步
  }
  function statusCounts(exId, cls) {
    const c = { done: 0, stale: 0, none: 0, absent: 0 };
    for (const st of students) {
      if (cls !== 'all' && st.cls !== cls) continue;
      c[st['st_' + exId]]++;
    }
    return c;
  }
  const byId = id => students.find(s => s.id === id);
  const scope = cls => cls === 'all' ? students : students.filter(s => s.cls === cls);
  function qmeta(exId) {
    const ex = EXAMS.find(e => e.id === exId);
    const tmpl = questions(students[0], ex);
    const cache = new Map(students.map(st => [st.id, questions(st, ex)]));
    return tmpl.map(q => {
      let s = 0, n = 0;
      for (const st of students) { const g = cache.get(st.id)[q.n - 1].got; if (g != null) { s += g; n++; } }
      return { n: q.n, full: q.full, avg: n ? s / n : 0 };
    });
  }
  const e4 = () => EXAMS.find(e => e.current);

  return { EXAMS, STATUS, students, questions, rankDelta, statusCounts, byId, scope, qmeta, e4, KP, QERR, rnd, ri, clamp };
})();
