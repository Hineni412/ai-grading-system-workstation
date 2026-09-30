import { computed, ref, watch, type Ref } from 'vue'
import { useRoute } from 'vue-router'

import {
  resolveReviewItem,
  type ReviewItemLike,
} from '../../api/review'
import type { ScanStudentMatchOption } from '../../api/scan-grading'
import { useResultsCenterStore } from '../../stores/results-center'
import { useReviewQueueStore } from '../../stores/review-queue'
import { useSessionStore } from '../../stores/session'
import { entryQuery, positiveIntegerQuery } from './review-route'
import type { StudentStripEntry } from './ReviewStudentStrip.vue'

export interface StudentNavTarget {
  name: string
  reviewItemId: string
}

export type ReviewMode = 'batch' | 'deep'

// 深查页学生/题目的导航数据：成绩明细入口按当时矩阵顺序切换同题学生，
// 其他入口退回当前题目队列的相邻答卷。
export function useReviewStudentNav(options: {
  mode: Ref<ReviewMode>
  openStudentQuestion: (questionId: string, reviewItemId: string) => Promise<void>
}) {
  const route = useRoute()
  const sessionStore = useSessionStore()
  const reviewStore = useReviewQueueStore()
  const resultsStore = useResultsCenterStore()
  const { mode, openStudentQuestion } = options

  const deepItem = computed(() => mode.value === 'deep' ? reviewStore.currentItem : null)
  const deepItemIndex = computed(() => {
    if (!deepItem.value) return -1
    return reviewStore.filteredItems.findIndex(
      (entry) => entry.review_item_id === deepItem.value?.review_item_id,
    )
  })
  const previousDeepItem = computed(() =>
    deepItemIndex.value > 0
      ? reviewStore.filteredItems[deepItemIndex.value - 1] ?? null
      : null,
  )
  const nextDeepItem = computed(() =>
    deepItemIndex.value >= 0
      ? reviewStore.filteredItems[deepItemIndex.value + 1] ?? null
      : null,
  )

  // 成绩明细跳转过来时按当时矩阵顺序（班级/搜索/状态/排序生效后的行序）切换同题学生。
  const reviewNavIds = computed<number[]>(() => {
    if (entryQuery(route.query) !== 'results') return []
    const nav = resultsStore.reviewNavigation
    return nav !== null && nav.sessionId === sessionStore.selectedSessionId
      ? nav.studentIds
      : []
  })
  const navIndex = computed(() => {
    const id = activeStudentId.value
    return id === null ? -1 : reviewNavIds.value.indexOf(id)
  })

  function navQuestionTarget(index: number): StudentNavTarget | null {
    const data = resultsStore.results
    const questionId = reviewStore.selectedQuestionId
    const studentId = reviewNavIds.value[index]
    if (!data || questionId === null || studentId === undefined) return null
    if (data.session_id !== sessionStore.selectedSessionId) return null
    const student = data.students.find((entry) => entry.student_id === studentId)
    const item = student?.items.find((entry) => entry.question_id === questionId)
    return student && item
      ? { name: student.student_name, reviewItemId: item.review_item_id }
      : null
  }

  function queueNavTarget(item: ReviewItemLike | null): StudentNavTarget | null {
    if (item === null) return null
    const resolved = resolveReviewItem(item)
    return { name: resolved.student_name, reviewItemId: resolved.review_item_id }
  }

  const previousStudentTarget = computed<StudentNavTarget | null>(() => {
    if (reviewNavIds.value.length > 0) {
      if (navIndex.value <= 0) return null
      for (let index = navIndex.value - 1; index >= 0; index -= 1) {
        const target = navQuestionTarget(index)
        if (target !== null) return target
      }
      return null
    }
    return queueNavTarget(previousDeepItem.value)
  })
  const nextStudentTarget = computed<StudentNavTarget | null>(() => {
    if (reviewNavIds.value.length > 0) {
      if (navIndex.value < 0) return null
      for (let index = navIndex.value + 1; index < reviewNavIds.value.length; index += 1) {
        const target = navQuestionTarget(index)
        if (target !== null) return target
      }
      return null
    }
    return queueNavTarget(nextDeepItem.value)
  })
  const studentNavPosition = computed(() => {
    if (reviewNavIds.value.length > 0) {
      return navIndex.value >= 0
        ? `第 ${navIndex.value + 1} / ${reviewNavIds.value.length} 人`
        : null
    }
    return deepItemIndex.value >= 0
      ? `第 ${deepItemIndex.value + 1} / ${reviewStore.filteredItems.length} 人`
      : null
  })
  const studentNav = computed(() => ({
    previousName: previousStudentTarget.value?.name ?? null,
    nextName: nextStudentTarget.value?.name ?? null,
    position: studentNavPosition.value,
  }))

  const requestedStudentId = computed(() => (
    entryQuery(route.query) === 'results'
      ? positiveIntegerQuery(route.query.student)
      : null
  ))
  const activeStudentId = computed<number | null>(() => {
    if (entryQuery(route.query) !== 'results') return null
    const current = deepItem.value
    if (current !== null) {
      const studentId = resolveReviewItem(current).student_id
      if (studentId > 0) return studentId
    }
    return requestedStudentId.value
  })
  const stripStudent = computed(() => {
    if (activeStudentId.value === null) return null
    const data = resultsStore.results
    if (!data || data.session_id !== sessionStore.selectedSessionId) return null
    return data.students.find(
      (student) => student.student_id === activeStudentId.value,
    ) ?? null
  })
  const studentStripEntries = computed<StudentStripEntry[]>(() => {
    const student = stripStudent.value
    const data = resultsStore.results
    if (!student || !data) return []
    const byQuestion = new Map(
      student.items.map((item) => [item.question_id, item]),
    )
    return data.questions.map((question) => ({
      questionId: question.question_id,
      item: byQuestion.get(question.question_id) ?? null,
    }))
  })
  const showStudentStrip = computed(() => (
    mode.value === 'deep'
    && stripStudent.value !== null
    && studentStripEntries.value.length > 0
  ))

  const studentSearchOptions = computed<ScanStudentMatchOption[]>(() => {
    const data = resultsStore.results
    if (!data || data.session_id !== sessionStore.selectedSessionId) return []
    return data.students.map((student) => ({
      id: student.student_id,
      name: student.student_name,
      student_code: student.student_code ?? '',
      class_name: student.class_name,
      pinyin_initials: student.pinyin_initials,
      pinyin_full: student.pinyin_full,
    }))
  })
  const studentSearchNotice = ref('')
  watch(deepItem, () => {
    studentSearchNotice.value = ''
  })

  function openStudentPick(studentId: number | undefined): void {
    studentSearchNotice.value = ''
    if (studentId === undefined) return
    const questionId = reviewStore.selectedQuestionId
    const data = resultsStore.results
    const student = data?.students.find((entry) => entry.student_id === studentId)
    const item = student?.items.find((entry) => entry.question_id === questionId)
    if (!student || !item) {
      studentSearchNotice.value = '该生本题无作答记录'
      return
    }
    void openStudentQuestion(item.question_id, item.review_item_id)
  }

  function openQuestionNav(direction: -1 | 1): void {
    const entries = studentStripEntries.value
    const index = entries.findIndex(
      (entry) => entry.questionId === reviewStore.selectedQuestionId,
    )
    if (index < 0) return
    for (let step = index + direction; step >= 0 && step < entries.length; step += direction) {
      const item = entries[step]!.item
      if (item !== null) {
        void openStudentQuestion(entries[step]!.questionId, item.review_item_id)
        return
      }
    }
  }

  function openStudentNav(direction: -1 | 1): void {
    const target = direction < 0 ? previousStudentTarget.value : nextStudentTarget.value
    const questionId = reviewStore.selectedQuestionId
    if (target === null || questionId === null) return
    void openStudentQuestion(questionId, target.reviewItemId)
  }

  return {
    deepItem,
    deepItemIndex,
    previousDeepItem,
    nextDeepItem,
    reviewNavIds,
    navIndex,
    previousStudentTarget,
    nextStudentTarget,
    studentNav,
    activeStudentId,
    stripStudent,
    studentStripEntries,
    showStudentStrip,
    studentSearchOptions,
    studentSearchNotice,
    openStudentPick,
    openQuestionNav,
    openStudentNav,
  }
}
