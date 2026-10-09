// 原型合成数据：姓名、学号、题型、分数与档位均为随机生成，不对应任何真实学生或考试。
window.PROTO = (() => {
  let seed = 20261009
  const rnd = () => (seed = (seed * 1103515245 + 12345) % 2147483648) / 2147483648
  const pick = list => list[Math.floor(rnd() * list.length)]
  const sigmoid = x => 1 / (1 + Math.exp(-x))

  const chapters = [
    { id: 'c1', label: '第一章 勾股定理', learned: true, sections: [
      { id: 'c1s1', label: '1 探索勾股定理', types: ['利用三边图形面积求值', '弦图拼图中的面积推理', '直接用勾股定理求边', '网格中表示无理数长度'] },
      { id: 'c1s2', label: '2 一定是直角三角形吗', types: ['辨识勾股数', '判定直角三角形', '判定直角后求高与面积'] },
      { id: 'c1s3', label: '3 勾股定理的应用', types: ['实景测量求边长', '由等长条件列勾股方程', '立体表面展开求最短路径', '容器内线段长度范围'] },
    ] },
    { id: 'c2', label: '第二章 实数', learned: true, sections: [
      { id: 'c2s1', label: '1 认识实数', types: ['辨识无理数', '估算无理数的整数范围', '比较实数大小', '数轴上定位无理数'] },
      { id: 'c2s2', label: '2 平方根与立方根', types: ['求平方根与立方根', '辨析根与实数概念', '由根反求原数与字母', '按程序图迭代开方'] },
      { id: 'c2s3', label: '3 二次根式', types: ['二次根式有意义的条件', '双向根式夹击求字母', '辨析最简与同类根式', '根式化简与四则运算', '乘法公式参与根式运算'] },
    ] },
    { id: 'c3', label: '第三章 位置与坐标', learned: false, sections: [
      { id: 'c3s1', label: '1 确定位置', types: ['用有序数对描述位置'] },
      { id: 'c3s2', label: '2 平面直角坐标系', types: ['象限内点的坐标特征', '坐标系中的距离与面积'] },
    ] },
    { id: 'c4', label: '第四章 一次函数', learned: false, sections: [
      { id: 'c4s1', label: '1 函数', types: ['判断变量间的函数关系'] },
      { id: 'c4s2', label: '2 一次函数与正比例函数', types: ['由条件求一次函数表达式', '一次函数图象性质辨析'] },
    ] },
    { id: 'c5', label: '第五章 二元一次方程组', learned: false, sections: [
      { id: 'c5s1', label: '1 认识二元一次方程组', types: ['辨识二元一次方程组'] },
      { id: 'c5s2', label: '2 求解二元一次方程组', types: ['代入与加减消元求解'] },
    ] },
  ]

  const types = []
  for (const chapter of chapters) for (const section of chapter.sections) for (const name of section.types) {
    types.push({ key: `${section.id}-${types.length}`, name, chapter, section, examined: chapter.learned,
      difficulty: (rnd() - 0.5) * 2.4, definition: `考查学生能否${name.replace(/^(利用|直接用|由|按)/, '')}；以本章主考题为准，次题型不计入掌握度。` })
  }

  const surnames = '王李张刘陈杨赵黄周吴徐孙胡朱高林何郭马罗梁宋郑谢韩唐冯于董萧程曹袁邓许傅沈曾彭吕苏卢蒋蔡贾丁魏薛叶阎余潘杜戴夏钟汪田任姜范方石姚谭廖邹熊金陆郝孔白崔康毛邱秦江史顾侯邵孟龙万段雷钱汤尹黎易常武乔贺赖龚文'.split('')
  const given = '子涵 雨桐 浩然 梓轩 思远 欣怡 宇航 诗涵 俊熙 若曦 嘉怡 一诺 明轩 语嫣 博文 佳琪 天佑 心怡 奕辰 可欣 泽宇 静怡 晨阳 雅琳 皓轩 梦瑶 子墨 婉清 承泽 安然'.split(' ')
  const students = []
  const used = new Set()
  for (let i = 0; i < 91; i += 1) {
    const cls = i < 48 ? '9' : '10'
    let name
    do { name = pick(surnames) + pick(given) } while (used.has(name))
    used.add(name)
    const ability = (rnd() + rnd() + rnd() - 1.5) * 2.2
    const noScore = i === 17 || i === 66
    const student = { id: `s${i + 1}`, name, cls, code: `2025${cls.padStart(2, '0')}${String((i < 48 ? i : i - 48) + 1).padStart(2, '0')}`,
      ability, score: noScore ? null : Math.min(0.98, Math.max(0.06, sigmoid(ability * 1.1 + 0.4) + (rnd() - 0.5) * 0.12)), tiers: {} }
    if (!noScore) {
      for (const type of types) {
        if (!type.examined || rnd() < 0.06) continue
        const p = sigmoid(ability - type.difficulty + 0.9 + (rnd() - 0.5) * 1.4)
        const tier = rnd() < 0.08 ? 'insufficient' : p < 0.4 ? 'weak' : p < 0.6 ? 'unsteady' : 'stable'
        student.tiers[type.key] = { tier, mastery: p }
      }
    }
    students.push(student)
  }

  const exams = ['九月学情反馈', '第一章单元测', '第四周练习']
  for (const type of types) {
    if (!type.examined) continue
    type.typical = { exam: pick(exams), no: 1 + Math.floor(rnd() * 22), rate: 0.2 + rnd() * 0.4,
      text: `【合成示例题】与“${type.name}”同类的一道考试原题题面，正式页面显示原题题干、公式与配图。` }
    type.others = [0, 1].map(() => ({ exam: pick(exams), no: 1 + Math.floor(rnd() * 22), rate: 0.4 + rnd() * 0.5 }))
  }

  return { chapters, types, students, sessionCount: 3 }
})()
