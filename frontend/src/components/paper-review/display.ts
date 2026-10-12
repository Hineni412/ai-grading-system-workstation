import type { PaperDimension, PaperPoint, Quadrant } from '../../api/paper-review'

export const quadrantNames: Record<Quadrant, string> = {
  good: '基础能区分', free: '送分', high: '有难度·能区分', suspect: '可疑',
}
export const quadrantColors: Record<Quadrant, string> = {
  good: '#2f7d5c', free: '#98a4aa', high: '#496579', suspect: '#b3402a',
}
export function pct(value: number | null | undefined, digits = 1): string {
  return value == null ? '数据不足' : `${(value * 100).toFixed(digits)}%`
}
export function displayRate(item: PaperPoint, cls: string): number | null {
  return cls ? item.class_rates[cls] ?? null : item.rate
}
export function dots(r: number | null): number {
  return r == null ? 0 : r >= .4 ? 4 : r >= .3 ? 3 : r >= .2 ? 2 : 1
}
export function band(score: number | null): string {
  return score == null ? 'na' : score >= 80 ? 'good' : score >= 60 ? 'amber' : 'red'
}
const questionTypeNames: Record<string, string> = { choice: '选择题', multiple_choice: '多选题', fill_blank: '填空题' }
export function questionTypeName(type: string): string {
  return questionTypeNames[type] ?? (type.endsWith('题') ? type : '解答题')
}
export function dimLine(d: PaperDimension): string {
  if (d.score == null) return '数据不足，未计入总分'
  switch (d.key) {
    case 'coverage': return d.detail.missing.length ? `漏选更高频：${d.detail.missing[0]?.replace(/^题型·/, '')}${d.detail.missing.length > 1 ? `等${d.detail.missing.length}项` : ''}` : '已选的都是高频题型'
    case 'time_fit': return `中档生 ${d.detail.unfinished}/${d.detail.mid_students} 人疑似没做完`
    case 'distribution': return `均分 ${pct(d.detail.mean, 0)} · 40%以下占 ${pct(d.detail.floor_share, 0)}`
    case 'basic_hits': return `${d.detail.hit_items.length}/${d.detail.basic_items.length} 道基础题考出了问题`
    case 'discrimination': return d.detail.low.length ? `${d.detail.low.length} 题区分偏弱：${d.detail.low.join('、')}` : '每题都能分出强弱'
    case 'independence': {
      const pairs = d.detail.pairs
      return pairs.length ? `${pairs.length} 组同题型作答联动：${pairs[0]!.a}–${pairs[0]!.b}${pairs.length > 1 ? ' 等' : ''}` : '没有发现重复考查'
    }
  }
}
export const rules = [
  '典型题覆盖：每个小节按本卷实际选了几个题型（k 个）计，用所选题型的出现套数之和，比该节最高频 k 个题型的套数之和；常见难度 ≥7 的偏难题型没选不扣分；只作为次要题型考到算一半。',
  '题量与时间：总分居中一半的学生里，末尾连续空白达满分 15% 的人数比例；≤5% 得 100，≥40% 得 0。空白是得 0 分且已有记录明确写未作答或 blank 类。',
  '分数分布：均分率在 60%至80%、标准差在 0.12至0.25、满分段（≥95%）和 40% 以下各不超过 10% 时满分，三项取平均。',
  '易错暴露：题库预估难度 <4 的基础题里，得分率 0.40至0.85 且区分度 ≥0.30 的分值占比。',
  '题目区分：按分值加权，区分度 ≥0.4 记满分，0.3至0.4 记 0.8，0.2至0.3 记 0.4，<0.2 或为空记 0。',
  '题间独立：只看每题都作答的学生，把每道整题总分对其余题得分回归取残差，再两两算相关；相关明显高出全场平均（≥0.20）判为作答联动。联动且题库题型相同的题对按簇合并，簇内除最大分值题以外的分值记重复分；重复分占满分 ≤5% 得 100，≥30% 得 0。',
  '总分：六项按权重加权平均（典型题覆盖 20、题量与时间 10、分数分布 15、易错暴露 30、题目区分 15、题间独立 10）；数据不足的项不计入，权重按剩余项重新分配。',
  '信度 α：整题总分的内部一致性，只显示不计分。',
  '题序倒挂：同题型内靠前的整题明显难于靠后的整题，只显示不计分。',
  '连锁失分：大题内前问 0 分的学生后问也 0 分，前问结果被后问使用时属正常设计，只显示不计分。',
  '题型常见难度：期中期末卷里该题型各题题库难度的中位数，按整题难度，含多问大题时可能偏高。',
  '以上分界线是当前初值，待和老师一起校准。',
]
