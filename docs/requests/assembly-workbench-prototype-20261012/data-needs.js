/* 虚构学情数据：班级、考试、要补的点（题型）、相关原题、分组与试卷篮初稿 */
PROTO.CLASSES = [
  { id:'9', name:'9班', students:45 },
  { id:'10', name:'10班', students:46 },
];
PROTO.EXAMS = [
  { id:'w2', title:'第二周学情反馈', date:'2026-09-14', students:90 },
  { id:'w3', title:'第三周学情反馈', date:'2026-09-21', students:89 },
  { id:'w4', title:'第四周学情反馈', date:'2026-09-28', students:91 },
];
PROTO.CAUSE_CATS = ['概念理解','计算与化简','审题与条件','方法与思路','过程与依据','书写与规范','未作答'];
PROTO.CAUSE_COLORS = ['#6c9b8a','#75aebb','#ba9666','#937eae','#a09173','#83a2a8','#b57874'];

/* 相关原题：失分的考试原题（非题库题，含题面与各班得分率） */
PROTO.ORIGINALS = {
  o1a:{ key:'o1a', exam:'w4', label:'14(8)', type:'解答题', score:6, rate:38, rates:{c9:43,c10:32}, diff:5.2,
    stem:'计算：(√18 − √8) ÷ √2 + √(3/4)',
    causes:{ '计算与化简':14, '方法与思路':6, '过程与依据':3, '未作答':2 } },
  o1b:{ key:'o1b', exam:'w3', label:'Q9', type:'选择题', score:3, rate:41, rates:{c9:44,c10:38}, diff:4.6,
    stem:'(√27 − √12) ÷ √3 的结果是（ ）',
    causes:{ '计算与化简':10, '概念理解':4, '审题与条件':2 } },
  o1c:{ key:'o1c', exam:'w3', label:'17(2)', type:'解答题', score:4, rate:45, rates:{c9:49,c10:41}, diff:4.9,
    stem:'化简：√(5/3) × √15 ÷ √5',
    causes:{ '计算与化简':8, '书写与规范':2 } },
  o1d:{ key:'o1d', exam:'w2', label:'Q12', type:'填空题', score:3, rate:47, rates:{c9:51,c10:43}, diff:3.6,
    stem:'√24 ÷ √6 = ____',
    causes:{ '计算与化简':6 } },
  o1e:{ key:'o1e', exam:'w4', label:'Q6', type:'选择题', score:3, rate:52, rates:{c9:55,c10:49}, diff:3.9,
    stem:'下列运算正确的是（ ）A. √9÷√3 = 3  B. √8·√2 = 4  C. √5·√5 = 25  D. √12÷2 = √6',
    causes:{ '概念理解':5, '计算与化简':3 } },
  o1f:{ key:'o1f', exam:'w2', label:'15(1)', type:'解答题', score:3, rate:58, rates:{c9:61,c10:54}, diff:3.2,
    stem:'计算：√48 ÷ √3 − 2',
    causes:{ '计算与化简':4, '审题与条件':1 } },
  o2a:{ key:'o2a', exam:'w4', label:'Q8', type:'选择题', score:3, rate:33, rates:{c9:36,c10:30}, diff:4.3, img:'数轴示意',
    stem:'如图，数轴上点 M 落在 3 与 4 之间且更靠近 3，点 M 可能表示的数是（ ）',
    causes:{ '概念理解':9, '方法与思路':5, '审题与条件':3 } },
  o2b:{ key:'o2b', exam:'w3', label:'Q5', type:'选择题', score:3, rate:42, rates:{c9:45,c10:39}, diff:4.0, img:'数轴示意',
    stem:'如图，在数轴上标出 √10 的位置，应落在（ ）之间',
    causes:{ '概念理解':6, '计算与化简':2 } },
  o2c:{ key:'o2c', exam:'w2', label:'Q9', type:'选择题', score:3, rate:55, rates:{c9:58,c10:52}, diff:3.4,
    stem:'与 √13 最接近的整数是（ ）',
    causes:{ '审题与条件':4, '概念理解':3 } },
  o3a:{ key:'o3a', exam:'w3', label:'Q8', type:'选择题', score:3, rate:44, rates:{c9:47,c10:41}, diff:3.7,
    stem:'下列说法正确的是（ ）A. √4 = ±2  B. −9 的平方根是 −3  C. (−3)² 的算术平方根是 3  D. 0 没有平方根',
    causes:{ '概念理解':8, '审题与条件':2 } },
  o4a:{ key:'o4a', exam:'w2', label:'Q4', type:'选择题', score:3, rate:62, rates:{c9:65,c10:59}, diff:2.9,
    stem:'下列各根式中，属于最简二次根式的是（ ）',
    causes:{ '概念理解':6 } },
  o5a:{ key:'o5a', exam:'w4', label:'Q11', type:'填空题', score:3, rate:46, rates:{c9:49,c10:43}, diff:4.4,
    stem:'计算：√8 + √18 − √32 = ____',
    causes:{ '计算与化简':9, '书写与规范':2 } },
  o6a:{ key:'o6a', exam:'w3', label:'Q13', type:'填空题', score:3, rate:63, rates:{c9:66,c10:60}, diff:4.1,
    stem:'直角三角形的两边长为 3 和 4，则第三边长为 ____',
    causes:{ '概念理解':7, '审题与条件':4 } },
  o7a:{ key:'o7a', exam:'w4', label:'Q16', type:'解答题', score:6, rate:39, rates:{c9:42,c10:36}, diff:5.8, img:'折断的树示意',
    stem:'一棵高 9 m 的树被风折断，树梢落地处距树根 3 m，求折断处距地面的高度。',
    causes:{ '方法与思路':8, '审题与条件':4, '未作答':3 } },
  o8a:{ key:'o8a', exam:'w2', label:'Q7', type:'选择题', score:3, rate:66, rates:{c9:69,c10:63}, diff:3.0,
    stem:'若 √(2−x) 在实数范围内有意义，则 x 的取值范围是（ ）',
    causes:{ '概念理解':5 } },
  o9a:{ key:'o9a', exam:'w4', label:'Q13', type:'填空题', score:3, rate:43, rates:{c9:46,c10:40}, diff:5.1,
    stem:'化简：|√5 − 3| + √((√5 − 2)²) = ____',
    causes:{ '计算与化简':7, '概念理解':5 } },
  o10a:{ key:'o10a', exam:'w3', label:'Q6', type:'选择题', score:3, rate:68, rates:{c9:71,c10:65}, diff:4.5,
    stem:'比较大小：√7 + 2 ____ √5 + 3',
    causes:{ '方法与思路':4, '计算与化简':3 } },
  o11a:{ key:'o11a', exam:'w2', label:'Q16', type:'解答题', score:5, rate:74, rates:{c9:77,c10:71}, diff:5.3,
    stem:'化简：6 ÷ √3 + √(1/3) × √27',
    causes:{ '计算与化简':7, '过程与依据':2 } },
  o12a:{ key:'o12a', exam:'w4', label:'Q18', type:'解答题', score:6, rate:71, rates:{c9:74,c10:68}, diff:5.6,
    stem:'计算：(−1)³ + |1 − √2| − √8 ÷ √2',
    causes:{ '计算与化简':6, '审题与条件':2 } },
  oN1:{ key:'oN1', exam:'w3', label:'Q20', type:'解答题', score:8, rate:29, rates:{c9:31,c10:27}, diff:7.2,
    stem:'如图，在 Rt△ABC 中，∠C = 90°，D 为斜边 AB 上一点，CD ⊥ AB，AC = 6，BC = 8。求 CD 的长。',
    causes:{ '方法与思路':9, '未作答':5, '过程与依据':4 } },
  oN2:{ key:'oN2', exam:'w4', label:'Q19', type:'填空题', score:4, rate:34, rates:{c9:37,c10:31}, diff:6.4,
    stem:'已知 √x+3 + |y−1| = 0，则 x + 2y = ____',
    causes:{ '概念理解':8, '审题与条件':3 } },
};
PROTO.EXAM_SHORT = { w2:'第二周', w3:'第三周', w4:'第四周' };

/* 要补的点（题型）：minRate 取相关原题中最低得分率 */
PROTO.POINTS = [
  { id:'p1', name:'二次根式的乘除运算', section:'八年级上册 · 第二章 实数 · 7 二次根式', weak:18, evidence:85, failTotal:6, related:['o1a','o1b','o1c','o1d','o1e','o1f'], strong:8, groups:6 },
  { id:'p2', name:'数轴上表示与估算无理数', section:'八年级上册 · 第二章 实数 · 1 认识无理数', weak:21, evidence:88, failTotal:4, related:['o2a','o2b','o2c'], strong:11, groups:4 },
  { id:'p3', name:'平方根与算术平方根的区别', section:'八年级上册 · 第二章 实数 · 2 平方根', weak:15, evidence:86, failTotal:3, related:['o3a'], strong:6, groups:5 },
  { id:'p4', name:'最简二次根式的识别', section:'八年级上册 · 第二章 实数 · 7 二次根式', weak:9, evidence:72, failTotal:2, related:['o4a'], strong:3, groups:7 },
  { id:'p5', name:'二次根式的加减合并', section:'八年级上册 · 第二章 实数 · 7 二次根式', weak:16, evidence:84, failTotal:3, related:['o5a'], strong:7, groups:6 },
  { id:'p6', name:'勾股定理求边长', section:'八年级上册 · 第一章 勾股定理 · 1 探索勾股定理', weak:13, evidence:90, failTotal:3, related:['o6a'], strong:4, groups:9 },
  { id:'p7', name:'勾股定理的实际应用', section:'八年级上册 · 第一章 勾股定理 · 3 勾股定理的应用', weak:20, evidence:82, failTotal:3, related:['o7a'], strong:12, groups:8 },
  { id:'p8', name:'二次根式有意义的取值范围', section:'八年级上册 · 第二章 实数 · 7 二次根式', weak:8, evidence:75, failTotal:2, related:['o8a'], strong:2, groups:5 },
  { id:'p9', name:'绝对值与算术平方根的非负性', section:'八年级上册 · 第二章 实数 · 6 实数', weak:17, evidence:87, failTotal:2, related:['o9a'], strong:9, groups:3 },
  { id:'p10', name:'无理数的大小比较', section:'八年级上册 · 第二章 实数 · 4 估算', weak:11, evidence:80, failTotal:2, related:['o10a'], strong:5, groups:4 },
  { id:'p11', name:'分母有理化与根式除法', section:'八年级上册 · 第二章 实数 · 7 二次根式', weak:14, evidence:79, failTotal:3, related:['o11a'], strong:6, groups:2 },
  { id:'p12', name:'实数的混合运算', section:'八年级上册 · 第二章 实数 · 6 实数', weak:10, evidence:86, failTotal:3, related:['o12a'], strong:4, groups:6 },
];

/* 其他需关注题型（默认收起在链接后） */
PROTO.EXTRA_POINTS = [
  { id:'e1', name:'立方根的概念与计算', weak:7, section:'八年级上册 · 第二章 实数 · 3 立方根', strong:3, groups:4 },
  { id:'e2', name:'用计算器开方取近似值', weak:5, section:'八年级上册 · 第二章 实数 · 5 用计算器开方', strong:1, groups:1 },
  { id:'e3', name:'勾股定理逆定理判定直角', weak:9, section:'八年级上册 · 第一章 勾股定理 · 2 一定是直角三角形吗', strong:5, groups:6 },
  { id:'e4', name:'实数与数轴的一一对应', weak:6, section:'八年级上册 · 第二章 实数 · 6 实数', strong:2, groups:2 },
  { id:'e5', name:'二次根式估值的复合题', weak:12, section:'八年级上册 · 第二章 实数 · 4 估算', strong:6, groups:3 },
  { id:'e6', name:'算术平方根的双重非负性', weak:8, section:'八年级上册 · 第二章 实数 · 2 平方根', strong:4, groups:3 },
];

/* 试卷篮初稿：顺序即卷面顺序；source 是来源标签；pointId 指明补的点 */
PROTO.DRAFT = [
  { qid:'Q01', pointId:'p2', source:'补 第四周·Q8' },
  { qid:'Q02', pointId:'p3', source:'补 第三周·Q8' },
  { qid:'Q13', pointId:'p9', source:'补 第四周·Q13' },
  { qid:'Q04', pointId:'p10', source:'补 第三周·Q6' },
  { qid:'Q05', pointId:'p12', source:'补 第四周·Q18' },
  { qid:'Q07', pointId:'p6', source:'补 第三周·Q13' },
  { qid:'Q16', pointId:'p5', source:'补 第四周·Q11' },
  { qid:'Q09', pointId:'p1', source:'补 第三周·Q9' },
  { qid:'Q10', pointId:'p6', source:'补 第三周·Q13' },
  { qid:'Q17', pointId:'p6', source:'补 第三周·Q13' },
];
