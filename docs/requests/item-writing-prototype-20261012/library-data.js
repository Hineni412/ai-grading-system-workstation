/* 命题训练原型 - 情境库兜底数据（合成）：无本机试抽数据时使用；另含“模拟抽取”合成情境池 */
window.ITEM_LIBRARY_DEMO = {
  source_note: '合成兜底（未加载本机试抽数据）',
  terms: {
    '八年级上册': { has_candidates: true, chapters: [
      { name: '第一章 勾股定理', fit: 12, dedup: 11, extracted: 0 },
      { name: '第二章 实数', fit: 3, dedup: 3, extracted: 0 },
      { name: '第三章 位置与坐标', fit: 6, dedup: 5, extracted: 0 },
      { name: '第四章 一次函数', fit: 14, dedup: 13, extracted: 0 },
      { name: '第五章 二元一次方程组', fit: 9, dedup: 8, extracted: 0 },
      { name: '第六章 数据的分析', fit: 7, dedup: 7, extracted: 0 },
      { name: '第七章 证明', fit: 2, dedup: 2, extracted: 0 } ] },
    '八年级下册': { has_candidates: false, note: '无候选题', chapters: [] }
  },
  materials: [
    { id: 'd1', kind: '情境材料', title: '快递包裹计费规则', situation: '快递公司按包裹重量分段计费，首重与续重单价不同，涉及分段函数关系。', section: '4.4 一次函数的应用', chapter: '第四章 一次函数', context_kind: '生活情境', needs_figure: false, local_issues: [], source_question_ids: [9101], status: 'pending', disabled_reason: null, origin: 'demo', revision: 1, source_text: null },
    { id: 'd2', kind: '情境材料', title: '古算折绳测井', situation: '古代算题中用绳索折段测量井深，绳长与井深之间存在差倍数量关系。', section: '1.1 探索勾股定理', chapter: '第一章 勾股定理', context_kind: '数学文化', needs_figure: false, local_issues: [], source_question_ids: [9102], status: 'pending', disabled_reason: null, origin: 'demo', revision: 1, source_text: null },
    { id: 'd3', kind: '情境材料', title: '纯符号运算定义', situation: '定义了一种新运算符号及其运算规则，离开原题定义式后情境不成立。', section: '2.3 二次根式', chapter: '第二章 实数', context_kind: '数学文化', needs_figure: false, local_issues: [], source_question_ids: [9103], status: 'disabled', disabled_reason: '新定义本身即为题目核心，脱离原题后情境不独立存在（演示）。', origin: 'demo', revision: 1, source_text: null }
  ],
  pool: {
    '第一章 勾股定理': [
      { title: '古代折绳测井深', situation: '古代算题情境，用绳索对折与三折分别测量井深，折后余量不同，绳长与井深之间存在数量关系。', context_kind: '数学文化', section: '1.1 探索勾股定理', needs_figure: false },
      { title: '等腰三角形屋架', situation: '房屋人字屋架的两根斜梁与水平横梁构成三角形结构，横梁上若干拉杆与斜梁长度满足勾股关系。', context_kind: '生活情境', section: '1.3 勾股定理的应用', needs_figure: true } ],
    '第二章 实数': [
      { title: '正方形地块面积与边长', situation: '规划一块正方形地块，已知面积求围栏边长，涉及开平方与无理数估算。', context_kind: '生活情境', section: '2.4 估算', needs_figure: false } ],
    '第三章 位置与坐标': [
      { title: '景区导览图定位', situation: '景区导览图用网格坐标标注景点与出入口位置，游览路线可用坐标序列表示。', context_kind: '生活情境', section: '3.1 确定位置', needs_figure: true },
      { title: '海上搜救方位通报', situation: '海上搜救以观测点为原点建立坐标系，遇险船只用方位角与距离报告位置。', context_kind: '科学跨学科', section: '3.1 确定位置', needs_figure: false } ],
    '第四章 一次函数': [
      { title: '阶梯电价计费', situation: '居民用电按月用电量分档计价，各档单价不同，电费与用电量构成分段函数关系。', context_kind: '生活情境', section: '4.4 一次函数的应用', needs_figure: false },
      { title: '手机套餐计费比较', situation: '两种手机套餐分别按固定月租加通话时长、阶梯流量计费，比较不同用量下的费用关系。', context_kind: '生活情境', section: '4.4 一次函数的应用', needs_figure: false },
      { title: '弹簧伸长与挂重', situation: '物理实验中弹簧悬挂砝码，弹簧伸长量与所挂砝码质量在弹性限度内成正比例关系。', context_kind: '科学跨学科', section: '4.3 一次函数的图象', needs_figure: false } ],
    '第五章 二元一次方程组': [
      { title: '鸡兔同笼', situation: '笼中鸡兔同养，只知总头数与总足数，求鸡兔各有多少，属传统筹算问题。', context_kind: '数学文化', section: '5.3 二元一次方程组的应用', needs_figure: false },
      { title: '团体购票优惠方案', situation: '景区对成人票与学生票分别定价，团体达到一定人数可打折，涉及两种票价与人数的方程组。', context_kind: '生活情境', section: '5.3 二元一次方程组的应用', needs_figure: false } ],
    '第六章 数据的分析': [
      { title: '运动会评分权重', situation: '运动会团体总分由若干项目按不同权重加权合成，涉及加权平均的计算与比较。', context_kind: '生活情境', section: '6.1 平均数与方差', needs_figure: false },
      { title: '两班成绩波动比较', situation: '比较两个班级同一测试成绩的集中程度，平均数相近时用方差衡量波动大小。', context_kind: '生活情境', section: '6.1 平均数与方差', needs_figure: false } ],
    '第七章 证明': [
      { title: '拼图验证勾股关系', situation: '用四个全等直角三角形拼成正方形，通过面积关系验证勾股定理，属经典证明情境。', context_kind: '数学文化', section: '7.1 为什么要证明', needs_figure: true } ]
  }
};
