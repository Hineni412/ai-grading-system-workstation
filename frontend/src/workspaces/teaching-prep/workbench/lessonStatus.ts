import { computed, ref, type ComputedRef } from 'vue'

import { useTeachingPrepCatalogStore } from '../stores/catalog'
import {
  teachingPrepWorkbenchApi,
  type LessonPreparationStatus,
} from '../api/workbench'

export function hasFormalLessonTree(
  nodes: Array<{ node_type: string; is_active: boolean }>,
): boolean {
  return nodes.some(node => node.node_type === 'lesson' && node.is_active)
}

/**
 * 课时准备状态摘要（资料/改编/副本三格）的只读数据源。
 * 备课首页与课时页各自创建实例，互不影响。
 */
export function useLessonStatusFeed() {
  const catalog = useTeachingPrepCatalogStore()
  const lessonStatuses = ref<LessonPreparationStatus[]>([])

  const statusByLessonId: ComputedRef<Map<string, LessonPreparationStatus>> = computed(
    () => new Map(lessonStatuses.value.map(item => [item.lesson_node_id, item])),
  )

  async function reload(): Promise<void> {
    const semesterId = catalog.selectedSemester?.id
    lessonStatuses.value = semesterId
      ? await teachingPrepWorkbenchApi.lessonStatuses(semesterId)
      : []
  }

  function statusFor(lessonId: string): LessonPreparationStatus | null {
    return statusByLessonId.value.get(lessonId) ?? null
  }

  return {
    lessonStatuses,
    statusByLessonId,
    statusFor,
    reload,
  }
}
