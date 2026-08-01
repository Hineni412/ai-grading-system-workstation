<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import { ApiError } from '../../../api/errors'
import {
  modelApprovalApi,
  type ModelOperation,
  type ModelPreview,
} from '../api/modelApproval'
import {
  studentCardApi,
  type FinalStructure,
  type StudentCard,
} from '../api/studentCards'
import { supportApi } from '../api/support'

const props = defineProps<{ sessionToken: string }>()
const emit = defineEmits<{
  activity: []
  locked: [reason?: string]
}>()

const cards = ref<StudentCard[]>([])
const loading = ref(true)
const busy = ref(false)
const rosterBusy = ref(false)
const errorMessage = ref('')
const notice = ref('')
const activeTool = ref<'roster' | 'discussion'>('discussion')
const rosterName = ref('')
const rosterStudentId = ref('')
const rosterClass = ref('')
const rosterBatch = ref('')
const selectedSubjectId = ref('')
const sourceText = ref('')
const followUpAnswer = ref('')
const preview = ref<ModelPreview | null>(null)
const operation = ref<ModelOperation | null>(null)
const modelConfirmOperationId = ref('')
const saveOperationId = ref('')

const portraitSummary = ref('')
const portraitStrengths = ref('')
const portraitNeeds = ref('')
const portraitQuestions = ref('')
const sopTitle = ref('')
const sopSteps = ref('')
const sopReviewDate = ref('')

const selectedCard = computed(() => (
  cards.value.find((card) => card.subject.subject_id === selectedSubjectId.value) ?? null
))
const canSave = computed(() => (
  operation.value?.state === 'succeeded'
  && operation.value.response_kind === 'proposal'
  && portraitSummary.value.trim()
  && sopTitle.value.trim()
  && lines(sopSteps.value).length > 0
))

function report(error: unknown): void {
  if (error instanceof ApiError && error.code === 'vault_locked') {
    emit('locked')
    return
  }
  errorMessage.value = error instanceof ApiError
    ? error.message
    : '学生卡没有完成本次操作，已保存内容没有改变。'
}

function lines(value: string): string[] {
  return value.split(/\r?\n/).map((item) => item.trim()).filter(Boolean)
}

function resetFlow(): void {
  preview.value = null
  operation.value = null
  modelConfirmOperationId.value = ''
  saveOperationId.value = ''
  followUpAnswer.value = ''
  portraitSummary.value = ''
  portraitStrengths.value = ''
  portraitNeeds.value = ''
  portraitQuestions.value = ''
  sopTitle.value = ''
  sopSteps.value = ''
  sopReviewDate.value = ''
}

async function refresh(): Promise<void> {
  try {
    cards.value = await studentCardApi.list(props.sessionToken)
    if (
      !selectedSubjectId.value
      || !cards.value.some((card) => card.subject.subject_id === selectedSubjectId.value)
    ) selectedSubjectId.value = cards.value[0]?.subject.subject_id ?? ''
    if (!cards.value.length) activeTool.value = 'roster'
  } catch (error) {
    report(error)
  } finally {
    loading.value = false
  }
}

function rosterRows(): Array<{
  source_student_id: string
  display_name: string
  class_label: string | null
  source_id_was_provided: boolean
}> {
  const seen = new Set<string>()
  return rosterBatch.value
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean)
    .map((line) => {
      const [displayName = '', sourceStudentId = '', classLabel = ''] = line
        .split(/[\t,，]/)
        .map((value) => value.trim())
      return {
        source_student_id: sourceStudentId || `manual-${globalThis.crypto.randomUUID()}`,
        display_name: displayName,
        class_label: classLabel || rosterClass.value.trim() || null,
        source_id_was_provided: Boolean(sourceStudentId),
      }
    })
    .filter((item) => {
      if (!item.display_name) return false
      const key = item.source_id_was_provided
        ? `id\u0000${item.source_student_id}`
        : `name\u0000${item.display_name}\u0000${item.class_label ?? ''}`
      if (seen.has(key)) return false
      seen.add(key)
      return true
    })
}

function isExistingStudent(
  sourceStudentId: string,
  displayName: string,
  classLabel: string | null,
  sourceIdWasProvided: boolean,
): boolean {
  return cards.value.some((card) => (
    sourceIdWasProvided
      ? card.subject.source_student_id === sourceStudentId
      : card.subject.display_name === displayName
        && (card.subject.class_label ?? '') === (classLabel ?? '')
  ))
}

async function addRosterStudent(): Promise<void> {
  if (!rosterName.value.trim()) return
  rosterBusy.value = true
  errorMessage.value = ''
  notice.value = ''
  const input = {
    source_student_id: rosterStudentId.value.trim() || `manual-${globalThis.crypto.randomUUID()}`,
    display_name: rosterName.value.trim(),
    class_label: rosterClass.value.trim() || null,
  }
  if (isExistingStudent(
    input.source_student_id,
    input.display_name,
    input.class_label,
    Boolean(rosterStudentId.value.trim()),
  )) {
    errorMessage.value = rosterStudentId.value.trim()
      ? '名单中已有相同校内编号的学生，本次没有重复添加。'
      : '名单中已有同名、同班级且未填写编号的学生；如为同名学生，请填写不同校内编号。'
    rosterBusy.value = false
    return
  }
  try {
    await supportApi.createSubject(props.sessionToken, input)
    rosterName.value = ''
    rosterStudentId.value = ''
    await refresh()
    notice.value = '学生已加入本机加密名单。'
    emit('activity')
  } catch (error) {
    report(error)
  } finally {
    rosterBusy.value = false
  }
}

async function importRoster(): Promise<void> {
  const rows = rosterRows().filter((item) => !isExistingStudent(
    item.source_student_id,
    item.display_name,
    item.class_label,
    item.source_id_was_provided,
  ))
  if (!rows.length) {
    errorMessage.value = '没有可新增的学生；请检查每行姓名，或确认是否已在名单中。'
    return
  }
  rosterBusy.value = true
  errorMessage.value = ''
  notice.value = ''
  let imported = 0
  try {
    for (const row of rows) {
      await supportApi.createSubject(props.sessionToken, {
        source_student_id: row.source_student_id,
        display_name: row.display_name,
        class_label: row.class_label,
      })
      imported += 1
    }
    rosterBatch.value = ''
    await refresh()
    notice.value = `已将 ${imported} 名学生加入本机加密名单。`
    emit('activity')
  } catch (error) {
    await refresh()
    report(error)
    if (imported) notice.value = `已加入 ${imported} 名学生；其余内容未继续写入，请核对后重试。`
  } finally {
    rosterBusy.value = false
  }
}

async function preparePreview(text = sourceText.value): Promise<void> {
  if (!selectedSubjectId.value || !text.trim()) return
  busy.value = true
  errorMessage.value = ''
  notice.value = ''
  preview.value = null
  operation.value = null
  modelConfirmOperationId.value = ''
  saveOperationId.value = ''
  try {
    preview.value = await modelApprovalApi.prepare(
      props.sessionToken,
      text.trim(),
      selectedSubjectId.value,
    )
    modelConfirmOperationId.value = globalThis.crypto.randomUUID()
    emit('activity')
  } catch (error) {
    report(error)
  } finally {
    busy.value = false
  }
}

async function confirmSend(): Promise<void> {
  if (!preview.value?.model_enabled) return
  if (!modelConfirmOperationId.value) {
    modelConfirmOperationId.value = globalThis.crypto.randomUUID()
  }
  busy.value = true
  errorMessage.value = ''
  try {
    operation.value = await modelApprovalApi.confirm(
      props.sessionToken,
      preview.value,
      modelConfirmOperationId.value,
    )
    if (operation.value.response_kind === 'proposal') loadProposal(operation.value)
    emit('activity')
  } catch (error) {
    report(error)
  } finally {
    busy.value = false
  }
}

async function answerFollowUp(): Promise<void> {
  if (!followUpAnswer.value.trim()) return
  const questions = operation.value?.follow_up_questions ?? []
  const questionText = questions
    .map((question, index) => `${index + 1}. ${question}`)
    .join('\n')
  const combined = [
    sourceText.value.trim(),
    questionText ? `AI 追问：\n${questionText}` : '',
    `教师回答：${followUpAnswer.value.trim()}`,
  ].filter(Boolean).join('\n')
  sourceText.value = combined
  followUpAnswer.value = ''
  await preparePreview(combined)
}

function loadProposal(value: ModelOperation): void {
  saveOperationId.value = globalThis.crypto.randomUUID()
  const proposal = value.proposal ?? {}
  const portrait = proposal.portrait && typeof proposal.portrait === 'object'
    ? proposal.portrait as Record<string, unknown>
    : proposal
  const sop = proposal.sop && typeof proposal.sop === 'object'
    ? proposal.sop as Record<string, unknown>
    : {}
  portraitSummary.value = String(portrait.summary ?? value.draft_text ?? '')
  portraitStrengths.value = listText(portrait.strengths)
  portraitNeeds.value = listText(portrait.needs)
  portraitQuestions.value = listText(portrait.open_questions)
  sopTitle.value = String(sop.title ?? '')
  sopSteps.value = listText(sop.steps)
  sopReviewDate.value = String(sop.review_date ?? '')
}

function listText(value: unknown): string {
  return Array.isArray(value) ? value.map(String).join('\n') : ''
}

async function saveFinalStructure(): Promise<void> {
  if (!canSave.value || !operation.value || !selectedSubjectId.value) return
  if (!saveOperationId.value) saveOperationId.value = globalThis.crypto.randomUUID()
  busy.value = true
  errorMessage.value = ''
  const structure: FinalStructure = {
    portrait: {
      summary: portraitSummary.value.trim(),
      strengths: lines(portraitStrengths.value),
      needs: lines(portraitNeeds.value),
      open_questions: lines(portraitQuestions.value),
    },
    sop: {
      title: sopTitle.value.trim(),
      steps: lines(sopSteps.value),
      review_date: sopReviewDate.value || null,
    },
  }
  try {
    const entry = await studentCardApi.confirm(
      props.sessionToken,
      selectedSubjectId.value,
      operation.value.operation_id,
      structure,
      saveOperationId.value,
    )
    await refresh()
    resetFlow()
    sourceText.value = ''
    notice.value = entry.projection_state === 'applied'
      ? '学生卡已保存，普通工作图只收到匿名任务与 SOP 引用。'
      : '学生卡已保存；匿名投影将在下次读取时继续恢复。'
    emit('activity')
  } catch (error) {
    report(error)
  } finally {
    busy.value = false
  }
}

onMounted(() => {
  void refresh()
})
</script>

<template>
  <section class="student-workspace" aria-labelledby="student-workspace-title">
    <header class="student-workspace__heading">
      <div>
        <p class="section-kicker">敏感事项 · 仅在解锁期间读取</p>
        <h2 id="student-workspace-title">班级名单与学生支持</h2>
        <p>先维护本机加密名单，再选择学生与 AI 讨论；教师确认后才形成当前结构化学生摘要。</p>
      </div>
      <span class="local-badge">本机加密</span>
    </header>

    <nav class="student-tool-switch" aria-label="学生敏感区功能">
      <button
        type="button"
        :class="{ 'is-active': activeTool === 'roster' }"
        @click="activeTool = 'roster'"
      >
        班级学生名单
      </button>
      <button
        type="button"
        :class="{ 'is-active': activeTool === 'discussion' }"
        :disabled="!cards.length"
        @click="activeTool = 'discussion'"
      >
        与 AI 讨论学生情况
      </button>
    </nav>

    <p v-if="notice" class="notice notice--success" role="status">{{ notice }}</p>
    <p v-if="errorMessage" class="notice notice--danger" role="alert">{{ errorMessage }}</p>

    <section v-if="activeTool === 'roster'" class="roster-panel" aria-labelledby="roster-title">
      <header>
        <div>
          <p class="section-kicker">名单配置</p>
          <h3 id="roster-title">一个学生一张加密身份卡</h3>
        </div>
        <span>{{ cards.length }} 名学生</span>
      </header>
      <div class="roster-layout">
        <form class="roster-form" @submit.prevent="addRosterStudent">
          <label><span>姓名</span><input v-model="rosterName" maxlength="80" placeholder="例如：张同学"></label>
          <label><span>校内编号（可不填）</span><input v-model="rosterStudentId" maxlength="120" placeholder="学号或系统编号"></label>
          <label><span>班级（可不填）</span><input v-model="rosterClass" maxlength="80" placeholder="例如：九年级 1 班"></label>
          <button type="submit" :disabled="rosterBusy || !rosterName.trim()">
            {{ rosterBusy ? '正在保存…' : '加入名单' }}
          </button>
        </form>
        <form class="roster-batch" @submit.prevent="importRoster">
          <label>
            <span>批量粘贴名单</span>
            <textarea
              v-model="rosterBatch"
              rows="5"
              placeholder="每行一名：姓名，学号，班级&#10;也可以只写姓名；上方班级会作为默认班级"
            />
          </label>
          <button type="submit" :disabled="rosterBusy || !rosterBatch.trim()">批量加入名单</button>
        </form>
      </div>
      <div v-if="cards.length" class="roster-chips" aria-label="当前学生名单">
        <button
          v-for="card in cards"
          :key="card.subject.subject_id"
          type="button"
          @click="selectedSubjectId = card.subject.subject_id; activeTool = 'discussion'; resetFlow()"
        >
          <strong>{{ card.subject.display_name }}</strong>
          <small>{{ card.subject.class_label || '班级未记录' }}</small>
        </button>
      </div>
      <p v-else-if="!loading" class="empty-line">名单为空。手动添加一名，或一次粘贴整班名单。</p>
    </section>

    <template v-else>

    <p v-if="loading" class="empty-line">正在读取加密学生卡…</p>
    <p v-else-if="!cards.length" class="empty-line">
      当前没有已建立的学生身份卡。这里不会为了展示效果虚构学生。
    </p>
    <div v-else class="student-cards">
      <article v-for="card in cards" :key="card.subject.subject_id" class="student-card">
        <div class="student-card__title">
          <div>
            <strong>{{ card.subject.display_name }}</strong>
            <small>{{ card.subject.class_label || '班级未记录' }}</small>
          </div>
          <span>{{ card.entries.length }} 条确认记录</span>
        </div>

        <div v-if="!card.entries.length" class="student-card__empty">
          <dl>
            <div><dt>教师原话</dt><dd>—</dd></div>
            <div><dt>当前学生摘要</dt><dd>—</dd></div>
            <div><dt>SOP</dt><dd>—</dd></div>
          </dl>
          <p v-if="card.existing_records.length || card.support_plans.length">
            已有 {{ card.existing_records.length }} 条支持记录、{{ card.support_plans.length }} 个支持计划；
            新卡不会覆盖旧数据。
          </p>
        </div>

        <section v-for="entry in card.entries" :key="entry.entry_id" class="card-entry">
          <dl>
            <div><dt>教师原话</dt><dd>{{ entry.teacher_quote }}</dd></div>
            <div><dt>当前学生摘要</dt><dd>{{ entry.portrait.summary }}</dd></div>
            <div>
              <dt>SOP</dt>
              <dd>
                <strong>{{ entry.sop.title }}</strong>
                <ol><li v-for="step in entry.sop.steps" :key="step">{{ step }}</li></ol>
              </dd>
            </div>
          </dl>
          <small>{{ entry.projection_state === 'applied' ? '匿名引用已同步' : '匿名引用待恢复' }}</small>
        </section>
      </article>
    </div>

    <form v-if="cards.length" class="student-capture" @submit.prevent="preparePreview()">
      <label>
        <span>学生</span>
        <select v-model="selectedSubjectId" @change="resetFlow">
          <option v-for="card in cards" :key="card.subject.subject_id" :value="card.subject.subject_id">
            {{ card.subject.display_name }}{{ card.subject.class_label ? ` · ${card.subject.class_label}` : '' }}
          </option>
        </select>
      </label>
      <label class="student-capture__text">
        <span>新增一条教师记录</span>
        <textarea
          v-model="sourceText"
          rows="2"
          maxlength="4000"
          placeholder="写下教师观察或学生原话"
          @input="resetFlow"
        />
      </label>
      <button type="submit" :disabled="busy || !selectedCard || !sourceText.trim()">
        {{ busy ? '正在本机匿名化…' : '生成匿名发送预览' }}
      </button>
    </form>

    <section v-if="preview" class="model-preview" aria-label="模型发送确认">
      <header>
        <div><p class="section-kicker">实际发送内容</p><h3>请逐字核对匿名预览</h3></div>
        <span>{{ preview.model_name }}</span>
      </header>
      <dl>
        <div><dt>学生标识</dt><dd>{{ preview.exact_payload.student_alias }}</dd></div>
        <div><dt>实际正文</dt><dd>{{ preview.exact_payload.task_text }}</dd></div>
        <div><dt>模型限制</dt><dd>{{ preview.exact_payload.instructions }}</dd></div>
        <div><dt>已移除</dt><dd>{{ preview.removed_categories.join('、') || '无' }}</dd></div>
      </dl>
      <p>确认后最多发生 1 次物理请求；结果不明时不会自动重试。</p>
      <button type="button" :disabled="busy || !preview.model_enabled" @click="confirmSend">
        {{ preview.model_enabled ? '确认发送这份内容' : '真实模型未启用，不会发送或产生费用' }}
      </button>
    </section>

    <section v-if="operation?.response_kind === 'follow_up'" class="follow-up">
      <p class="section-kicker">模型追问 · 尚未形成结论</p>
      <h3>请教师补充必要信息</h3>
      <ul><li v-for="question in operation.follow_up_questions" :key="question">{{ question }}</li></ul>
      <form @submit.prevent="answerFollowUp">
        <textarea v-model="followUpAnswer" rows="3" placeholder="教师补充；下一轮仍会先展示匿名预览" />
        <button type="submit" :disabled="busy || !followUpAnswer.trim()">生成下一轮匿名预览</button>
      </form>
    </section>

    <form
      v-if="operation?.response_kind === 'proposal'"
      class="structure-confirmation"
      @submit.prevent="saveFinalStructure"
    >
      <header>
        <div><p class="section-kicker">教师最终确认</p><h3>核对后才写入学生卡</h3></div>
        <span>模型草稿不能直接落库</span>
      </header>
      <div class="structure-grid">
        <fieldset>
          <legend>当前结构化学生摘要</legend>
          <label><span>摘要</span><textarea v-model="portraitSummary" rows="4" /></label>
          <label><span>优势（每行一项）</span><textarea v-model="portraitStrengths" rows="3" /></label>
          <label><span>待支持事项（每行一项）</span><textarea v-model="portraitNeeds" rows="3" /></label>
          <label><span>待核实问题（每行一项）</span><textarea v-model="portraitQuestions" rows="3" /></label>
        </fieldset>
        <fieldset>
          <legend>SOP</legend>
          <label><span>标题</span><input v-model="sopTitle"></label>
          <label><span>步骤（每行一步）</span><textarea v-model="sopSteps" rows="7" /></label>
          <label><span>复查日期（可空）</span><input v-model="sopReviewDate" type="date"></label>
        </fieldset>
      </div>
      <button type="submit" :disabled="busy || !canSave">教师确认并写入加密学生卡</button>
    </form>
    </template>
  </section>
</template>

<style scoped>
.student-workspace {
  padding: var(--space-5);
  border: var(--border-width) solid var(--color-border-default);
  border-radius: var(--radius-panel);
  background: var(--color-bg-surface);
}

.student-workspace__heading,
.student-card__title,
.model-preview > header,
.structure-confirmation > header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: var(--space-4);
}

.student-workspace h2,
.student-workspace h3,
.student-workspace p {
  margin-top: 0;
}

.student-workspace h2 { margin-bottom: var(--space-1); font-size: var(--font-size-h2); }
.student-workspace__heading p:last-child { color: var(--color-text-secondary); }
.section-kicker { margin-bottom: var(--space-1); color: var(--color-accent-active); font-size: var(--font-size-caption); font-weight: var(--font-weight-semibold); letter-spacing: .08em; }
.local-badge,
.student-card__title > span,
.model-preview > header > span,
.structure-confirmation > header > span { flex: 0 0 auto; padding: var(--space-1) var(--space-2); border-radius: var(--radius-tag); background: var(--color-success-subtle); color: var(--color-success); font-size: var(--font-size-caption); }

.student-tool-switch { display: flex; gap: var(--space-2); margin: var(--space-4) 0; padding-bottom: var(--space-2); border-bottom: var(--border-width) solid var(--color-border-default); }
.student-workspace .student-tool-switch button { min-height: var(--control-height-default); border-color: var(--color-border-default); background: var(--color-bg-surface); color: var(--color-text-secondary); }
.student-workspace .student-tool-switch button.is-active { border-color: var(--color-accent); background: var(--color-accent-subtle); color: var(--color-accent-active); box-shadow: inset 0 -2px 0 var(--color-accent); }

.roster-panel { display: grid; gap: var(--space-4); }
.roster-panel > header { display: flex; align-items: start; justify-content: space-between; gap: var(--space-3); }
.roster-panel > header h3 { margin-bottom: 0; }
.roster-panel > header > span { color: var(--color-text-secondary); font-size: var(--font-size-dense); }
.roster-layout { display: grid; grid-template-columns: minmax(280px, .8fr) minmax(360px, 1.2fr); gap: var(--space-3); }
.roster-form,
.roster-batch { display: grid; align-content: start; gap: var(--space-3); padding: var(--space-4); border: var(--border-width) solid var(--color-border-default); border-radius: var(--radius-control); background: var(--color-bg-subtle); }
.roster-form label,
.roster-batch label { display: grid; gap: var(--space-1); color: var(--color-text-secondary); font-size: var(--font-size-dense); }
.roster-form input,
.roster-batch textarea { width: 100%; }
.roster-chips { display: grid; grid-template-columns: repeat(auto-fill, minmax(150px, 1fr)); gap: var(--space-2); }
.student-workspace .roster-chips button { display: grid; min-width: 0; min-height: 0; gap: 2px; padding: var(--space-2) var(--space-3); border-color: var(--color-border-default); background: var(--color-bg-surface); color: var(--color-text-primary); text-align: left; }
.roster-chips strong,
.roster-chips small { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.roster-chips small { color: var(--color-text-muted); font-weight: var(--font-weight-regular); }

.student-cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(320px, 1fr)); gap: var(--space-3); margin: var(--space-4) 0; }
.student-card { padding: var(--space-4); border: var(--border-width) solid var(--color-border-default); border-radius: var(--radius-panel); background: var(--color-bg-subtle); }
.student-card__title > div { display: grid; gap: var(--space-1); }
.student-card__title small { color: var(--color-text-muted); }
.student-card dl,
.model-preview dl { display: grid; gap: var(--space-2); margin: var(--space-3) 0; }
.student-card dl div,
.model-preview dl div { display: grid; grid-template-columns: 104px minmax(0, 1fr); gap: var(--space-2); }
.student-card dt,
.model-preview dt { color: var(--color-text-muted); font-size: var(--font-size-dense); }
.student-card dd,
.model-preview dd { margin: 0; color: var(--color-text-primary); white-space: pre-wrap; }
.student-card dd ol { margin: var(--space-1) 0 0; padding-inline-start: var(--space-5); }
.student-card__empty { margin-top: var(--space-3); padding: var(--space-3); border: var(--border-width) dashed var(--color-border-default); border-radius: var(--radius-control); }
.student-card__empty p,
.card-entry > small { color: var(--color-text-muted); font-size: var(--font-size-dense); }
.card-entry { margin-top: var(--space-3); padding-top: var(--space-3); border-top: var(--border-width) solid var(--color-border-default); }

.student-capture { display: grid; grid-template-columns: minmax(150px, .45fr) minmax(0, 1.55fr) auto; align-items: end; gap: var(--space-3); padding: var(--space-4); border: var(--border-width) solid var(--color-border-default); border-radius: var(--radius-panel); background: var(--color-bg-subtle); }
.student-capture label,
.structure-confirmation label { display: grid; gap: var(--space-2); color: var(--color-text-primary); font-size: var(--font-size-dense); font-weight: var(--font-weight-medium); }
.student-capture select,
.student-capture textarea,
.structure-confirmation input,
.structure-confirmation textarea,
.follow-up textarea { width: 100%; }

.student-workspace button { min-height: var(--control-height-large); padding: 0 var(--space-4); border: var(--border-width) solid var(--color-accent); border-radius: var(--radius-control); background: var(--color-accent); color: var(--color-bg-surface); cursor: pointer; }
.student-workspace button:disabled { cursor: not-allowed; opacity: var(--opacity-disabled); }
.notice { margin: var(--space-3) 0 0; padding: var(--space-3); border-radius: var(--radius-control); }
.notice--success { background: var(--color-success-subtle); color: var(--color-success); }
.notice--danger { background: var(--color-danger-subtle); color: var(--color-danger); }
.empty-line { padding: var(--space-5); border: var(--border-width) dashed var(--color-border-default); border-radius: var(--radius-control); color: var(--color-text-muted); }

.model-preview,
.follow-up,
.structure-confirmation { margin-top: var(--space-4); padding: var(--space-4); border: var(--border-width) solid var(--color-ai); border-radius: var(--radius-panel); background: var(--color-ai-subtle); }
.model-preview h3,
.follow-up h3,
.structure-confirmation h3 { margin-bottom: 0; }
.model-preview > header > span,
.structure-confirmation > header > span { background: var(--color-ai-subtle); color: var(--color-ai); }
.model-preview > p { color: var(--color-text-secondary); font-size: var(--font-size-dense); }
.follow-up form { display: grid; gap: var(--space-3); }
.structure-grid { display: grid; grid-template-columns: 1fr 1fr; gap: var(--space-4); margin: var(--space-4) 0; }
.structure-grid fieldset { display: grid; gap: var(--space-3); margin: 0; padding: var(--space-4); border: var(--border-width) solid var(--color-border-default); border-radius: var(--radius-control); background: var(--color-bg-surface); }

@media (max-width: 820px) {
  .student-capture,
  .structure-grid,
  .roster-layout { grid-template-columns: 1fr; }
}
</style>
