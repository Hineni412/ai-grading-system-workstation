<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'

import {
  createTrainingFromAssembly,
  defaultPaperRules,
  type AssemblyExportSubmitFormat,
  type AssemblySection,
  type PaperRules,
} from '../../api/assembly'
import { TERMINAL_JOB_STATUSES } from '../../api/jobs'
import { useAssemblyStore } from '../../stores/assembly'
import { useConfirm } from '../../composables/useConfirm'
import { useJobStore } from '../../stores/jobs'
import AppButton from '../design-system/AppButton.vue'
import AssemblyPaperSheet from '../question-assembly/AssemblyPaperSheet.vue'
import { useSheetSections } from '../question-assembly/assembly-sheet'

const route = useRoute()
const router = useRouter()
const assembly = useAssemblyStore()
const { confirm } = useConfirm()
const jobs = useJobStore()
const exportFormat = ref<AssemblyExportSubmitFormat>('docx')

const { editableSections, sheetSections } = useSheetSections()

const currentJob = computed(() => (
  assembly.exportJobId === null ? null : jobs.jobs[assembly.exportJobId] ?? null
))
const currentJobDone = computed(() => (
  currentJob.value !== null && TERMINAL_JOB_STATUSES.has(currentJob.value.status)
))

// ---- 出卷设置（原「这张卷」右栏并入此面板） ----
const practiceRules = computed(() => (
  typeof assembly.draft.practice_rules === 'object' && assembly.draft.practice_rules
    ? assembly.draft.practice_rules
    : null
))
const rules = computed<PaperRules>(() => practiceRules.value ?? defaultPaperRules())
const isTraining = computed(() => rules.value.purpose === 'training')
const targetLabel = computed(() => {
  const kind = assembly.draft.assembly_context?.target_kind
  return kind === 'type' ? '题型' : kind === 'knowledge' ? '知识点' : kind === 'mixed' ? '训练目标' : '技能'
})
const settings = ref(defaultPaperRules())
watch(rules, (value) => { settings.value = { ...value } }, { immediate: true })
const rulesChanged = computed(() => JSON.stringify(settings.value) !== JSON.stringify(rules.value))
const violations = computed(() => assembly.draft.rule_violations ?? [])
const settingsMessage = ref('')
const rulesOpen = ref(false)

function valid(value: PaperRules): boolean {
  return [value.question_count, value.max_questions_per_skill, value.max_written_questions, value.recent_activity_count, value.difficulty_max].every(Number.isInteger)
    && value.question_count >= 1
    && value.max_questions_per_skill >= 1 && value.max_questions_per_skill <= value.question_count
    && value.max_written_questions >= 0 && value.max_written_questions <= value.question_count
    && value.recent_activity_count >= 0
    && value.difficulty_max >= 1 && value.difficulty_max <= 10
    && (value.purpose !== 'training' || (value.question_count >= 8 && value.question_count <= 12))
}

async function saveSettings(): Promise<void> {
  if (!valid(settings.value)) { settingsMessage.value = '请核对设置范围；训练卷题数为 8–12。'; return }
  const ok = await assembly.save({
    ...assembly.draft,
    practice_rules: { ...settings.value },
    assembly_context: assembly.draft.assembly_context,
    title: assembly.draft.title,
  })
  settingsMessage.value = ok ? '出卷设置已保存。' : assembly.message
}

async function restoreDefaults(): Promise<void> {
  settings.value = defaultPaperRules()
  await saveSettings()
}

watch(() => route.query.rules, (value) => {
  if (value !== '1') return
  rulesOpen.value = true
  void nextTick(() => document.getElementById('assembly-rules')?.scrollIntoView({ block: 'start', behavior: 'smooth' }))
}, { immediate: true, flush: 'post' })

// ---- 生成训练卷（训练卷用途时替换导出面板） ----
const generating = ref(false)
const generateMessage = ref('')
const primaryReason = computed(() => (
  assembly.saveState !== 'idle'
    ? assembly.saveState === 'saving' ? '正在保存出卷设置，请稍候。' : assembly.message || '出卷设置尚未保存，请重试。'
    : !valid(settings.value)
    ? '请先核对出卷设置。'
    : rulesChanged.value
      ? '出卷设置尚未保存，请重试。'
      : violations.value[0]?.message
        ?? (isTraining.value && (assembly.selectedQuestionCount < 8 || assembly.selectedQuestionCount > 12)
          ? '训练卷需要 8–12 道题，请继续调整。'
          : '')
))

async function generate(): Promise<void> {
  if (generating.value) return
  generateMessage.value = ''
  generating.value = true
  try {
    if (!await assembly.whenSaved()) {
      generateMessage.value = assembly.message || '出卷设置尚未保存，请重试。'
      return
    }
    if (primaryReason.value) {
      generateMessage.value = primaryReason.value
      return
    }
    const draft = await createTrainingFromAssembly({
      class_ids: assembly.draft.assembly_context?.class_ids ?? [],
      draft_revision: assembly.draft.revision,
      rules: rules.value,
    })
    assembly.rememberTrainingDraft(draft.draft_id)
    await router.push({ path: '/training', query: { mode: 'paper', draft: draft.draft_id } })
  } catch {
    generateMessage.value = '训练卷未生成，请核对试卷与出卷设置后重试。'
  } finally {
    generating.value = false
  }
}

// ---- 分节管理 ----
function addSection(): void {
  const section: AssemblySection = {
    id: `section-${Date.now().toString(36)}`,
    title: `分节 ${editableSections.value.length + 1}`,
    question_ids: [],
  }
  void assembly.replaceSections([...editableSections.value, section])
}

function renameSection(sectionId: string, title: string): void {
  const cleanTitle = title.trim()
  if (!cleanTitle) return
  void assembly.replaceSections(editableSections.value.map((section) => (
    section.id === sectionId ? { ...section, title: cleanTitle } : section
  )))
}

function removeSection(sectionId: string): void {
  void assembly.replaceSections(editableSections.value.filter((section) => section.id !== sectionId))
}

async function deleteRecord(recordId: string): Promise<void> {
  const confirmed = await confirm({
    title: '删除这条导出记录？',
    message: '已下载到其他位置的文件不受影响。',
    confirmLabel: '删除',
    danger: true,
  })
  if (confirmed) await assembly.deleteRecord(recordId)
}
</script>

<template>
  <div class="assembly-editor">
    <p v-if="assembly.message" class="assembly-feedback is-error" role="status">{{ assembly.message }}</p>

    <div class="assembly-editor__workspace">
      <aside class="assembly-editor-panel assembly-editor-panel--settings" aria-labelledby="assembly-settings-title">
        <header class="assembly-panel-heading">
          <div>
            <p class="assembly-kicker">PAPER SETTINGS</p>
            <h2 id="assembly-settings-title">试卷设置</h2>
          </div>
        </header>

        <div class="assembly-settings">
          <label>
            <span>试卷标题</span>
            <input class="app-input"
              :value="assembly.draft.title"
              maxlength="120"
              placeholder="如：九年级函数专项练习"
              @change="assembly.updateSettings({ title: ($event.target as HTMLInputElement).value })"
            >
          </label>
          <label>
            <span>副标题</span>
            <input class="app-input"
              :value="assembly.draft.header_text"
              maxlength="200"
              placeholder="如：限时 45 分钟"
              @change="assembly.updateSettings({ header_text: ($event.target as HTMLInputElement).value })"
            >
          </label>
          <details class="assembly-more-settings"><summary>更多设置</summary>          <label>
            <span>预览版本</span>
            <select class="app-input"
              :value="assembly.draft.preview_mode"
              @change="assembly.updateSettings({
                preview_mode: ($event.target as HTMLSelectElement).value as 'student' | 'teacher',
              })"
            >
              <option value="student">学生版</option>
              <option value="teacher">教师版</option>
            </select>
          </label>
          <label>
            <span>试题排版</span>
            <select class="app-input"
              :value="assembly.draft.layout_mode"
              @change="assembly.updateSettings({
                layout_mode: ($event.target as HTMLSelectElement).value as 'sequential' | 'grouped_by_type' | 'sections',
              })"
            >
              <option value="sequential">按当前顺序</option>
              <option value="grouped_by_type">按题型归组</option>
              <option value="sections">按手动分节</option>
            </select>
          </label>
          <label class="assembly-check">
            <input
              type="checkbox"
              :checked="assembly.draft.include_answer"
              @change="assembly.updateSettings({
                include_answer: ($event.target as HTMLInputElement).checked,
              })"
            >
            <span>导出时包含答案</span>
          </label>
          </details>
        </div>

        <details
          v-if="practiceRules"
          id="assembly-rules"
          class="assembly-rules"
          :open="rulesOpen"
          @toggle="rulesOpen = ($event.target as HTMLDetailsElement).open"
        >
          <summary>出卷设置 <span v-if="rulesChanged" class="assembly-badge">已改动</span><small>{{ rules.question_count }} 题 · 同{{ targetLabel }} ≤ {{ rules.max_questions_per_skill }} · 解答 ≤ {{ rules.max_written_questions }}</small></summary>
          <div class="assembly-rules-grid" @change="saveSettings">
            <label>用途<select v-model="settings.purpose" class="app-input" aria-label="出卷用途"><option value="handout">讲义 · 只打印</option><option value="training">训练卷 · 回收批改</option></select></label>
            <label>每卷题数<input v-model.number="settings.question_count" class="app-input" aria-label="每卷题数" type="number" :min="settings.purpose === 'training' ? 8 : 1" :max="settings.purpose === 'training' ? 12 : undefined"></label>
            <label>难度上限<input v-model.number="settings.difficulty_max" class="app-input" aria-label="难度上限" type="number" min="1" max="10"></label>
            <label>同一{{ targetLabel }}最多<input v-model.number="settings.max_questions_per_skill" class="app-input" :aria-label="`同一${targetLabel}最多`" type="number" min="1" :max="settings.question_count"></label>
            <label>解答题最多<input v-model.number="settings.max_written_questions" class="app-input" aria-label="解答题最多" type="number" min="0" :max="settings.question_count"></label>
            <label>近期原题排除次数<input v-model.number="settings.recent_activity_count" class="app-input" aria-label="近期原题排除次数" type="number" min="0"><small>0 = 不排除</small></label>
          </div>
          <AppButton variant="ghost" size="small" @click="restoreDefaults">恢复默认</AppButton>
          <p class="assembly-rules-note">改动只对这张卷生效</p>
          <p v-if="settingsMessage" class="assembly-rules-note" role="status">{{ settingsMessage }}</p>
        </details>
        <div v-if="practiceRules" class="assembly-rules-checks">
          <p class="is-ok">✓ 题数 {{ assembly.selectedQuestionCount }} / 目标 {{ rules.question_count }}</p>
          <p :class="violations.some((v) => v.code === 'skill') ? 'is-warn' : 'is-ok'">{{ violations.some((v) => v.code === 'skill') ? '!' : '✓' }} 同{{ targetLabel }}最多 {{ rules.max_questions_per_skill }} 道</p>
          <p :class="violations.some((v) => v.code === 'written') ? 'is-warn' : 'is-ok'">{{ violations.some((v) => v.code === 'written') ? '!' : '✓' }} 解答题最多 {{ rules.max_written_questions }} 道</p>
          <p :class="violations.some((v) => v.code === 'difficulty') ? 'is-warn' : 'is-ok'">{{ violations.some((v) => v.code === 'difficulty') ? '!' : '✓' }} 难度不超过 {{ rules.difficulty_max }} 级</p>
          <p :class="violations.some((v) => ['recent', 'similar'].includes(v.code)) ? 'is-warn' : 'is-ok'">{{ violations.some((v) => ['recent', 'similar'].includes(v.code)) ? '!' : '✓' }} 近期原题与相似题检查</p>
        </div>

        <section class="assembly-section-editor">
          <header>
            <div>
              <strong>分节</strong>
            </div>
            <AppButton type="button" variant="ghost" size="small" @click="addSection">添加分节</AppButton>
          </header>
          <div v-if="editableSections.length" class="assembly-section-list">
            <div v-for="section in editableSections" :key="section.id">
              <input class="app-input"
                :value="section.title"
                maxlength="80"
                aria-label="分节名称"
                @change="renameSection(section.id, ($event.target as HTMLInputElement).value)"
              >
              <span>{{ section.question_ids.length }} 题</span>
              <button type="button" aria-label="删除分节" @click="removeSection(section.id)">×</button>
            </div>
          </div>
          <div v-else class="assembly-default-sections"><div v-for="section in sheetSections" :key="section.id"><span>{{ section.title }}</span><small>{{ section.questions.length }} 题</small></div></div>
        </section>

      </aside>

      <main class="assembly-paper" aria-labelledby="assembly-preview-title">
        <header class="assembly-paper__heading">
          <div>
            <p class="assembly-kicker">PAPER</p>
            <h2 id="assembly-preview-title">卷面即顺序 · 拖动题目调整</h2>
          </div>
          <span>{{ assembly.selectedQuestionCount }} 题</span>
        </header>
        <div class="assembly-paper__body">
          <AssemblyPaperSheet variant="page" />
        </div>
      </main>

      <aside class="assembly-editor-panel assembly-editor-panel--export" :aria-labelledby="isTraining ? 'assembly-training-title' : 'assembly-export-title'">
        <header class="assembly-panel-heading">
          <div>
            <p class="assembly-kicker">{{ isTraining ? 'TRAINING' : 'EXPORT' }}</p>
            <h2 :id="isTraining ? 'assembly-training-title' : 'assembly-export-title'">{{ isTraining ? '生成训练卷' : '导出' }}</h2>
          </div>
          <AppButton v-if="!isTraining" type="button" variant="ghost" size="small" @click="assembly.loadRecords">刷新</AppButton>
        </header>

        <div v-if="isTraining" class="assembly-export-actions">
          <AppButton
            type="button"
            variant="primary"
            :disabled="!assembly.selectedQuestionCount || generating || Boolean(primaryReason)"
            @click="generate"
          >{{ generating ? '正在生成…' : '生成训练卷 →' }}</AppButton>
          <p v-if="primaryReason" class="assembly-rules-note" role="status">{{ primaryReason }}</p>
          <AppButton
            v-if="assembly.trainingDraftId"
            variant="secondary"
            :as="RouterLink"
            :to="{ path: '/training', query: { mode: 'paper', draft: assembly.trainingDraftId } }"
          >继续这张训练卷 →</AppButton>
          <p class="assembly-rules-note">回收批改会调用模型、产生费用，批改前在训练页确认；结果更新这些学生的掌握度，考试分数不受影响。</p>
          <p v-if="generateMessage" class="assembly-feedback is-error" role="status">{{ generateMessage }}</p>
        </div>

        <template v-else>
          <div class="assembly-export-actions">
            <select v-model="exportFormat" class="app-input assembly-export-format" aria-label="导出格式" :disabled="assembly.submitting">
              <option value="docx">Word（方便修改）</option>
              <option value="pdf">PDF（适合打印）</option>
            </select>
            <AppButton
              variant="primary"
              :disabled="!assembly.canExport || assembly.submitting"
              @click="assembly.submitExport(exportFormat)"
            >
              导出 {{ exportFormat === 'pdf' ? 'PDF' : 'Word' }}
            </AppButton>
          </div>

          <div v-if="currentJob" class="assembly-job">
            <div>
              <strong>导出任务 #{{ currentJob.id }}</strong>
              <span>{{ currentJob.status }} · {{ Math.round(currentJob.progress * 100) }}%</span>
            </div>
            <progress :value="currentJob.progress" max="1" />
            <div class="assembly-job__actions">
              <AppButton
                v-if="currentJob.status === 'succeeded' && typeof currentJob.result.download_url === 'string'" variant="primary" :as="'a'" :href="currentJob.result.download_url">
                下载文件
              </AppButton>
              <AppButton v-if="!currentJobDone" variant="secondary" @click="jobs.cancel(currentJob.id)">取消</AppButton>
              <AppButton
                v-if="currentJob.status === 'failed' || currentJob.status === 'cancelled'"
                variant="secondary"
                @click="assembly.retryExport(currentJob.id)"
              >
                重试
              </AppButton>
            </div>
          </div>

          <section class="assembly-record-section">
            <header>
              <strong>导出记录</strong>
              <span>{{ assembly.records.length }} 条</span>
            </header>
            <ul v-if="assembly.records.length" class="assembly-records">
              <li v-for="record in assembly.records" :key="record.id">
                <div>
                  <strong>{{ record.title }}</strong>
                  <span v-if="record.source === 'ai'" class="assembly-record-source">AI 组卷</span>
                  <span>{{ record.question_count }} 题 · {{ record.export_format }}</span>
                  <small>{{ record.filename }}</small>
                </div>
                <div>
                  <AppButton :as="'a'" variant="ghost" size="small" :href="record.download_url">下载</AppButton>
                  <AppButton type="button" variant="ghost" size="small" @click="assembly.restoreRecord(record.id)">恢复</AppButton>
                  <AppButton type="button" class="is-danger" variant="ghost" size="small" @click="deleteRecord(record.id)">删除</AppButton>
                </div>
              </li>
            </ul>
            <p v-else>暂无导出记录。</p>
          </section>
        </template>
      </aside>
    </div>
  </div>
</template>

<style scoped>
.assembly-export-format {
  width: 100%;
  margin-bottom: 8px;
}

.assembly-rules {
  margin-top: 10px;
  border-top: 1px solid var(--color-border-subtle);
  padding: 8px 14px 0;
}

.assembly-rules > summary {
  cursor: pointer;
  font-size: var(--font-size-dense);
  font-weight: var(--font-weight-semibold);
  display: flex;
  flex-wrap: wrap;
  gap: 2px 8px;
  align-items: baseline;
}

.assembly-rules > summary small {
  color: var(--color-text-muted);
  font-weight: var(--font-weight-normal);
  font-size: var(--font-size-caption);
}

.assembly-rules-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 6px 10px;
  margin-top: 8px;
  font-size: var(--font-size-caption);
  color: var(--color-text-secondary);
}

.assembly-rules-grid label { display: grid; gap: 3px; min-width: 0; }

.assembly-rules-grid label:first-child { grid-column: 1 / -1; }

.assembly-rules-grid small { color: var(--color-text-muted); }

.assembly-rules-note {
  font-size: var(--font-size-caption);
  color: var(--color-text-muted);
  margin: 6px 0 0;
}

.assembly-rules-checks {
  border-top: 1px solid var(--color-border-subtle);
  margin-top: 8px;
  padding: 6px 14px 0;
}

.assembly-rules-checks p { margin: 3px 0; font-size: var(--font-size-caption); color: var(--color-text-secondary); }

.assembly-rules-checks p.is-ok { color: var(--color-success); }

.assembly-rules-checks p.is-warn { color: var(--color-warning); }

.assembly-badge {
  background: var(--color-warning-subtle);
  color: var(--color-warning);
  border-radius: 5px;
  font-size: var(--font-size-caption);
  font-weight: var(--font-weight-normal);
  padding: 1px 6px;
}

</style>
