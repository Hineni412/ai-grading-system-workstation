// 原型数据：章节结构取自八上教材目录；题型名称部分为示意；题目全部为合成数据，不含真实题库正文。
window.QB_PROTO = (() => {
  const chapters = [
    { id: 'c1', label: '第一章 勾股定理', sections: [
      { id: 'c1s1', label: '1 探索勾股定理', types: ['利用三边图形面积关系求值', '弦图拼图中的面积推理', '直接用勾股定理求边与量'], points: ['用勾股定理解三角形', '以直角三角形三边为边长的图形面积', '利用勾股定理证明线段平方关系'] },
      { id: 'c1s2', label: '2 一定是直角三角形吗', types: ['判定直角三角形', '逆定理求角或面积'], points: ['判断三边能否构成直角三角形', '在网格中判断直角三角形', '利用勾股定理的逆定理求解'] },
      { id: 'c1s3', label: '3 勾股定理的应用', types: ['折叠中的勾股计算', '最短路径与展开图', '实际情境求高度或距离'], points: ['勾股定理与折叠问题', '勾股定理与网格问题', '求梯子滑落高度'] },
    ] },
    { id: 'c2', label: '第二章 实数', sections: [
      { id: 'c2s1', label: '1 认识实数', types: ['辨识无理数', '实数分类与数轴对应'], points: ['无理数', '无理数的大小估算', '实数的分类'] },
      { id: 'c2s2', label: '2 平方根与立方根', types: ['求平方根与立方根', '非负性求参数', '估算无理数的整数部分'], points: ['求一个数的算术平方根', '利用算术平方根的非负性解题', '无理数整数部分的有关计算'] },
      { id: 'c2s3', label: '3 二次根式', types: ['根式化简与四则运算', '乘法公式参与根式运算', '二次根式有意义的条件'], points: ['二次根式的加减运算', '化为最简二次根式', '二次根式有意义的条件'] },
    ] },
    { id: 'c3', label: '第三章 位置与坐标', sections: [
      { id: 'c3s1', label: '1 确定位置', types: ['有序数对与方位描述'], points: ['用有序数对表示位置', '用方向角和距离确定物体的位置'] },
      { id: 'c3s2', label: '2 平面直角坐标系', types: ['写点坐标与判象限', '点到坐标轴的距离', '坐标系中求面积'], points: ['写出直角坐标系中点的坐标', '求点到坐标轴的距离', '已知点所在的象限求参数'] },
      { id: 'c3s3', label: '3 轴对称与坐标变化', types: ['关于坐标轴对称的点', '坐标系中的图形变化'], points: ['坐标系中的对称', '坐标与图形变化——轴对称'] },
    ] },
    { id: 'c4', label: '第四章 一次函数', sections: [
      { id: 'c4s1', label: '1 函数', types: ['函数概念与自变量范围'], points: ['函数的概念', '求自变量的取值范围'] },
      { id: 'c4s2', label: '2 认识一次函数', types: ['识别一次函数并求参数'], points: ['识别一次函数', '根据一次函数的定义求参数'] },
      { id: 'c4s3', label: '3 一次函数的图象', types: ['k、b 与图象位置', '求一次函数解析式', '图象性质比较大小'], points: ['根据一次函数解析式判断其经过的象限', '正比例函数的性质', '判断一次函数的图象'] },
      { id: 'c4s4', label: '4 一次函数的应用', types: ['分段计价与方案选择', '行程图象信息题'], points: ['梯度计价问题', '行程问题(一次函数的实际应用)', '最大利润问题(一次函数的实际应用)'] },
    ] },
    { id: 'c5', label: '第五章 二元一次方程组', sections: [
      { id: 'c5s1', label: '1 认识二元一次方程组', types: ['方程组的定义与解'], points: ['二元一次方程的定义', '二元一次方程的解'] },
      { id: 'c5s2', label: '2 二元一次方程组的解法', types: ['消元法解方程组', '已知解求参数'], points: ['代入消元法', '加减消元法', '二元一次方程组的错解复原问题'] },
      { id: 'c5s3', label: '3 二元一次方程组的应用', types: ['列方程组解应用题'], points: ['方案问题(二元一次方程组的应用)', '行程问题(二元一次方程组的应用)'] },
      { id: 'c5s4', label: '4 二元一次方程与一次函数', types: ['交点与方程组的解', '直线围成的面积'], points: ['求一次函数解析式', '两直线的交点与二元一次方程组的解', '求直线围成的图形面积'] },
    ] },
    { id: 'c6', label: '第六章 数据的分析', sections: [
      { id: 'c6s1', label: '1 平均数与方差', types: ['平均数、众数与方差计算'], points: ['求一组数据的平均数', '求众数'] },
      { id: 'c6s2', label: '2 中位数与箱线图', types: ['中位数与四分位数'], points: ['求中位数', '求四分位数'] },
    ] },
    { id: 'c7', label: '第七章 证明', sections: [
      { id: 'c7s2', label: '2 认识证明', types: ['命题真假与反例'], points: ['判断命题真假', '举例说明假（真）命题'] },
      { id: 'c7s3', label: '3 平行线的证明', types: ['平行线判定与性质证明', '三角形内角和推理'], points: ['同位角相等两直线平行', '内错角相等两直线平行'] },
    ] },
  ]
  const earlierPoints = ['倒数', '点到直线的距离', '线段的和与差']
  const dims = {
    ability: ['推理能力', '运算能力', '几何直观', '模型观念', '空间观念', '应用意识', '数据观念'],
    thought: ['数形结合思想', '方程思想', '分类讨论思想', '函数思想', '转化思想'],
    method: ['坐标法', '图象法', '待定系数法', '构造辅助线法', '公式法', '代入消元法', '加减消元法', '整体代入法'],
    model: ['全等三角形模型', '面积等积模型', '折叠模型', '角平分线模型', '一线三垂直模型'],
    special: ['动态几何题', '格点作图题', '新定义题', '数学阅读理解题', '规律探究题'],
    error: ['概念理解', '计算与化简', '审题与条件', '方法与思路', '过程与依据'],
  }
  const papers = ['示意卷 A · 期中', '示意卷 B · 期中', '示意卷 C · 期末', '示意卷 D · 期末', '示意卷 E · 单元测', '示意卷 F · 单元测']

  let seed = 20261008
  const rand = () => { seed = (seed * 1664525 + 1013904223) % 4294967296; return seed / 4294967296 }
  const pick = list => list[Math.floor(rand() * list.length)]
  const maybe = (p, list) => (rand() < p ? [pick(list)] : [])
  const sections = chapters.flatMap(chapter => chapter.sections.map(section => ({ ...section, chapter })))
  const questions = []
  for (let i = 1; i <= 180; i++) {
    const section = sections[Math.floor(Math.pow(rand(), 1.3) * sections.length)]
    const qtype = rand() < 0.4 ? '选择题' : rand() < 0.45 ? '填空题' : '解答题'
    const type = pick(section.types)
    const others = sections.filter(s => s.chapter === section.chapter && s !== section).flatMap(s => s.types)
    const secondary = others.length && rand() < 0.15 ? [pick(others)] : []
    const kps = [...new Set([pick(section.points), ...maybe(0.35, section.points)])]
    if (rand() < 0.08) kps.push(pick(earlierPoints))
    const abilities = [...new Set([pick(dims.ability), ...maybe(0.3, dims.ability)])]
    questions.push({
      id: i, section: section.id, chapter: section.chapter.id, qtype,
      difficulty: Math.max(1, Math.min(10, Math.round((2 + rand() * 7 + (qtype === '解答题' ? 1 : 0)) * 10) / 10)),
      paper: pick(papers), points: qtype === '解答题' ? 2 + Math.floor(rand() * 4) : 1,
      type: [type, ...secondary], kp: kps, ability: abilities,
      thought: maybe(0.55, dims.thought), method: maybe(0.45, dims.method), model: maybe(0.18, dims.model),
      special: maybe(0.35, dims.special), error: maybe(0.4, dims.error),
      stem: `【合成题干 ${i}】${section.chapter.label.split(' ')[1]} · ${section.label.replace(/^[*☆\d\s]+/, '')}：${type}${qtype === '选择题' ? '（四选一）' : qtype === '填空题' ? '（填空）' : '（写出过程）'}`,
      duplicates: rand() < 0.06 ? 1 + Math.floor(rand() * 2) : 0,
    })
  }
  return { chapters, earlierPoints, dims, papers, questions }
})()
