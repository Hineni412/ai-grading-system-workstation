import { computed, watch } from 'vue'

import { useCurriculumScopeStore } from '../../../stores/curriculum-scope'
import type { CurriculumVolume as GlobalCurriculumVolume } from '../../../api/question-bank'
import { useTeachingPrepCatalogStore, type CatalogLoadScope } from '../stores/catalog'
import type { CurriculumEdition, TeachingSemester } from '../api/catalog'

export interface PrepSemesterKeys {
  gradeLevel: number
  volume: 'first' | 'second'
  term: 'first' | 'second'
  title: string
  publisher: string | null
}

/**
 * 全局教学学期是备课学期的唯一开关。
 * 只有 TeachingPrepHomeView 打开同步监听，避免子页面重复切换学期。
 */
export function useTeachingPrepSemesterScope(options: {
  enableSyncWatch?: boolean
  loadScope?: () => CatalogLoadScope
} = {}) {
  const catalog = useTeachingPrepCatalogStore()
  const curriculumScope = useCurriculumScopeStore()

  const hasGlobalVolume = computed(() => Boolean(curriculumScope.selectedVolumeId))
  const canImportMaterials = computed(() => (
    Boolean(curriculumScope.selectedVolumeId && catalog.selectedSemester)
  ))

  function semesterForGlobalScope(): TeachingSemester | null {
    const volume = curriculumScope.selectedVolume
    if (!volume) return null
    return matchPrepSemester(catalog.curricula, catalog.semesters, volume)
  }

  async function syncSemesterForGlobalScope(scope?: CatalogLoadScope): Promise<void> {
    if (catalog.saveState === 'saving') return
    const loadScope = scope ?? options.loadScope?.() ?? 'overview'
    if (!curriculumScope.selectedVolumeId) {
      if (catalog.selectedSemester) catalog.clearSemesterSelection()
      return
    }
    const target = semesterForGlobalScope()
    if (!target) {
      if (catalog.selectedSemester || catalog.selectedCurriculum) {
        catalog.clearSemesterSelection()
      }
      return
    }
    if (catalog.selectedSemester?.id === target.id) return
    await catalog.selectSemester(target.id, loadScope)
  }

  async function createSemesterForGlobalVolume(input: {
    schoolYear: string
    plannedLessonCount: number
  }): Promise<void> {
    const volume = curriculumScope.selectedVolume
    if (!volume) throw new Error('请先在顶部选择教学学期')
    const keys = prepKeysForVolume(volume)
    if (keys.gradeLevel < 7 || keys.gradeLevel > 9) {
      throw new Error('当前教学学期的年级不在初中备课范围内。')
    }
    const schoolYear = input.schoolYear.trim()
    if (!schoolYear) throw new Error('请填写学年')
    const existing = semesterForGlobalScope()
    if (existing) {
      await catalog.selectSemester(existing.id, options.loadScope?.() ?? 'overview')
      return
    }
    const curriculum = matchingCurricula(catalog.curricula, keys)[0] ?? null
    if (curriculum) {
      catalog.selectedCurriculumId = curriculum.id
      await catalog.createSemester({
        request_token: `semester-${globalThis.crypto.randomUUID().replaceAll('-', '')}`,
        school_year: schoolYear,
        term: keys.term,
        planned_new_lesson_count: input.plannedLessonCount,
      })
      return
    }
    await catalog.createSemesterWorkspace({
      curriculum: {
        title: keys.title,
        grade_level: keys.gradeLevel,
        volume: keys.volume,
        publisher: keys.publisher,
        edition_label: keys.publisher,
      },
      semester: {
        school_year: schoolYear,
        term: keys.term,
        planned_new_lesson_count: input.plannedLessonCount,
      },
    })
  }

  if (options.enableSyncWatch) {
    watch(
      () => [
        curriculumScope.loadState,
        curriculumScope.selectedVolumeId,
        catalog.loadState,
        catalog.saveState,
      ] as const,
      ([scopeState, , catalogState, saveState]) => {
        if (scopeState === 'ready' && catalogState === 'ready' && saveState !== 'saving') {
          void syncSemesterForGlobalScope()
        }
      },
    )
  }

  return {
    hasGlobalVolume,
    canImportMaterials,
    semesterForGlobalScope,
    syncSemesterForGlobalScope,
    createSemesterForGlobalVolume,
  }
}

export function prepKeysForVolume(volume: GlobalCurriculumVolume): PrepSemesterKeys {
  const isFirst = volume.semester.includes('上')
  return {
    gradeLevel: gradeLevelForVolume(volume),
    volume: isFirst ? 'first' : 'second',
    term: isFirst ? 'first' : 'second',
    title: volume.label,
    publisher: volume.textbook_version.trim() || null,
  }
}

export function defaultSchoolYear(now = new Date()): string {
  const year = now.getFullYear()
  const start = now.getMonth() + 1 >= 8 ? year : year - 1
  return `${start}-${start + 1}`
}

export function matchPrepSemester(
  curricula: CurriculumEdition[],
  semesters: TeachingSemester[],
  volume: GlobalCurriculumVolume,
): TeachingSemester | null {
  const keys = prepKeysForVolume(volume)
  if (keys.gradeLevel < 7) return null
  const curriculum = matchingCurricula(curricula, keys)[0]
  if (!curriculum) return null
  const statusOrder = { active: 0, planning: 1, completed: 2, archived: 3 } as const
  return [...semesters]
    .filter(item => item.curriculum_id === curriculum.id && item.term === keys.term)
    .sort((left, right) => (
      statusOrder[left.status] - statusOrder[right.status]
      || right.updated_at.localeCompare(left.updated_at)
    ))[0] ?? null
}

function matchingCurricula(
  curricula: CurriculumEdition[],
  keys: PrepSemesterKeys,
): CurriculumEdition[] {
  return curricula
    .filter(item => item.grade_level === keys.gradeLevel && item.volume === keys.volume)
    .sort((left, right) => (
      Number(right.is_active) - Number(left.is_active)
      || right.updated_at.localeCompare(left.updated_at)
    ))
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
