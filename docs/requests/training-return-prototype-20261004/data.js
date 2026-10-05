/* data.js — 回收批改原型的合成数据：20 名学生、10 题 24 判定点、掌握度样例、来源标注 */

// 每块的取数口径（勾选"标注数据来源"后叠显）
const SRC = {
  counts: '扫描批次 submissions + 各份 assessment + feedback 修订',
  queue: 'submission.status / assessment.status / review_points / feedback.source_review_revision',
  ledger: 'GET /submissions/{id}/assessment review_points',
  lock: 'POST /assessment/reviews（逐点）',
  judge: '逐份 POST /submissions/{id}/assessment',
  update: '逐份 POST /submissions/{id}/evidence publish',
  feedback: 'GET /submissions/{id}/feedback mastery_changes',
};

// ---- 学生（8班 0801-0810 / 9班 0901-0910，版本 V1/V2，页数 1-3）----
const S_NAMES = [
  ['陈曦','林浩','苏婉','高远','何静','马骏','许诺','杜若','沈舟','韩雪'],
  ['曹阳','彭越','吕蒙','朱婷','秦风','尤丽','许晴','邹凯','白露','宁静'],
];
const S_VER  = [2,1,2,2,1,2,2,1,2,2, 1,2,2,1,2,2,1,2,2,1];
const S_PAGE = [2,3,1,2,2,2,3,2,1,2, 2,1,3,2,2,1,3,2,1,2];
const STUDENTS = S_NAMES.flatMap((names, c) => names.map((name, i) => {
  const ix = c * 10 + i;
  return { id:'s'+String(ix+1).padStart(2,'0'), ix, name,
    cls:(c===0?'8班':'9班'), sid:(c===0?'08':'09')+String(i+1).padStart(2,'0'),
    ver:'V'+S_VER[ix], pages:S_PAGE[ix] };
}));

// ---- 题目与判定点：八年级上册 · 勾股定理/实数/平面直角坐标系/一次函数/全等三角形 ----
// 选择、填空各 1 点；解答题按依赖给 4-7 点。ev 为 AI 留的短证据。
const QS = [
  { t:'选择 · Rt△ABC 中 ∠C=90°，a=3，b=4，斜边 c 为', pts:['选出 c=5'], ev:['勾选 A 项'] },
  { t:'选择 · 化简 √9 的结果为', pts:['选出 3'], ev:['勾选 B 项'] },
  { t:'填空 · 点 P(3,-2) 关于 x 轴的对称点坐标', pts:['写出 (3, 2)'], ev:['写为 (3, 2)'] },
  { t:'填空 · 一次函数 y=2x-1 与 y 轴的交点坐标', pts:['写出 (0, -1)'], ev:['写为 (0, -1)'] },
  { t:'选择 · 下列各数中是无理数的是', pts:['选出 √2'], ev:['勾选 C 项'] },
  { t:'填空 · 补充一个条件使 △ABC≌△DEF：AB=DE，∠B=∠E，还需', pts:['写出 BC=EF（配 SAS）'], ev:['写 BC=EF'] },
  { t:'选择 · 一次函数 y=-2x+3 的图象经过的象限', pts:['选出一、二、四象限'], ev:['勾选 D 项'] },
  { t:'解答 · 在 Rt△ABC 中 ∠C=90°，a=6，b=8，求斜边 c', pts:[
      '写出 a²+b²=c² 并代入 a=6、b=8', '求得 c²=100', '求得斜边 c=10',
      '说明舍去负根 c=-10 的理由', '单位与答语完整', '步骤书写规范、无跳步' ],
    ev:['第 2 行公式与代入正确', '第 3 行算出 100', '第 4 行 c=10',
      '第 4 行字迹重叠，认不出是否写了舍根', '有答语带单位', '步骤连贯'] },
  { t:'解答 · 已知一次函数图象过 (1,-1) 与 (3,3)，求解析式', pts:[
      '设出 y=kx+b 并代入两点', '列出方程组求出 k=2', '求出 b=-3',
      '写出解析式 y=2x-3', '代回一点检验成立', '说明 k、b 的实际含义正确' ],
    ev:['设 y=kx+b，代入正确', 'k=2 求解正确', 'b=-3 正确',
      '写出 y=2x-3', '代入 (1,-1) 检验', '含义栏涂抹严重，认不出内容'] },
  { t:'解答 · 已知 AB=DE、∠B=∠E、BC=EF，证明 △ABC≌△DEF', pts:[
      '找出三组对应边与对应角', '写出判定依据 SAS', '对应顶点书写顺序正确',
      '推出结论 △ABC≌△DEF', '证明过程完整、无跳步' ],
    ev:['对应关系列出', '标注 SAS', '顺序一致', '结论成立', '过程完整'] },
];
const QN = QS.map(q => q.pts.length);          // [1,1,1,1,1,1,1,6,6,5]
const TOTAL_PTS = QN.reduce((a, b) => a + b, 0); // 24

// ---- 已完成学生的掌握度样例 ----
const MASTERY = [
  { goal:'勾股定理求边', before:'还不稳 62%', after:'较稳定 81%' },
  { goal:'实数与二次根式', before:'较稳定 71%', after:'较稳定 84%' },
  { goal:'平面直角坐标系中点的对称', before:'还不稳 48%', after:'还不稳 66%' },
  { goal:'待定系数法求一次函数解析式', before:'明显薄弱 35%', after:'还不稳 58%' },
  { goal:'全等三角形判定（SAS/ASA）', before:'较稳定 78%', after:'较稳定 90%' },
];
const CHAP_SUM = [
  '第十二章 勾股定理 · 2 个小节 · 平均 +19%',
  '第十三章 实数 · 1 个小节 · 平均 +13%',
  '第十四章 一次函数 · 2 个小节 · 平均 +23%',
];
