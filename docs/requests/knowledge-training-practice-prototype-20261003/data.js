/* data.js — 合成数据（种子固定，不连接真实接口）
   结构：卷(八年级上册) → 章 → 节 → 技能；学生 → 考试得分率/技能掌握度/各场错题 */
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
  const EXAMS = [
    { id: 'e1', name: '第二周学情反馈', date: '9月6日' },
    { id: 'e2', name: '第三周学情反馈', date: '9月13日' },
    { id: 'e3', name: '第四周学情反馈', date: '9月20日' },
  ];

  // ---- 章节结构（沿用知识结构页原型的章节树） ----
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
  // “已学到”可选项（之后章为未学）
  const LEARN_OPTIONS = ['第一章 勾股定理', '第二章 实数', '第三章 位置与坐标', '第四章 一次函数'];

  // ---- 展平成对象结构 ----
  let seq = 0;
  const skillById = {};
  const chapters = TREE.map(ch => ({
    id: 'ch' + seq++, name: ch.name,
    sections: ch.sections.map(sec => ({
      id: 'sec' + seq++, name: sec.name, chName: ch.name,
      topics: sec.t.map(n => ({ id: 'tp' + seq++, name: n })),
      skills: sec.s.map(n => {
        const sk = { id: 'sk' + seq++, name: n, secName: sec.name, chName: ch.name,
          _d: 0.35 + rnd() * 0.4,
          diffBank: Array.from({ length: 8 }, () => ri(2, 6)), // 各难度级（1–8）适用题数
        };
        skillById[sk.id] = sk;
        return sk;
      }),
    })),
  }));
  const allSkills = Object.values(skillById);
  const sectionSkillIds = sec => sec.skills.map(s => s.id);
  const chapterSkillIds = ch => ch.sections.flatMap(sectionSkillIds);
  const allSkillIds = () => allSkills.map(s => s.id);

  // ---- 学生姓名（合成） ----
  const NAMES9 = ('陈思远 刘雨桐 王子墨 李欣怡 张浩然 赵梓涵 孙诗涵 周俊杰 吴梦瑶 郑博文 ' +
    '冯天佑 褚明轩 卫若汐 蒋艺涵 沈煜城 韩瑾瑜 杨皓轩 朱亦辰 秦哲瀚 尤雨泽 许楷瑞 何博涛').split(' ');
  const NAMES10 = ('吕雪丽 施依娜 孔雅静 曹心妍 严笑薇 华曼玉 金书瑶 魏慕晴 陶芮涵 姜静璇 ' +
    '戚晟涵 谢凌薇 邹佳琦 喻芸熙 窦钰彤 章天瑜 云可岚 苏彦妮 潘馨蕊 葛静香').split(' ');

  // ---- 生成学生 ----
  const TIERS = ['weak', 'unsteady', 'stable', 'insufficient', 'none'];
  const TIER_LABEL = { weak: '明显薄弱', unsteady: '还不稳', stable: '较稳定', insufficient: '证据不足', none: '无观测' };
  const TIER_RANK = { weak: 0, unsteady: 1, insufficient: 2, stable: 3, none: 4 }; // 越小越薄弱

  const students = [];
  const mkStudents = (names, cls, prefix) => {
    names.forEach((name, i) => {
      const id = 'stu' + students.length;
      const ability = clamp(0.68 + (rnd() + rnd() - 1) * 0.26, 0.42, 0.96);
      const scores = {};
      for (const e of EXAMS) scores[e.id] = clamp(ability + (rnd() - 0.5) * 0.18, 0.35, 0.98);
      const bLo = clamp(Math.round(ability * 7) + ri(-1, 0), 1, 6); // 可练难度带 1–8
      students.push({ id, code: prefix + String(i + 1).padStart(2, '0'), name, cls, ability, scores,
        band: [bLo, Math.min(bLo + ri(2, 3), 8)],
        skills: {}, wrongs: [], newPool: ri(2, 9) });
    });
  };
  mkStudents(NAMES9, '9班', '0901');
  mkStudents(NAMES10, '10班', '1001');

  // 两名学生本学期无成绩
  const NO_SCORE = [students[5], students[31]];
  for (const st of NO_SCORE) for (const e of EXAMS) st.scores[e.id] = null;
  // 两名学生三场考试零错题
  const NO_WRONG = [students[1], students[36]];
  for (const st of students) {
    const vals = EXAMS.map(e => st.scores[e.id]).filter(v => v != null);
    st.scoreRate = vals.length ? vals.reduce((a, b) => a + b, 0) / vals.length : null;
  }

  // ---- 每技能掌握度 ----
  for (const st of students) {
    for (const sk of allSkills) {
      const r = rnd();
      if (r < 0.05) { st.skills[sk.id] = { m: null, tier: 'none' }; continue; }
      if (r < 0.13) { st.skills[sk.id] = { m: clamp(st.ability - sk._d + 0.6 + (rnd() - 0.5) * 0.2, 0.05, 0.98), tier: 'insufficient' }; continue; }
      const m = clamp(st.ability - sk._d + 0.62 + (rnd() - 0.5) * 0.18, 0.03, 0.99);
      const tier = m < 0.55 ? 'weak' : (m < 0.75 ? 'unsteady' : 'stable');
      st.skills[sk.id] = { m, tier };
    }
  }

  // ---- 每场考试错题 ----
  for (const st of students) {
    if (NO_WRONG.includes(st)) continue;
    const seen = []; // 之前场次错题（用于"同一原题多次失分"）
    for (const e of EXAMS) {
      if (st.scores[e.id] == null) continue;
      const n = Math.round((1 - st.ability) * ri(6, 12) + rnd() * 2); // 0–8 左右
      const usedQ = new Set();
      for (let i = 0; i < n; i++) {
        let q; do { q = ri(1, 22); } while (usedQ.has(q)); usedQ.add(q);
        const sk = allSkills[ri(0, allSkills.length - 1)];
        let qid = e.id + '-q' + q;
        if (seen.length && rnd() < 0.12) qid = seen[ri(0, seen.length - 1)]; // 原题再次失分
        else seen.push(qid);
        st.wrongs.push({ exam: e.id, qno: '第' + q + '题', qid, skill: sk.id, diff: ri(1, 8), missing: rnd() < 0.06 });
      }
    }
  }

  // ---- 聚合工具 ----
  const scopeStudents = (cls, minScore) => students.filter(st =>
    (cls === 'all' || st.cls === cls) &&
    (minScore == null || (st.scoreRate != null && st.scoreRate * 100 >= minScore)));

  function worstTier(st, skillIds) {
    let w = 'none';
    for (const id of skillIds) {
      const t = st.skills[id] ? st.skills[id].tier : 'none';
      if (TIER_RANK[t] < TIER_RANK[w]) w = t;
    }
    return w;
  }
  function tierCounts(studs, skillIds) {
    const c = { weak: 0, unsteady: 0, stable: 0, insufficient: 0, none: 0 };
    for (const st of studs) c[worstTier(st, skillIds)]++;
    return c;
  }
  function weakSkillsOf(st, skillIds) {
    return skillIds.filter(id => { const t = st.skills[id].tier; return t === 'weak' || t === 'unsteady'; });
  }
  function weakCount(st) {
    return allSkills.filter(sk => st.skills[sk.id].tier === 'weak').length;
  }

  // ---- 同质小组（同技能需要 + 相容的可练难度范围） ----
  const bandI = (a, b) => {
    const lo = Math.max(a[0], b[0]), hi = Math.min(a[1], b[1]);
    return lo <= hi ? [lo, hi] : null;
  };
  const GRP_CAPS = [6, 5, 6, 6]; // 各组人数上限（让组大小有别）
  function buildGroups(skillIds, studs) {
    const withWeak = [], noEvidence = [];
    for (const st of studs) {
      const ws = weakSkillsOf(st, skillIds);
      if (ws.length) withWeak.push({ st, ws });
      else noEvidence.push(st);
    }
    withWeak.sort((a, b) => b.ws.length - a.ws.length ||
      (avgM(a.st, a.ws) - avgM(b.st, b.ws)));
    const groups = [], leftover = [];
    const rest = withWeak.slice();
    let guard = 0;
    const usedTargets = new Set();
    while (rest.length >= 2 && groups.length < 4 && guard++ < 60) {
      // 优先选弱项尚未被已有组目标覆盖的 seed，让各组目标有别
      let si = 0;
      if (usedTargets.size) {
        let best = -1;
        for (let i = 0; i < rest.length; i++) {
          const uncovered = rest[i].ws.filter(id => !usedTargets.has(id)).length;
          if (uncovered > best) { best = uncovered; si = i; }
        }
      }
      const seedM = rest.splice(si, 1)[0];
      // 组目标：seed 弱项中优先取尚未被覆盖的 1–2 项；适合难度带 = seed 的可练范围
      const targets = seedM.ws.slice()
        .sort((x, y) => (usedTargets.has(x) ? 1 : 0) - (usedTargets.has(y) ? 1 : 0)
          || (seedM.st.skills[x].m ?? 9) - (seedM.st.skills[y].m ?? 9))
        .slice(0, 2);
      const band = seedM.st.band.slice();
      const members = [seedM.st];
      const cap = GRP_CAPS[groups.length % GRP_CAPS.length];
      for (let i = rest.length - 1; i >= 0 && members.length < cap; i--) {
        const m = rest[i];
        // 同技能需要（≥1 组目标） + 可练难度带与组相容
        if (!bandI(band, m.st.band) || !m.ws.some(id => targets.includes(id))) continue;
        members.push(m.st); rest.splice(i, 1);
      }
      if (members.length >= 2) { groups.push({ targets, band, members }); targets.forEach(id => usedTargets.add(id)); }
      else leftover.push(seedM.st);
    }
    for (const m of rest) leftover.push(m.st);
    const noEv = noEvidence.map(st => ({
      st, reason: skillIds.every(id => st.skills[id].tier === 'none')
        ? '本节尚无作答证据，无法判断薄弱点'
        : '本节技能作答均已达标或证据不足，无直接失分',
    }));
    const unfit = leftover.map(st => {
      const ws = weakSkillsOf(st, skillIds);
      const skillHit = groups.some(g => g.targets.some(id => ws.includes(id)));
      const bandHit = groups.some(g => g.targets.some(id => ws.includes(id)) && bandI(g.band, st.band));
      return {
        st,
        reason: skillHit && !bandHit
          ? `薄弱点与小组相同，但可练难度 ${st.band[0]}–${st.band[1]} 级与候选组不重叠`
          : '薄弱技能与已成型小组目标不同，同薄弱点人数不足',
      };
    });
    return { groups, ungrouped: { noEvidence: noEv, unfit } };
  }
  function avgM(st, skillIds) {
    const vs = skillIds.map(id => st.skills[id].m).filter(v => v != null);
    return vs.length ? vs.reduce((a, b) => a + b, 0) / vs.length : null;
  }
  function bandBank(skill, band) {
    let s = 0;
    for (let d = band[0]; d <= band[1]; d++) s += skill.diffBank[d - 1];
    return s;
  }
  function groupStats(g) {
    // g: {targets:[skillId], members:[student]}
    const perM = g.members.map(st => ({ st, m: avgM(st, g.targets) }));
    const ms = perM.map(x => x.m).filter(v => v != null);
    const clsC = {};
    for (const m of g.members) clsC[m.cls] = (clsC[m.cls] || 0) + 1;
    const sr = g.members.map(s => s.scoreRate).filter(v => v != null);
    const band = g.band || g.members[0].band;
    return {
      n: g.members.length, clsC, band,
      avgMastery: ms.length ? ms.reduce((a, b) => a + b, 0) / ms.length : null,
      minM: ms.length ? Math.min(...ms) : null, maxM: ms.length ? Math.max(...ms) : null,
      spread: ms.length ? Math.max(...ms) - Math.min(...ms) : null,
      scoreRate: sr.length ? sr.reduce((a, b) => a + b, 0) / sr.length : null,
      bank: g.targets.reduce((a, id) => a + bandBank(skillById[id], band), 0),
      perM,
    };
  }

  return { VOLUME, EXAMS, LEARN_OPTIONS, chapters, allSkills, skillById,
    sectionSkillIds, chapterSkillIds, allSkillIds,
    students, TIERS, TIER_LABEL, TIER_RANK,
    scopeStudents, worstTier, tierCounts, weakSkillsOf, weakCount, avgM,
    buildGroups, groupStats, rnd, ri, clamp };
})();
