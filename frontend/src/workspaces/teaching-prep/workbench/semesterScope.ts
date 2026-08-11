import { watch } from 'vue'

import { useCurriculumScopeStore } from '../../../stores/curriculum-scope'
import type { CurriculumVolume as GlobalCurriculumVolume } from '../../../api/question-bank'
import { useTeachingPrepCatalogStore, type CatalogLoadScope } from '../stores/catalog'

/**
 * 全局教学范围（年级/册别）与备课学期的对齐逻辑，从旧六维导航机平移。
 * 只负责学期选择，不再夹带任何页面导航副作用。
 */
export function useTeachingPrepSemesterScope(options: {
  onSemesterMissing?: (message: string) => void
} = {}) {
  const catalog = useTeachingPrepCatalogStore()
  const curriculumScope = useCurriculumScopeStore()

  function semesterForGlobalScope(): { id: string } | null {
    const volume = curriculumScope.selectedVolume
    if (!volume) return null
    const gradeLevel = gradeLevelForVolume(volume)
    const curriculumVolume = volume.semester.includes('上') ? 'first' : 'second'
    const curricula = catalog.curricula
      .filter(item => item.grade_level === gradeLevel && item.volume === curriculumVolume)
      .sort((left, right) => (
        curriculumMatchScore(right, volume) - curriculumMatchScore(left, volume)
        || Number(right.is_active) - Number(left.is_active)
        || right.updated_at.localeCompare(left.updated_at)
      ))
    const curriculum = curricula[0]
    if (!curriculum) return null
    const expectedTerm = curriculumVolume === 'first' ? 'first' : 'second'
    const statusOrder = { active: 0, planning: 1, completed: 2, archived: 3 } as const
    return [...catalog.semesters]
      .filter(item => item.curriculum_id === curriculum.id && item.term === expectedTerm)
      .sort((left, right) => (
        statusOrder[left.status] - statusOrder[right.status]
        || right.updated_at.localeCompare(left.updated_at)
      ))[0] ?? null
  }

  async function syncSemesterForGlobalScope(scope: CatalogLoadScope = 'overview'): Promise<void> {
    const target = semesterForGlobalScope()
    if (!curriculumScope.selectedVolumeId) return
    if (!target) {
      catalog.clearSemesterSelection()
      options.onSemesterMissing?.(
        `备课工作台还没有“${curriculumScope.selectedVolume?.label ?? '所选学期'}”的学期资料。已清空旧学期上下文，请先建立本学期。`,
      )
      return
    }
    if (catalog.selectedSemester?.id === target.id) return
    await catalog.selectSemester(target.id, scope)
  }

  watch(
    () => [curriculumScope.loadState, curriculumScope.selectedVolumeId] as const,
    ([scopeState]) => {
      if (scopeState === 'ready' && catalog.loadState === 'ready') {
        void syncSemesterForGlobalScope()
      }
    },
  )

  return {
    semesterForGlobalScope,
    syncSemesterForGlobalScope,
  }
}

function gradeLevelForVolume(volume: GlobalCurriculumVolume): number {
  const digit = Number(volume.grade.match(/\d+/)?.[0])
  if (Number.isSafeInteger(digit) && digit > 0) return digit
  const labels: Record<string, number> = {
    一: 1, 二: 2, 三: 3, 四: 4, 五: 5, 六: 6,
    七: 7, 八: 8, 九: 9, 十: 10, 十一: 11, 十二: 12,
  }
  const match = Object.entries(labels)
    .sort(([left], [right]) => right.length - left.length)
    .find(([label]) => volume.grade.includes(label))
  return match?.[1] ?? 0
}

function curriculumMatchScore(
  curriculum: { title: string; publisher: string | null; edition_label: string | null },
  volume: GlobalCurriculumVolume,
): number {
  const haystack = `${curriculum.title} ${curriculum.publisher ?? ''} ${curriculum.edition_label ?? ''}`
  return haystack.includes(volume.textbook_version) ? 1 : 0
}
