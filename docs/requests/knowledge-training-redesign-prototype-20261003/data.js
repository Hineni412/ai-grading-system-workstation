/* data.js — 合成数据（种子固定，不连接真实接口）
   结构：卷(八年级上册) → 章 → 节 → 项(知识点/技能) → 学生观测
   另有"往届内容"组（七年级下册 · 本学期考试涉及） */
const KT = (() => {
  let seed = 20261003;
  function rnd() {
    seed |= 0; seed = (seed + 0x6D2B79F5) | 0;
    let t = Math.imul(seed ^ (seed >>> 15), 1 | seed);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  }
  const ri = (a, b) => a + Math.floor(rnd() * (b - a + 1));
  const clamp = (v, a, b) => Math.min(b, Math.max(a, v));

  const VOLUME = '八年级上册';

  // ---- 已考查章：手工命名（真实感） ----
  const TREE = [
    { name: '第一章 勾股定理', sections: [
      { name: '1 探索勾股定理',
        t: ['勾股定理的内容', '勾股定理的验证（面积法）', '勾股定理的证明'],
        s: ['确认直角与斜边', '用勾股定理求边长', '列出勾股等式'] },
      { name: '2 一定是直角三角形吗',
        t: ['勾股定理的逆定理', '勾股数', '直角三角形的判定条件'],
        s: ['由三边判断直角三角形', '识别勾股数'] },
      { name: '3 勾股定理的应用',
        t: ['勾股定理的实际应用', '展开曲面求最短路'],
        s: ['建模列勾股方程', '展开图求最短路径', '在格点中应用勾股定理', '解实际测量问题'] },
    ]},
    { name: '第二章 实数', sections: [
      { name: '1 认识无理数',
        t: ['无理数的概念', '无理数的判断'],
        s: ['区分有理数与无理数', '用面积估算无理数'] },
      { name: '2 平方根',
        t: ['平方根的概念', '算术平方根', '开平方运算'],
        s: ['求一个数的平方根', '区分平方根与算术根'] },
      { name: '3 立方根',
        t: ['立方根的概念', '开立方运算'],
        s: ['求立方根', '比较立方根与平方根'] },
      { name: '4 估算',
        t: ['估算无理数的大小', '无理数的区间估计'],
        s: ['用夹逼法估算无理数'] },
      { name: '5 用计算器开方',
        t: ['计算器开方操作'],
        s: ['用计算器求平方根与立方根'] },
      { name: '6 实数',
        t: ['实数的分类', '实数与数轴', '实数的相反数与绝对值'],
        s: ['在数轴上表示实数', '实数大小比较'] },
      { name: '7 二次根式',
        t: ['二次根式的概念', '二次根式有意义的条件', '最简二次根式', '二次根式的乘除'],
        s: ['化简二次根式', '二次根式乘除运算', '二次根式有意义的条件判断'] },
    ]},
  ];

  // ---- 未考查章：只有结构与项数 ----
  const DORMANT = [
    { name: '第三章 位置与坐标', secs: 3, t: 8, s: 6 },
    { name: '第四章 一次函数', secs: 5, t: 13, s: 9 },
    { name: '第五章 二元一次方程组', secs: 7, t: 16, s: 11 },
    { name: '第六章 数据的分析', secs: 4, t: 9, s: 7 },
    { name: '第七章 证明', secs: 5, t: 12, s: 8 },
    { name: '综合与实践', secs: 2, t: 4, s: 3 },
  ];

  // ---- 往届内容（七年级下册，本学期考试涉及） ----
  const PRIOR = [
    ['运用完全平方公式进行运算', 'skill'], ['平方差公式因式分解', 'skill'],
    ['幂的乘方与积的乘方', 'topic'], ['同底数幂的乘除运算', 'topic'],
    ['三角形内角和定理应用', 'topic'], ['全等三角形的判定（SSS）', 'topic'],
    ['轴对称图形的性质运用', 'skill'], ['线段垂直平分线性质运用', 'skill'],
    ['用表格表示变量关系', 'topic'], ['等可能事件的概率计算', 'topic'],
  ];

  // ---- 学生姓名（合成） ----
  const SURNAMES = '柏 常 池 迟 储 戴 樊 费 甘 耿 关 杭 纪 简 解 柯 蓝 雷 路 梅 苗 倪 欧 裴 戚 冉 邵 时 舒 汤 滕 韦 翁 席 项 邢 荀 颜 应 郁 喻 湛 章 甄 卓 邹 祖 艾 薄 岑 单 房 葛 华 季 康 乐 毛 南 宁 庞'.split(' ');
  const GIVENS = '一诺 亦凡 子墨 宇航 雨桐 欣怡 梓涵 诗涵 欣妍 可欣 语桐 梦瑶 俊杰 浩然 明轩 子轩 思远 博文 天佑 沐宸 奕辰 若汐 艺涵 若曦 煜城 鹤轩 瑾瑜 皓轩 擎宇 志泽 睿渊 弘文 哲瀚 雨泽 楷瑞 建辉 晋鹏 天磊 绍辉 泽洋 鑫鹏 博涛 苑杰 黎昕 胤祥 梦洁 凌薇 美莲 雅静 雪丽 依娜 雅芙 雨婷 晟涵 梦舒 秀影 海莲 歆蕾 书瑶 慕晴 希蓝 芮涵 婧琪 璟雯 诗茵 静璇 婕珍 沐卉 琪涵 佳琦 雪慧 淑颖 乐姗 玥怡 芸熙 钰彤 璟雯 天瑜 婧琪 静宸 诗嘉 可岚 天瑜 婧宜 彦妮 馨蕊 静香 雪慧 心妍 笑薇 曼玉'.split(' ');

  // ---- 生成学生 ----
  const students = [];
  const usedNames = new Set();
  let code = 20250901;
  for (const cls of ['9班', '10班']) {
    const n = cls === '9班' ? 46 : 45;
    if (cls === '10班') code = 20251001;
    for (let i = 0; i < n; i++) {
      let name;
      do { name = SURNAMES[ri(0, SURNAMES.length - 1)] + GIVENS[ri(0, GIVENS.length - 1)]; }
      while (usedNames.has(name));
      usedNames.add(name);
      // 能力 0.42–0.95，略偏正态
      const ability = clamp(0.68 + (rnd() + rnd() - 1) * 0.22, 0.42, 0.95);
      students.push({
        id: 'stu' + students.length, code: String(code++),
        name, cls, ability,
        scoreRate: clamp(ability + (rnd() - 0.5) * 0.1, 0.3, 0.99),
      });
    }
  }

  // ---- 生成知识项 ----
  let seq = 0;
  function mkItem(name, kind, chName, secName, extra) {
    return Object.assign({
      id: 'it' + (seq++), name, kind, chName, secName,
      def: `考查学生对"${name}"的识别、理解或直接应用；以作答证据为准，相邻项目互不推断。`,
      obs: {}, // sid -> {m,lo,hi,tier,obs,full}
    }, extra || {});
  }
  const items = [];
  const chapters = [];
  for (const ch of TREE) {
    const chap = { name: ch.name, sections: [], examined: true };
    for (const sec of ch.sections) {
      const s = { name: sec.name, topics: [], skills: [] };
      for (const t of sec.t) { const it = mkItem(t, 'topic', ch.name, sec.name); s.topics.push(it); items.push(it); }
      for (const k of sec.s) { const it = mkItem(k, 'skill', ch.name, sec.name); s.skills.push(it); items.push(it); }
      chap.sections.push(s);
    }
    chapters.push(chap);
  }
  for (const d of DORMANT) chapters.push({ name: d.name, examined: false, sections: [], counts: d });
  const prior = { name: '往届内容', vol: '七年级下册', items: PRIOR.map(([n, k]) => {
    const it = mkItem(n, k, '往届内容', '七年级下册', { prior: true });
    items.push(it); return it;
  }) };

  // ---- 观测：已考查项 + 往届项 有证据；未考查无 ----
  const evidenceItems = items; // 全部为已考查/往届项
  // 2 名学生本学期无任何证据
  students[7]._noEv = true; students[58]._noEv = true;
  // 先决定每项是否"有学生明显薄弱"（目标 ~55%），通过难度控制
  for (const it of evidenceItems) {
    const hard = rnd() < (it.kind === 'skill' ? 0.62 : 0.5);
    it._d = hard ? 0.6 + rnd() * 0.15 : 0.45 + rnd() * 0.22;
    it._hasWeak = hard;
  }
  for (const it of evidenceItems) {
    for (const st of students) {
      if (st._noEv) continue;
      if (rnd() < 0.06) continue; // 少量学生无观测
      const obs = ri(1, 12);
      let m = clamp(st.ability - it._d + 0.62 + (rnd() - 0.5) * 0.16, 0.03, 0.99);
      if (!it._hasWeak && m < 0.62) m = 0.62 + rnd() * 0.2;
      const w = clamp(0.2 / Math.sqrt(obs), 0.04, 0.2);
      const lo = clamp(m - w, 0, 1), hi = clamp(m + w, 0, 1);
      const tier = obs < 3 ? 'insufficient' : (hi < 0.6 ? 'weak' : (lo >= 0.6 ? 'stable' : 'unsteady'));
      const full = Math.round(m * obs);
      it.obs[st.id] = { m, lo, hi, tier, obs, full };
    }
  }

  // ---- 知识点↔技能 关联（仿真实结构 {topic_key,skill_key,question_count,same_part_question_count,basis}） ----
  const itemById = Object.fromEntries(items.map(i => [i.id, i]));
  const skillBySec = {}, skillByChap = {};
  for (const ch of chapters.filter(c => c.examined)) {
    skillByChap[ch.name] = [];
    for (const sec of ch.sections) {
      skillBySec[sec.name] = sec.skills;
      skillByChap[ch.name].push(...sec.skills);
    }
  }
  const assoc = [];
  for (const ch of chapters.filter(c => c.examined)) for (const sec of ch.sections) {
    for (const tp of sec.topics) {
      const n = ri(1, 3), chosen = new Set();
      for (let i = 0; i < n; i++) {
        const pool = rnd() < 0.85 ? skillBySec[sec.name]
          : skillByChap[ch.name].filter(s => s.secName !== sec.name);
        if (!pool.length) continue;
        const sk = pool[Math.floor(rnd() * pool.length)];
        if (chosen.has(sk.id)) continue;
        chosen.add(sk.id);
        const sp = rnd() < 0.7;
        const qc = sp ? ri(4, 20) : ri(1, 4);
        assoc.push({
          topic_key: tp.id, skill_key: sk.id,
          question_count: qc,
          same_part_question_count: sp ? qc : 0,
          basis: sp ? 'same_part' : 'question_cooccurrence',
        });
      }
    }
  }
  function relatedOf(item) {
    const out = [];
    for (const a of assoc) {
      if (a.topic_key === item.id)
        out.push({ item: itemById[a.skill_key], basis: a.basis, qc: a.question_count, spc: a.same_part_question_count });
      else if (a.skill_key === item.id)
        out.push({ item: itemById[a.topic_key], basis: a.basis, qc: a.question_count, spc: a.same_part_question_count });
    }
    return out;
  }

  // ---- 聚合工具 ----
  const TIERS = ['weak', 'unsteady', 'stable', 'insufficient'];
  const TIER_LABEL = { weak: '明显薄弱', unsteady: '还不稳', stable: '较稳定', insufficient: '证据不足' };
  function agg(item, studs) {
    const c = { weak: 0, unsteady: 0, stable: 0, insufficient: 0, none: 0 };
    let ms = 0, mn = 0;
    const byTier = { weak: [], unsteady: [], stable: [], insufficient: [] };
    for (const st of studs) {
      const o = item.obs[st.id];
      if (!o) { c.none++; continue; }
      c[o.tier]++;
      byTier[o.tier].push({ st, o });
      if (o.tier !== 'insufficient') { ms += o.m; mn++; }
    }
    for (const t of TIERS) byTier[t].sort((a, b) => a.o.m - b.o.m);
    return {
      counts: c, byTier,
      evidence: studs.length - c.none,
      groupMastery: mn ? ms / mn : null,
      weakCount: c.weak,
    };
  }
  function groupTier(a) {
    // 有观测学生中人数最多的档位；证据不足不参与多数
    const c = a.counts;
    const pool = [['stable', c.stable], ['unsteady', c.unsteady], ['weak', c.weak]];
    pool.sort((x, y) => y[1] - x[1]);
    if (!pool[0][1]) return 'insufficient';
    return pool[0][0];
  }
  function studentAgg(st) {
    let weak = 0, un = 0, stable = 0, ins = 0;
    const weakItems = [];
    for (const it of evidenceItems) {
      const o = it.obs[st.id];
      if (!o) continue;
      if (o.tier === 'weak') { weak++; weakItems.push(it); }
      else if (o.tier === 'unsteady') un++;
      else if (o.tier === 'stable') stable++;
      else ins++;
    }
    return { weak, unsteady: un, stable, insufficient: ins, weakItems };
  }
  // 全班层面 KPI（all 范围基准的固定数字，随范围重算）
  function scopeStudents(cls) { return cls === 'all' ? students : students.filter(s => s.cls === cls); }
  function kpis(studs) {
    const ids = new Set(studs.map(s => s.id));
    let withEv = 0, weakAny = 0, scoreSum = 0;
    for (const st of studs) {
      const sa = studentAgg(st);
      const has = sa.weak + sa.unsteady + sa.stable + sa.insufficient > 0;
      if (has) { withEv++; scoreSum += st.scoreRate; }
      if (sa.weak > 0) weakAny++;
    }
    let weakTopics = 0, weakSkills = 0;
    for (const it of evidenceItems) {
      if (it.prior) continue; // 往届内容不计入本册数字
      const a = agg(it, studs);
      if (a.counts.weak > 0) (it.kind === 'skill' ? weakSkills++ : weakTopics++);
    }
    return {
      total: studs.length, withEv, weakAny,
      scoreRate: withEv ? scoreSum / withEv : null,
      weakTopics, weakSkills,
      weakSkillItems: evidenceItems.filter(it => !it.prior && it.kind === 'skill' && agg(it, studs).counts.weak > 0)
        .map(it => ({ it, a: agg(it, studs) }))
        .sort((x, y) => y.a.counts.weak - x.a.counts.weak
          || y.a.counts.unsteady - x.a.counts.unsteady
          || (x.a.groupMastery ?? 1) - (y.a.groupMastery ?? 1)),
      weakTopicCount: weakTopics,
    };
  }

  return { VOLUME, students, chapters, prior, evidenceItems, TIERS, TIER_LABEL,
    agg, groupTier, studentAgg, scopeStudents, kpis, rnd, clamp,
    assoc, relatedOf, itemById };
})();
