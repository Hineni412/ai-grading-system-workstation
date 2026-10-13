/* 合成示例数据 —— 全部为虚构数据，仅用于原型展示 */
window.QB_FIG = {
  /* 程序图：输入x → 取算术平方根 → 取立方根 → 是有理数？(是:返回/否:输出y) */
  flow: '<svg viewBox="0 0 300 142" width="300" height="142" role="img" aria-label="计算程序图">'
    + '<defs><marker id="qa" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="6" markerHeight="6" orient="auto"><path d="M0,0 L8,4 L0,8 Z" fill="#7c959c"/></marker></defs>'
    + '<rect x="8" y="10" width="58" height="22" rx="4" fill="#fff" stroke="#8aa5ab"/>'
    + '<text x="37" y="25" font-size="10.5" text-anchor="middle">输入x值</text>'
    + '<line x1="66" y1="21" x2="78" y2="21" stroke="#7c959c" marker-end="url(#qa)"/>'
    + '<rect x="80" y="10" width="88" height="22" rx="4" fill="#fff" stroke="#8aa5ab"/>'
    + '<text x="124" y="25" font-size="10.5" text-anchor="middle">取算术平方根</text>'
    + '<line x1="168" y1="21" x2="180" y2="21" stroke="#7c959c" marker-end="url(#qa)"/>'
    + '<rect x="182" y="10" width="58" height="22" rx="4" fill="#fff" stroke="#8aa5ab"/>'
    + '<text x="211" y="25" font-size="10.5" text-anchor="middle">取立方根</text>'
    + '<line x1="211" y1="32" x2="211" y2="50" stroke="#7c959c" marker-end="url(#qa)"/>'
    + '<polygon points="211,52 255,76 211,100 167,76" fill="#fff" stroke="#8aa5ab"/>'
    + '<text x="211" y="79" font-size="9.5" text-anchor="middle">是有理数?</text>'
    + '<path d="M167,76 H124 V34" fill="none" stroke="#7c959c" marker-end="url(#qa)"/>'
    + '<text x="141" y="70" font-size="9.5" fill="#7c959c">是</text>'
    + '<line x1="211" y1="100" x2="211" y2="112" stroke="#7c959c" marker-end="url(#qa)"/>'
    + '<text x="217" y="110" font-size="9.5" fill="#7c959c">否（无理数）</text>'
    + '<rect x="182" y="114" width="58" height="22" rx="4" fill="#fff" stroke="#8aa5ab"/>'
    + '<text x="211" y="129" font-size="10.5" text-anchor="middle">输出y</text></svg>',
  /* 秋千示意：O 为支点，OA 静止位置，OB 推送后位置 */
  swing: '<svg viewBox="0 0 210 168" width="210" height="168" role="img" aria-label="秋千示意图">'
    + '<line x1="30" y1="152" x2="190" y2="152" stroke="#b6c2c6"/>'
    + '<circle cx="96" cy="18" r="3" fill="#176b75"/><text x="84" y="16" font-size="11">O</text>'
    + '<line x1="96" y1="18" x2="96" y2="120" stroke="#8aa5ab" stroke-width="1.4"/>'
    + '<rect x="88" y="120" width="16" height="5" rx="1" fill="#8aa5ab"/>'
    + '<text x="76" y="126" font-size="11">A</text>'
    + '<line x1="96" y1="18" x2="162" y2="86" stroke="#8aa5ab" stroke-width="1.4"/>'
    + '<rect x="156" y="86" width="16" height="5" rx="1" fill="#8aa5ab"/>'
    + '<text x="174" y="90" font-size="11">B</text>'
    + '<line x1="96" y1="120" x2="162" y2="120" stroke="#9fb4ba" stroke-dasharray="3 3"/>'
    + '<line x1="162" y1="120" x2="162" y2="92" stroke="#9fb4ba" stroke-dasharray="3 3"/>'
    + '<path d="M154,120 h8 v-8" fill="none" stroke="#9fb4ba"/>'
    + '<text x="108" y="114" font-size="10" fill="#6b7785">10尺</text>'
    + '<text x="166" y="110" font-size="9.5" fill="#6b7785">x−4尺</text>'
    + '<line x1="96" y1="126" x2="96" y2="152" stroke="#9fb4ba" stroke-dasharray="3 3"/>'
    + '<text x="102" y="142" font-size="10" fill="#6b7785">离地1尺</text>'
    + '<text x="52" y="70" font-size="10" fill="#6b7785" transform="rotate(-90 52 70)">OA = x 尺</text>'
    + '<text x="146" y="48" font-size="10" fill="#6b7785" transform="rotate(-42 146 48)">OB = x 尺</text>'
    + '<circle cx="186" cy="96" r="4" fill="none" stroke="#9fb4ba"/>'
    + '<line x1="186" y1="100" x2="186" y2="128" stroke="#9fb4ba"/>'
    + '<text x="172" y="142" font-size="9.5" fill="#6b7785">人高5尺</text></svg>'
};

window.QB_QUESTIONS = {
  q6: {
    no: '第 6 题', source: '第四周延时练习-普通用卷', form: '选择题', diff: 4.5,
    typeMain: '按程序图迭代开方判断', typeSub: '无',
    chapter: '第二章 实数 › 2 平方根', kp: ['程序设计与实数运算', '开方运算'],
    ability: ['运算能力', '推理能力'], idea: ['数形结合'], method: ['程序模拟'], model: [], special: [],
    stem: '按如图所示的程序计算，若开始输入的 x 的值为 64，则最后输出的 y 的值为（　　）',
    fig: 'flow', options: ['∛2', '√2', '2', '8'],
    answer: '答案：B　解析：64 的算术平方根为 8，取立方根得 2，2 仍是有理数，按程序返回再取算术平方根得 √2，是无理数，输出 y=√2，故选 B。',
    exam: { count: 1, rate: 42, ref: '第三周学情反馈 · 第13题' },
    type: { rate: 68, need: 16, total: 41, dist: [10, 6, 22, 3] },
    stuck: { step: '化简二次根式', repeated: 22, indep: '45/82', common: true },
    freq: { final: 60, mid: 38 },
    examRows: [{ ref: '第三周学情反馈 · 第13题', rate: 42 }],
    stuckRows: [
      { step: '化简二次根式', indepRate: 55, indep: '45/82 人', repeated: 22, common: true },
      { step: '算术平方根与立方根混淆', indepRate: 30, indep: '25/82 人', repeated: 8 },
      { step: '按程序图顺序执行', indepRate: 15, indep: '12/82 人', repeated: 0 }
    ],
    points: [{ t: '作答为B', d: '输入 x=64 时选择正确的输出 y 值 · 客观作答',
      ks: ['程序设计与实数运算', '求一个数的算术平方根', '求一个数的立方根'], pre: '无理数' }],
    wrongs: [
      { o: 'A', t: '程序步骤顺序看错', d: '先取立方根再取平方根，得到 ∛2 后误选', m: '方法与思路 · 选项预分析 · 已有实际作答记录' },
      { o: 'C', t: '漏看循环条件', d: '算到 2 即认为输出结束，未判断“是否为有理数”', m: '方法与思路 · 选项预分析 · 已有实际作答记录' },
      { o: 'D', t: '把中间结果当输出', d: '将第一步算术平方根的结果 8 直接当作 y 值', m: '方法与思路 · 选项预分析' }
    ]
  },
  q13: {
    no: '第 13 题', source: '第五周周末练习-普通用卷', form: '解答题', diff: 5.2,
    typeMain: '勾股定理列方程解实际情境', typeSub: '列方程求几何量',
    chapter: '第一章 勾股定理 › 3 勾股定理的应用', kp: ['勾股定理的实际应用', '列方程解应用题', '古文数学阅读'],
    ability: ['推理能力', '模型观念'], idea: ['方程思想'], method: ['数形转化'], model: ['勾股模型'], special: [],
    stem: '明朝数学家程大位在他的著作《算法统宗》中写了一首计算秋千绳索长度的词《西江月》：“平地秋千未起，踏板一尺离地．送行二步与人齐，五尺人高曾记．仕女佳人争蹴，终朝笑语欢嬉．良工高士素好奇，算出索长有几？”译文：有一架秋千，当它静止时，踏板离地一尺．将它往前推送两步（两步算 10 尺）时，秋千的踏板与人一样高，此人的身高为五尺．如果这时秋千的绳索拉得很直，求秋千绳索（OA 或 OB）的长度．',
    fig: 'swing', options: null,
    answer: '答案：OA = OB = 14.5 尺　解析：设绳索长 x 尺．支点 O 距地面 (x+1) 尺，人高 5 尺，故推送后踏板比 O 低 (x−4) 尺；水平推送 10 尺，由勾股定理 x² = (x−4)² + 10²，解得 x = 14.5 尺。',
    exam: null,
    type: { rate: 55, need: 22, total: 48, dist: [12, 10, 20, 6] },
    stuck: null,
    freq: { final: 74, mid: 51 },
    examRows: [],
    stuckRows: [
      { step: '把情境转化为直角三角形', indepRate: 41, indep: '20/49 人', repeated: 0 },
      { step: '列方程并求解', indepRate: 18, indep: '9/49 人', repeated: 0 }
    ],
    points: [
      { t: '设索长为 x 并构造直角三角形', d: '把“送行二步与人齐”转化为水平 10 尺、竖直 (x−4) 尺 · 关键步骤',
        ks: ['勾股定理的实际应用'], pre: '列方程解应用题' },
      { t: '列 x²=(x−4)²+10² 并解得 14.5 尺', d: '方程建模与求解 · 主观给分',
        ks: ['勾股定理', '一元一次方程'], pre: '完全平方公式' }
    ],
    wrongs: [
      { o: '误', t: '竖直方向距离算错', d: '忘记扣除踏板离地 1 尺，直接用 5 尺作竖直边', m: '审题与建模 · 已有实际作答记录' },
      { o: '误', t: '把“两步”当 2 尺', d: '未将两步换算为 10 尺，水平边代入错误', m: '审题与建模 · 选项预分析' }
    ]
  },
  q11: {
    no: '第 11 题', source: '期中学情反馈-基础卷', form: '填空题', diff: 4.0,
    typeMain: '立体图形表面最短路径', typeSub: '无',
    chapter: '第一章 勾股定理 › 4 最短路径问题', kp: ['圆柱侧面展开图', '勾股定理'],
    ability: ['空间观念', '运算能力'], idea: ['转化与化归'], method: ['侧面展开'], model: [], special: ['新情境应用'],
    stem: '一个圆柱形饮料罐底面周长为 5 cm，高为 3 cm．一只蚂蚁从底面圆周上的点 A 处出发，沿圆柱侧面爬行一周到点 B 处．则蚂蚁爬行的最短路径长度为 ________ cm．',
    fig: null, options: null,
    answer: '答案：√34　解析：将圆柱侧面沿过 A 的母线展开为矩形，A、B 的水平距离等于底面周长 5，竖直距离为高 3，最短路径为 √(5²+3²) = √34 cm。',
    exam: { count: 2, rate: 81, ref: '期中学情反馈 · 第9题' },
    type: { rate: 74, need: 9, total: 37, dist: [4, 5, 25, 3] },
    stuck: { step: '圆柱侧面展开', repeated: 9, indep: '18/45', common: false },
    freq: null,
    examRows: [
      { ref: '期中学情反馈 · 第9题', rate: 81 },
      { ref: '第二周学情反馈 · 第15题', rate: 76 }
    ],
    stuckRows: [
      { step: '圆柱侧面展开', indepRate: 40, indep: '18/45 人', repeated: 9 },
      { step: '展开后对应边识别', indepRate: 22, indep: '10/45 人', repeated: 0 }
    ],
    points: [{ t: '作答为 √34', d: '写出含根号的最简结果 · 客观作答',
      ks: ['圆柱的侧面展开图', '勾股定理'], pre: '无理数' }],
    wrongs: [
      { o: '误', t: '未展开直接连线', d: '在圆柱面上目测连线，得到 3 或 5 等结果', m: '空间观念 · 已有实际作答记录' },
      { o: '误', t: '周长直径混淆', d: '把底面周长 5 当作直径代入计算', m: '概念理解 · 选项预分析' }
    ]
  }
};
