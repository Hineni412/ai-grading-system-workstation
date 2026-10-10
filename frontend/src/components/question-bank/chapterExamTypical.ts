import type { ChapterExamSection, ChapterExamSkillRow } from '../../api/question-bank'

/* F 典型题：一型一卡。页面轮播与放大浏览共用同一份卡片次序与键。 */
export interface ChapterExamTypicalCard {
  row: ChapterExamSkillRow
  rank: number
  key: string
  cardKey: string
  sectionId: string
  sectionLabel: string
}

export function examTargetName(row: ChapterExamSkillRow): string {
  return row.name.replace(/^(题型|技能)[·：:]\s*/, '')
}

export function examTargetKind(row: ChapterExamSkillRow): string {
  return row.target_kind === 'type' ? '题型' : '技能'
}

export function typicalCardsOf(section: ChapterExamSection): ChapterExamTypicalCard[] {
  const keyed = section.skills.filter(row => !row.unlinked)
  return keyed
    .filter(row => row.typical.length)
    .map(row => ({
      row,
      rank: keyed.indexOf(row) + 1,
      key: row.key ?? 'unlinked',
      cardKey: `${section.id}::${row.key ?? 'unlinked'}`,
      sectionId: section.id,
      sectionLabel: section.label,
    }))
}
