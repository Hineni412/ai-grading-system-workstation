<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'

import type { ExerciseRegionInput } from '../api/catalog'
import type { ExerciseSuggestion, ReferenceSelectionPayload } from '../api/workbench'
import { teachingPrepWorkbenchApi } from '../api/workbench'
import TeachingPrepDocumentWorkspace from '../components/TeachingPrepDocumentWorkspace.vue'
import TeachingPrepStickyActions from '../components/TeachingPrepStickyActions.vue'
import { useTeachingPrepWorkbenchContext } from '../workbench/context'

const workbench = useTeachingPrepWorkbenchContext()
const selectedLinks = ref<string[]>([])
const selectedCandidates = ref<string[]>([])
const teacherContext = ref('')
const activePreviewUrl = ref<string | null>(null)
const message = ref('先限定本节允许使用的资料，再决定是否让 AI 提出候选题。')
const capacity = ref<Record<string, unknown> | null>(null)
const editedMinutes = reactive<Record<string, number>>({})
const showManualEditor = ref(false)
const manualRegions = ref<Array<ExerciseRegionInput & { key: string; role: 'question' | 'answer' }>>([])
const manualForm = reactive({
  unitId: '',
  role: 'question' as 'question' | 'answer',
  x0: 0.1,
  y0: 0.1,
  x1: 0.9,
  y1: 0.5,
  questionNumber: '',
  contentLabel: '',
  estimatedMinutes: 4,
})

const preflight = computed(() => workbench.referencePreflight.value)
const references = computed(() => preflight.value?.catalog.material_links ?? [])
const suggestions = computed(() => workbench.activeSuggestionRun.value?.suggestions ?? [])
const acceptedSuggestions = computed(() => suggestions.value.filter(item => (
  item.decision === 'accepted' || item.decision === 'modified'
)))
const selectedPack = computed(() => workbench.catalog.resourcePacks.find(
  item => item.id === workbench.catalog.selectedResourcePackId,
) ?? workbench.catalog.resourcePacks[0] ?? null)
const selectedDraft = computed(() => workbench.catalog.lessonDrafts.find(
  item => item.id === workbench.catalog.selectedLessonDraftId,
) ?? workbench.catalog.lessonDrafts[0] ?? null)
const preferences = computed(() => workbench.catalog.teachingPreferences?.payload ?? null)
const canFreeze = computed(() => selectedLinks.value.length > 0)
const manualUnitOptions = computed(() => references.value.flatMap(link => (
  selectedLinks.value.includes(link.link_id) ? link.units : []
)))

watch(preflight, (next) => {
  if (!next) return
  const saved = next.draft?.payload
  selectedLinks.value = saved
    ? saved.material_selections.map(item => item.link_id)
    : next.catalog.material_links.map(item => item.link_id)
  teacherContext.value = saved?.teacher_context ?? ''
  activePreviewUrl.value = next.catalog.material_links[0]?.units[0]?.preview_url ?? null
  if (!manualUnitOptions.value.some(item => item.unit_id === manualForm.unitId)) {
    manualForm.unitId = manualUnitOptions.value[0]?.unit_id ?? ''
  }
}, { immediate: true })

function selectionPayload(): ReferenceSelectionPayload {
  if (!preflight.value || !preferences.value) throw new Error('参考资料或备课偏好尚未载入')
  return {
    material_selections: references.value
      .filter(item => selectedLinks.value.includes(item.link_id))
      .map(item => ({
        link_id: item.link_id,
        start_unit: item.start_unit,
        end_unit: item.end_unit,
        ppt_intent: 'keep' as const,
      })),
    exercise_candidate_ids: selectedCandidates.value,
    question_ids: [],
    assessment_ids: [],
    knowledge_scope: [],
    preparation_preferences: preferences.value,
    class_name: null,
    teacher_context: teacherContext.value.trim() || null,
  }
}

async function saveSelection(): Promise<void> {
  const lessonId = workbench.catalog.selectedLessonId
  if (!lessonId || !preflight.value) return
  workbench.setDirty('参考范围')
  try {
    await teachingPrepWorkbenchApi.saveReferenceDraft(lessonId, {
      expected_revision: preflight.value.draft?.revision ?? null,
      source_state_sha256: preflight.value.source_state_sha256,
      selection: selectionPayload(),
    })
    workbench.setDirty(null)
    message.value = '参考范围已保存。AI 只会看到这些资料与页段。'
    await workbench.refreshCurrentWorkspace()
  } catch {
    message.value = '保存失败，当前勾选仍保留在页面中；请刷新来源后重试。'
  }
}

async function startSuggestions(): Promise<void> {
  await saveSelection()
  const current = workbench.referencePreflight.value
  if (!current?.draft) return
  const snapshot = await teachingPrepWorkbenchApi.freezeReferenceSnapshot(
    current.lesson_node_id,
    `reference-snapshot-${crypto.randomUUID().replaceAll('-', '')}`,
    current.draft.revision,
  )
  const run = await teachingPrepWorkbenchApi.startExerciseSuggestions(
    snapshot.id,
    `exercise-suggestions-${crypto.randomUUID().replaceAll('-', '')}`,
  )
  workbench.watchSuggestionRun(run)
  message.value = '候选识别已开始；不会自动采用题目，也不会读取未勾选资料。'
}

async function cancelSuggestions(): Promise<void> {
  const run = workbench.activeSuggestionRun.value
  if (!run) return
  workbench.watchSuggestionRun(await teachingPrepWorkbenchApi.cancelExerciseSuggestions(run.id))
  message.value = '已取消后续候选识别，已经保存的参考范围仍在。'
}

async function decideSuggestion(
  item: ExerciseSuggestion,
  decision: 'accepted' | 'modified' | 'rejected',
): Promise<void> {
  const updated = await teachingPrepWorkbenchApi.reviewExerciseSuggestion(item.id, {
    expected_revision: item.revision,
    decision,
    teacher_payload: decision === 'modified'
      ? {
          ...(item.teacher_payload ?? item.original_payload),
          estimated_minutes: editedMinutes[item.id]
            ?? item.teacher_payload?.estimated_minutes
            ?? item.original_payload.estimated_minutes,
        }
      : null,
    rejection_reason: decision === 'rejected' ? '教师判断本节不采用' : null,
  })
  const run = workbench.activeSuggestionRun.value
  if (run) {
    workbench.watchSuggestionRun({
      ...run,
      suggestions: run.suggestions.map(current => current.id === updated.id ? updated : current),
    })
  }
}

function addManualRegion(): void {
  if (!manualForm.unitId || manualForm.x1 <= manualForm.x0 || manualForm.y1 <= manualForm.y0) {
    message.value = '人工区域边界无效：结束位置必须大于开始位置。'
    return
  }
  manualRegions.value.push({
    key: crypto.randomUUID(),
    role: manualForm.role,
    material_unit_id: manualForm.unitId,
    crop: {
      x0: manualForm.x0,
      y0: manualForm.y0,
      x1: manualForm.x1,
      y1: manualForm.y1,
    },
  })
  workbench.setDirty('人工候选区域')
  message.value = '人工区域已加入；可以继续添加并调整顺序。'
}

function moveManualRegion(index: number, offset: number): void {
  const target = index + offset
  if (target < 0 || target >= manualRegions.value.length) return
  const [item] = manualRegions.value.splice(index, 1)
  if (item) manualRegions.value.splice(target, 0, item)
}

async function saveManualCandidate(): Promise<void> {
  const questions = manualRegions.value.filter(item => item.role === 'question')
  const answers = manualRegions.value.filter(item => item.role === 'answer')
  if (!questions.length) {
    message.value = '人工候选至少需要一个题目区域。'
    return
  }
  const candidate = await workbench.catalog.saveExerciseCandidate(null, {
    question_number: manualForm.questionNumber.trim() || null,
    content_label: manualForm.contentLabel.trim() || '人工框选候选题',
    difficulty: 'unrated',
    classroom_use: 'guided_practice',
    estimated_minutes: manualForm.estimatedMinutes || null,
    teaching_focus: null,
    teacher_note: '教师在参考范围内人工补充',
    selection_status: 'classroom_candidate',
    answer_status: answers.length ? 'candidate' : 'missing',
    question_regions: questions.map(({ material_unit_id, crop }) => ({ material_unit_id, crop })),
    answer_regions: answers.map(({ material_unit_id, crop }) => ({ material_unit_id, crop })),
  })
  selectedCandidates.value = [...new Set([...selectedCandidates.value, candidate.id])]
  manualRegions.value = []
  showManualEditor.value = false
  workbench.setDirty(null)
  message.value = '人工候选已保存并选中；答案仍是待教师核对状态。'
}

async function freezePack(): Promise<void> {
  const lessonId = workbench.catalog.selectedLessonId
  if (!lessonId || !preferences.value) return
  const candidateIds = acceptedSuggestions.value
    .map(item => item.exercise_candidate_id)
    .filter((id): id is string => Boolean(id))
  await teachingPrepWorkbenchApi.resourcePackPreflight(lessonId, {
    reference_ppt_intents: {},
    selected_material_link_ids: selectedLinks.value,
    selected_exercise_candidate_ids: candidateIds,
  })
  await workbench.catalog.freezeResourcePack({
    class_name: null,
    lesson_type: 'new_lesson',
    teacher_context: teacherContext.value.trim() || null,
    reference_ppt_intents: {},
    question_ids: [],
    assessment_ids: [],
    knowledge_scope: [],
    preparation_preferences: preferences.value,
    selected_material_link_ids: selectedLinks.value,
    selected_exercise_candidate_ids: candidateIds,
  })
  message.value = '授课资源包已冻结。后续草稿只读取这个不可变版本。'
  await workbench.refreshCurrentWorkspace()
}

async function generateDraft(mode: 'local_template' | 'model'): Promise<void> {
  await workbench.catalog.prepareLessonDraft(mode)
  if (mode === 'model' && !workbench.catalog.lessonDraftPreflight?.model_available) {
    message.value = '当前未配置模型，可继续使用本地结构草稿；没有产生调用或费用。'
    return
  }
  await workbench.catalog.generateLessonDraft(mode)
  const draft = workbench.catalog.lessonDrafts[0]
  if (draft) await workbench.catalog.selectLessonDraft(draft)
  message.value = mode === 'model' ? '模型草稿已生成，仍需教师确认。' : '本地结构草稿已生成。'
}

async function previewCapacity(): Promise<void> {
  if (!selectedDraft.value) return
  const payload = structuredClone(selectedDraft.value.payload)
  payload.lesson_flow = payload.lesson_flow.map((item, index) => ({
    ...item,
    suggested_minutes: editedMinutes[`flow-${index}`] ?? item.suggested_minutes,
  }))
  capacity.value = await teachingPrepWorkbenchApi.capacityPreview(selectedDraft.value.id, payload)
  message.value = '容量已重新计算；这次预览没有保存新版本，也没有调用模型。'
}

async function confirmDraft(): Promise<void> {
  if (!selectedDraft.value) return
  const payload = structuredClone(selectedDraft.value.payload)
  payload.lesson_flow = payload.lesson_flow.map((item, index) => ({
    ...item,
    suggested_minutes: editedMinutes[`flow-${index}`] ?? item.suggested_minutes,
  }))
  await workbench.catalog.reviseLessonDraft(selectedDraft.value, payload, true)
  workbench.setDirty(null)
  message.value = '课堂草稿已保存为教师确认版本，可以进入逐页课件计划。'
}

async function createSlidePlan(): Promise<void> {
  if (!selectedDraft.value) return
  await workbench.catalog.createSlidePlan(selectedDraft.value)
  await workbench.openStage('slides')
}
</script>

<template>
  <section class="tp-workspace tp-preparation-workspace">
    <header class="tp-workspace__header">
      <div>
        <p class="tp-eyebrow">本节备课</p>
        <h1 data-workbench-title tabindex="-1">限定来源，确定课堂方案</h1>
        <p>教师先圈定资料池；教辅答案和参考课件都可不提供。没有答案时只保留题目原图，不会自动补答案截图。</p>
      </div>
      <span class="tp-trust-badge">只读所选来源</span>
    </header>

    <TeachingPrepDocumentWorkspace
      title="本节参考范围"
      subtitle="勾选资料版本与页段，点击来源可查看原页。"
      :preview-url="activePreviewUrl"
    >
      <template #rail>
        <p class="tp-eyebrow">允许资料</p>
        <label v-for="item in references" :key="item.link_id" class="tp-check-row">
          <input v-model="selectedLinks" type="checkbox" :value="item.link_id" @change="workbench.setDirty('参考范围')">
          <span><strong>{{ item.material_name }}</strong><small>第 {{ item.start_unit }}—{{ item.end_unit }} 页</small></span>
        </label>
        <button class="tp-button tp-button--secondary" type="button" @click="workbench.openStage('materials')">
          返回资料库核原页
        </button>
      </template>
      <template #toolbar>
        <select aria-label="选择原页预览" @change="activePreviewUrl = ($event.target as HTMLSelectElement).value">
          <option v-for="link in references" :key="link.link_id" :value="link.units[0]?.preview_url">
            {{ link.material_name }} · 第 {{ link.start_unit }} 页
          </option>
        </select>
      </template>
      <template #inspector>
        <h3>发送范围</h3>
        <p>{{ selectedLinks.length }} 份已确认资料。未勾选的资料不会进入候选识别。</p>
        <label class="tp-field"><span>本节补充说明</span><textarea v-model="teacherContext" rows="5" @input="workbench.setDirty('参考范围')" /></label>
        <button class="tp-button tp-button--secondary" type="button" :disabled="!canFreeze" @click="saveSelection">保存参考范围</button>
        <button class="tp-button tp-button--primary" type="button" :disabled="!canFreeze || workbench.activeSuggestionRun.value?.status === 'running'" @click="startSuggestions">
          {{ preflight?.model_available ? '生成 AI 候选题' : '用 Fake/已配置适配器生成候选' }}
        </button>
        <button v-if="workbench.activeSuggestionRun.value?.status === 'running'" class="tp-button tp-button--danger" type="button" @click="cancelSuggestions">取消识别</button>
      </template>
    </TeachingPrepDocumentWorkspace>

    <section class="tp-section-block" aria-labelledby="candidate-title">
      <div class="tp-section-heading">
        <div><p class="tp-eyebrow">候选题审核</p><h2 id="candidate-title">AI 找到什么，教师采用什么</h2></div>
        <span class="tp-status-pill">{{ workbench.activeSuggestionRun.value?.status ?? '尚未生成' }}</span>
      </div>
      <div v-if="!suggestions.length" class="tp-empty-state">
        <strong>还没有候选题</strong><p>可以不选题继续备课，也可以在允许范围内人工框选补充。</p>
      </div>
      <div v-else class="tp-candidate-grid">
        <article v-for="item in suggestions" :key="item.id" class="tp-candidate-card" :class="`is-${item.decision}`">
          <div><span class="tp-source-tag">AI 建议</span><strong>{{ item.original_payload.content_label || item.original_payload.question_number || '未命名题目' }}</strong></div>
          <p>{{ item.original_payload.reason }}</p>
          <p class="tp-muted">题目区域 {{ item.original_payload.question_regions.length }} 个 · 答案候选 {{ item.original_payload.answer_regions.length }} 个</p>
          <label class="tp-field tp-field--inline"><span>预计分钟</span><input v-model.number="editedMinutes[item.id]" type="number" min="0" step="1" :placeholder="String(item.original_payload.estimated_minutes ?? '')"></label>
          <div class="tp-inline-actions">
            <button type="button" @click="decideSuggestion(item, 'accepted')">采用</button>
            <button type="button" @click="decideSuggestion(item, 'modified')">保存人工修正</button>
            <button class="is-danger" type="button" @click="decideSuggestion(item, 'rejected')">不采用</button>
          </div>
        </article>
      </div>
      <button class="tp-button tp-button--secondary" type="button" @click="showManualEditor = !showManualEditor">
        {{ showManualEditor ? '收起人工兜底' : '人工框选 / 键盘录入区域' }}
      </button>
      <div v-if="showManualEditor" class="tp-manual-region-editor">
        <div>
          <label class="tp-field"><span>资料页</span><select v-model="manualForm.unitId"><option v-for="unit in manualUnitOptions" :key="unit.unit_id" :value="unit.unit_id">{{ unit.title || `第 ${unit.unit_index} 页` }}</option></select></label>
          <label class="tp-field"><span>区域类型</span><select v-model="manualForm.role"><option value="question">题目</option><option value="answer">答案候选</option></select></label>
          <div class="tp-coordinate-grid" aria-label="归一化区域边界">
            <label>X 起点<input v-model.number="manualForm.x0" type="number" min="0" max="1" step="0.01"></label>
            <label>Y 起点<input v-model.number="manualForm.y0" type="number" min="0" max="1" step="0.01"></label>
            <label>X 终点<input v-model.number="manualForm.x1" type="number" min="0" max="1" step="0.01"></label>
            <label>Y 终点<input v-model.number="manualForm.y1" type="number" min="0" max="1" step="0.01"></label>
          </div>
          <button type="button" @click="addManualRegion">加入区域</button>
        </div>
        <ol class="tp-region-order">
          <li v-for="(region, index) in manualRegions" :key="region.key">
            <span>{{ region.role === 'question' ? '题目' : '答案' }} {{ index + 1 }}</span>
            <code>{{ region.crop }}</code>
            <button type="button" :disabled="index === 0" @click="moveManualRegion(index, -1)">上移</button>
            <button type="button" :disabled="index === manualRegions.length - 1" @click="moveManualRegion(index, 1)">下移</button>
            <button class="is-danger" type="button" @click="manualRegions.splice(index, 1)">移除</button>
          </li>
        </ol>
        <div>
          <label class="tp-field"><span>题号</span><input v-model="manualForm.questionNumber" type="text"></label>
          <label class="tp-field"><span>内容标签</span><input v-model="manualForm.contentLabel" type="text"></label>
          <label class="tp-field tp-field--inline"><span>预计分钟</span><input v-model.number="manualForm.estimatedMinutes" type="number" min="1" max="90"></label>
          <button class="tp-button tp-button--primary" type="button" @click="saveManualCandidate">保存人工候选</button>
        </div>
      </div>
    </section>

    <section class="tp-section-block">
      <div class="tp-section-heading"><div><p class="tp-eyebrow">课堂草稿与容量</p><h2>从不可变资源包生成可核对方案</h2></div></div>
      <div class="tp-flow-columns">
        <div>
          <p>资源包：{{ selectedPack ? `第 ${selectedPack.version_number} 版` : '尚未冻结' }}</p>
          <div class="tp-inline-actions">
            <button class="tp-button tp-button--primary" type="button" :disabled="!canFreeze" @click="freezePack">冻结本节资源包</button>
            <button type="button" :disabled="!selectedPack" @click="generateDraft('local_template')">生成本地结构草稿</button>
            <button type="button" :disabled="!selectedPack" @click="generateDraft('model')">生成模型草稿</button>
          </div>
        </div>
        <div v-if="selectedDraft" class="tp-capacity-panel">
          <h3>第 {{ selectedDraft.version_number }} 版草稿</h3>
          <article v-for="(claim, index) in selectedDraft.payload.knowledge_objectives" :key="`objective-${index}`" class="tp-draft-claim">
            <strong>{{ claim.text }}</strong>
            <button type="button" :disabled="!claim.citations.length" @click="workbench.openStage('materials')">
              查看 {{ claim.citations.length }} 条原页来源
            </button>
          </article>
          <label v-for="(item, index) in selectedDraft.payload.lesson_flow" :key="`${item.phase}-${index}`" class="tp-field tp-field--inline">
            <span>{{ item.title }}</span><input v-model.number="editedMinutes[`flow-${index}`]" type="number" min="0" :placeholder="String(item.suggested_minutes)" @input="workbench.setDirty('课堂草稿时间')"><small>分钟</small>
          </label>
          <button type="button" @click="previewCapacity">即时计算容量</button>
          <p v-if="capacity" class="tp-capacity-summary">
            计划 {{ capacity.planned_minutes }} / {{ capacity.lesson_minutes }} 分钟；
            {{ capacity.within_capacity ? '容量合适' : `预计超时 ${capacity.overrun_minutes} 分钟` }}
          </p>
          <button type="button" @click="confirmDraft">保存并确认课堂草稿</button>
          <button class="tp-button tp-button--primary" type="button" :disabled="selectedDraft.status !== 'confirmed'" @click="createSlidePlan">需要课件时创建逐页计划</button>
        </div>
      </div>
    </section>

    <TeachingPrepStickyActions :state="workbench.dirtyReason.value ? 'dirty' : 'saved'" :message="message">
      <button class="tp-button tp-button--secondary" type="button" @click="workbench.openStage('materials')">上一步：核资料</button>
      <span class="tp-muted">课件是可选分支；草稿确认后可直接用于上课。</span>
      <button class="tp-button tp-button--primary" type="button" :disabled="!selectedDraft" @click="workbench.openStage('slides')">可选：制作课件</button>
    </TeachingPrepStickyActions>
  </section>
</template>
