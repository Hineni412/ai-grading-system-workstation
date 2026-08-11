<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'

import AppButton from '../../../components/design-system/AppButton.vue'
import StatePanel from '../../../components/design-system/StatePanel.vue'
import StatusBadge from '../../../components/design-system/StatusBadge.vue'
import type {
  LessonNode,
  SemesterLessonProgressStatus,
  SemesterStatus,
} from '../api/catalog'
import { LESSON_PROGRESS_OPTIONS } from '../progressLabels'
import { useTeachingPrepCatalogStore } from '../stores/catalog'
import { useLessonStatusFeed } from '../workbench/lessonStatus'
import { useTeachingPrepRouteStateContext } from '../workbench/routeContext'

const catalog = useTeachingPrepCatalogStore()
const routeState = useTeachingPrepRouteStateContext()
const statusFeed = useLessonStatusFeed()

const SEMESTER_STATUS_LABELS: Record<SemesterStatus, string> = {
  planning: '规划中',
  active: '进行中',
  completed: '已完成',
  archived: '已归档',
}

const semester = computed(() => catalog.selectedSemester)
const activeLessons = computed(() => catalog.lessonNodes.filter(
  item => item.node_type === 'lesson' && item.is_active,
))
const inactiveLessons = computed(() => catalog.lessonNodes.filter(
  item => item.node_type === 'lesson' && !item.is_active,
))
const showInactiveLessons = ref(false)
const actionMessage = ref('')
const busy = computed(() => catalog.saveState === 'saving')

const semesterSetup = reactive({
  title: '初中数学',
  gradeLevel: 8,
  volume: 'first' as 'first' | 'second' | 'whole_year',
  schoolYear: '2026-2027',
  term: 'first' as 'first' | 'second',
  plannedLessonCount: 60,
})
const semesterSetupOpen = ref(false)
const semesterSetupMessage = ref('')

const lessonSetupOpen = ref(false)
const lessonSetup = reactive({ title: '', durationMinutes: 45 })

onMounted(async () => {
  try {
    await statusFeed.reload()
  } catch {
    actionMessage.value = '课时准备状态暂时没有载入，表格内容不受影响。'
  }
})

function termLabel(term: 'first' | 'second'): string {
  return term === 'first' ? '第一学期' : '第二学期'
}

function cellTone(state: string): 'success' | 'info' | 'warning' | 'danger' | 'neutral' {
  return state === 'ready' ? 'success'
    : state === 'in_progress' ? 'info'
      : state === 'needs_teacher' || state === 'stale' ? 'warning'
        : state === 'failed' ? 'danger'
          : 'neutral'
}

const CELL_STATE_LABELS: Record<string, string> = {
  not_started: '未开始',
  in_progress: '进行中',
  needs_teacher: '待处理',
  ready: '就绪',
  stale: '来源已变化',
  failed: '未完成',
  not_applicable: '不需要',
}

function readiness(lessonId: string) {
  const status = statusFeed.statusFor(lessonId)
  return [
    { key: 'materials', label: '资料', state: status?.cells.materials.status ?? 'not_started' },
    { key: 'slides', label: '改编', state: status?.cells.slides.status ?? 'not_started' },
    {
      key: 'package',
      label: '副本',
      state: status?.latest.pptx_version_id ? 'ready' : 'not_started',
    },
  ] as const
}

async function switchSemester(event: Event): Promise<void> {
  const semesterId = (event.target as HTMLSelectElement).value
  if (!semesterId || semesterId === semester.value?.id) return
  await catalog.selectSemester(semesterId, 'overview')
  await statusFeed.reload()
}

async function setSemesterStatus(status: SemesterStatus): Promise<void> {
  const current = semester.value
  if (!current) return
  if (status === 'archived' && !window.confirm(
    `归档后本学期不再作为当前学期显示，可随时恢复。确定归档「${current.school_year} ${termLabel(current.term)}」吗？`,
  )) return
  try {
    await catalog.updateSemester({
      plannedNewLessonCount: current.planned_new_lesson_count,
      status,
    })
    actionMessage.value = status === 'archived' ? '学期已归档。' : '学期已恢复为进行中。'
  } catch {
    actionMessage.value = catalog.errorMessage || '学期状态没有保存。'
  }
}

async function createSemesterContext(): Promise<void> {
  semesterSetupMessage.value = '正在建立本学期资料容器…'
  try {
    if (catalog.selectedCurriculum) {
      await catalog.createSemester({
        request_token: `semester-${globalThis.crypto.randomUUID().replaceAll('-', '')}`,
        school_year: semesterSetup.schoolYear.trim(),
        term: semesterSetup.term,
        planned_new_lesson_count: semesterSetup.plannedLessonCount,
      })
    } else {
      await catalog.createSemesterWorkspace({
        curriculum: {
          title: semesterSetup.title.trim(),
          grade_level: semesterSetup.gradeLevel,
          volume: semesterSetup.volume,
          publisher: null,
          edition_label: null,
        },
        semester: {
          school_year: semesterSetup.schoolYear.trim(),
          term: semesterSetup.term,
          planned_new_lesson_count: semesterSetup.plannedLessonCount,
        },
      })
    }
    semesterSetupOpen.value = false
    semesterSetupMessage.value = ''
    actionMessage.value = '学期已建立，可以开始新建课时。'
  } catch {
    semesterSetupMessage.value = catalog.errorMessage || '学期没有建立，请检查填写内容。'
  }
}

async function createNewLesson(): Promise<void> {
  const title = lessonSetup.title.trim()
  if (!title) return
  try {
    await catalog.createLesson({
      request_token: `lesson-${globalThis.crypto.randomUUID().replaceAll('-', '')}`,
      parent_id: null,
      node_type: 'lesson',
      title,
      duration_minutes: lessonSetup.durationMinutes || null,
      source_kind: 'teacher',
    })
    lessonSetupOpen.value = false
    lessonSetup.title = ''
    await statusFeed.reload()
    actionMessage.value = `已新建课时「${title}」。`
  } catch {
    actionMessage.value = catalog.errorMessage || '课时没有建立。'
  }
}

async function setProgress(lesson: LessonNode, event: Event): Promise<void> {
  try {
    await catalog.setSemesterLessonProgress(
      lesson.id,
      (event.target as HTMLSelectElement).value as SemesterLessonProgressStatus,
    )
    await statusFeed.reload()
  } catch {
    actionMessage.value = catalog.errorMessage || '授课状态没有保存。'
  }
}

async function renameLesson(lesson: LessonNode): Promise<void> {
  const title = window.prompt('课时名称', lesson.title)?.trim()
  if (!title || title === lesson.title) return
  try {
    await catalog.updateLesson(lesson, { title })
    actionMessage.value = '课时名称已更新。'
  } catch {
    actionMessage.value = catalog.errorMessage || '课时名称没有保存。'
  }
}

async function deactivateLesson(lesson: LessonNode): Promise<void> {
  if (!window.confirm(
    `停用后该课时不再显示在首页课时表，可随时恢复。确定停用「${lesson.title}」吗？`,
  )) return
  try {
    await catalog.updateLesson(lesson, { isActive: false })
    await statusFeed.reload()
    actionMessage.value = `已停用「${lesson.title}」。`
  } catch {
    actionMessage.value = catalog.errorMessage || '课时没有停用。'
  }
}

async function restoreLesson(lesson: LessonNode): Promise<void> {
  try {
    await catalog.updateLesson(lesson, { isActive: true })
    await statusFeed.reload()
    actionMessage.value = `已恢复「${lesson.title}」。`
  } catch {
    actionMessage.value = catalog.errorMessage || '课时没有恢复。'
  }
}
</script>

<template>
  <section class="tp-page" aria-label="备课首页">
    <header class="tp-page-head">
      <div>
        <h1 data-workbench-title tabindex="-1">备课首页</h1>
        <p>查看本学期每一课时的准备情况，点击进入备课。</p>
      </div>
      <AppButton
        v-if="semester"
        variant="primary"
        :disabled="busy"
        @click="lessonSetupOpen = !lessonSetupOpen"
      >
        新建课时
      </AppButton>
    </header>

    <p v-if="actionMessage" class="tp-inline-message" role="status">{{ actionMessage }}</p>

    <section v-if="semester" class="tp-semester-card" aria-label="当前学期">
      <div class="tp-semester-card__meta">
        <h2>
          {{ semester.curriculum_title }} · {{ semester.school_year }} {{ termLabel(semester.term) }}
          <StatusBadge
            :tone="semester.status === 'active' ? 'success' : semester.status === 'archived' ? 'neutral' : 'info'"
            :label="SEMESTER_STATUS_LABELS[semester.status]"
          />
        </h2>
        <p>
          共 {{ semester.active_lesson_count }} 课时 · 已挂资料 {{ semester.material_count }} 份
          · 已授课 {{ semester.taught_lesson_count }} 节
        </p>
      </div>
      <div class="tp-semester-card__actions">
        <label v-if="catalog.semesters.length > 1" class="tp-field tp-field--inline">
          切换学期
          <select :value="semester.id" @change="switchSemester">
            <option v-for="item in catalog.semesters" :key="item.id" :value="item.id">
              {{ item.curriculum_title }} · {{ item.school_year }} {{ termLabel(item.term) }}
              （{{ SEMESTER_STATUS_LABELS[item.status] }}）
            </option>
          </select>
        </label>
        <AppButton
          v-if="semester.status === 'archived'"
          variant="secondary"
          :disabled="busy"
          @click="setSemesterStatus('active')"
        >
          恢复为进行中
        </AppButton>
        <AppButton
          v-else
          variant="ghost"
          :disabled="busy"
          @click="setSemesterStatus('archived')"
        >
          归档学期
        </AppButton>
      </div>
    </section>

    <section v-if="lessonSetupOpen" class="tp-panel" aria-label="新建课时">
      <div class="tp-panel__head"><h2>新建课时</h2></div>
      <div class="tp-panel__body tp-form-line">
        <label class="tp-field">
          课时标题
          <input v-model="lessonSetup.title" data-testid="new-lesson-title" type="text" placeholder="例如：第 6 课时 · 一次函数的应用">
        </label>
        <label class="tp-field">
          时长（分钟）
          <input v-model.number="lessonSetup.durationMinutes" type="number" min="1" max="300">
        </label>
        <AppButton variant="primary" :disabled="busy || !lessonSetup.title.trim()" @click="createNewLesson">
          保存课时
        </AppButton>
        <AppButton variant="ghost" @click="lessonSetupOpen = false">取消</AppButton>
      </div>
    </section>

    <StatePanel
      v-if="!semester && !semesterSetupOpen"
      kind="empty"
      title="还没有本学期"
      description="先建立本学期，再导入资料、新建课时。建立学期只登记资料归属，不会调用 AI。"
    />
    <div v-if="!semester && !semesterSetupOpen" class="tp-empty-actions">
      <AppButton variant="primary" @click="semesterSetupOpen = true">建立本学期</AppButton>
    </div>

    <section v-if="semesterSetupOpen" class="tp-panel" aria-label="建立本学期">
      <div class="tp-panel__head">
        <h2>建立本学期</h2>
        <span class="tp-panel__hint">这一步只建立资料归属，不会调用 AI</span>
      </div>
      <div class="tp-panel__body tp-form-line">
        <label v-if="!catalog.selectedCurriculum" class="tp-field">教材名称<input v-model="semesterSetup.title" type="text"></label>
        <label v-if="!catalog.selectedCurriculum" class="tp-field">年级<input v-model.number="semesterSetup.gradeLevel" type="number" min="1" max="12"></label>
        <label v-if="!catalog.selectedCurriculum" class="tp-field">
          册别
          <select v-model="semesterSetup.volume">
            <option value="first">上册</option><option value="second">下册</option><option value="whole_year">全一册</option>
          </select>
        </label>
        <label class="tp-field">学年<input v-model="semesterSetup.schoolYear" type="text" placeholder="2026-2027"></label>
        <label class="tp-field">
          学期
          <select v-model="semesterSetup.term">
            <option value="first">第一学期</option><option value="second">第二学期</option>
          </select>
        </label>
        <label class="tp-field">计划课时数<input v-model.number="semesterSetup.plannedLessonCount" type="number" min="1" max="300"></label>
        <AppButton variant="primary" :disabled="busy" @click="createSemesterContext">
          {{ busy ? '正在建立…' : '建立本学期' }}
        </AppButton>
        <AppButton variant="ghost" @click="semesterSetupOpen = false">取消</AppButton>
      </div>
      <p v-if="semesterSetupMessage" class="tp-inline-message" role="status">{{ semesterSetupMessage }}</p>
    </section>

    <template v-if="semester">
      <section v-if="activeLessons.length" class="tp-panel" aria-label="课时列表">
        <div class="tp-panel__head">
          <h2>课时</h2>
          <span class="tp-panel__hint">准备度：资料 → 改编 → 副本，全部就绪即可上课</span>
        </div>
        <table class="tp-grid">
          <thead>
            <tr><th class="tp-grid__main">课时</th><th>授课状态</th><th>准备进度</th><th class="tp-grid__actions">操作</th></tr>
          </thead>
          <tbody>
            <tr v-for="lesson in activeLessons" :key="lesson.id">
              <td>
                <div class="tp-cell-main">{{ lesson.title }}</div>
                <div class="tp-cell-sub">{{ lesson.duration_minutes ?? 45 }} 分钟</div>
              </td>
              <td>
                <select
                  aria-label="修改授课状态"
                  :value="statusFeed.statusFor(lesson.id)?.manual_progress ?? 'not_started'"
                  :disabled="busy"
                  @change="setProgress(lesson, $event)"
                >
                  <option v-for="option in LESSON_PROGRESS_OPTIONS" :key="option.value" :value="option.value">
                    {{ option.label }}
                  </option>
                </select>
              </td>
              <td>
                <span class="tp-readiness">
                  <StatusBadge
                    v-for="cell in readiness(lesson.id)"
                    :key="cell.key"
                    :tone="cellTone(cell.state)"
                    :label="`${cell.label}·${CELL_STATE_LABELS[cell.state]}`"
                  />
                </span>
              </td>
              <td>
                <div class="tp-row-actions">
                  <AppButton variant="ghost" @click="routeState.openLesson(lesson.id)">进入备课</AppButton>
                  <AppButton variant="ghost" :disabled="busy" @click="renameLesson(lesson)">重命名</AppButton>
                  <AppButton variant="ghost" :disabled="busy" @click="deactivateLesson(lesson)">停用</AppButton>
                </div>
              </td>
            </tr>
          </tbody>
        </table>
        <div v-if="inactiveLessons.length" class="tp-panel__foot">
          <AppButton variant="ghost" :aria-expanded="showInactiveLessons" @click="showInactiveLessons = !showInactiveLessons">
            {{ showInactiveLessons ? '收起已停用课时' : `查看已停用课时（${inactiveLessons.length}）` }}
          </AppButton>
          <table v-if="showInactiveLessons" class="tp-grid">
            <tbody>
              <tr v-for="lesson in inactiveLessons" :key="lesson.id">
                <td class="tp-grid__main">
                  <div class="tp-cell-main">{{ lesson.title }}</div>
                  <div class="tp-cell-sub">已停用</div>
                </td>
                <td class="tp-grid__actions">
                  <AppButton variant="ghost" :disabled="busy" @click="restoreLesson(lesson)">恢复</AppButton>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>
      <StatePanel
        v-else
        kind="empty"
        title="本学期还没有课时"
        description="点击右上角「新建课时」手工建立，或到资料库导入教材后让 AI 提出课时树建议。"
      />
    </template>
  </section>
</template>
