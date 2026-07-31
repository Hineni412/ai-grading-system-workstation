<script setup lang="ts">
import { computed, onMounted, ref, toRaw, watch } from 'vue'

import { ApiError } from '../../../api/errors'
import { actionApi, type ActionDashboard, type ActionItem } from '../api/actions'
import {
  collectionApi,
  type CollectionBoard,
  type CollectionStatus,
  type IndividualReminder,
  type MeetingDraft,
  type MeetingInbox,
} from '../api/collections'

const props = defineProps<{
  sessionToken: string
  actionRefreshKey?: number
  actionRefreshReason?: 'action_saved' | 'subject_deleted'
}>()
const emit = defineEmits<{
  activity: []
  confirmed: []
  error: [message: string]
  locked: [message?: string]
}>()

const loading = ref(true)
const saving = ref(false)
const rawMeeting = ref('')
const inbox = ref<MeetingInbox | null>(null)
const boards = ref<CollectionBoard[]>([])
const actions = ref<ActionItem[]>([])
const dashboard = ref<ActionDashboard | null>(null)
const selectedBoardId = ref('')
const boardTitle = ref('')
const boardActionId = ref('')
const boardParticipants = ref('')
const individualPreview = ref<IndividualReminder | null>(null)
const localMessage = ref('')
let actionContextRequestVersion = 0

const selectedBoard = computed(() => (
  boards.value.find((item) => item.board_id === selectedBoardId.value) ?? boards.value[0] ?? null
))

const canConfirmInbox = computed(() => Boolean(
  inbox.value
  && inbox.value.drafts.length
  && inbox.value.drafts.every((draft) => (
    draft.title.trim()
    && draft.final_deadline
    && !draft.sensitive_findings.length
    && draft.actions.length
    && draft.actions.every((action) => action.title.trim() && action.due_at)
  )),
))

const STATUS_LABELS: Record<CollectionStatus, string> = {
  pending_notice: '待通知',
  pending_submission: '待提交',
  submitted: '已提交',
  needs_review: '需核对',
  completed: '已完成',
}

function report(error: unknown): void {
  if (error instanceof ApiError && error.code === 'vault_locked') {
    emit('locked')
    return
  }
  emit(
    'error',
    error instanceof ApiError
      ? error.message
      : '会议收件箱或收集板没有完成本次操作，正式记录没有改变。',
  )
}

function reportActionContextRefresh(
  error: unknown,
  reason: 'action_saved' | 'subject_deleted',
): void {
  const completedFact = reason === 'subject_deleted'
    ? '学生支持数据已经删除，但 B04 行动选项暂时无法重新读取'
    : '行动已经保存，但 B04 等待复查信息暂时无法重新读取'
  if (error instanceof ApiError && error.code === 'vault_locked') {
    emit('locked', `${completedFact}；保险箱已锁定，请重新解锁后核对。`)
    return
  }
  emit('error', `${completedFact}，请稍后重试。`)
}

function toLocalInput(value: string | null): string {
  if (!value) return ''
  const date = new Date(value)
  const offset = date.getTimezoneOffset() * 60_000
  return new Date(date.getTime() - offset).toISOString().slice(0, 16)
}

function toIso(value: string): string | null {
  return value ? new Date(value).toISOString() : null
}

function setDraftDeadline(draft: MeetingDraft, value: string): void {
  draft.final_deadline = toIso(value)
}

function setActionDeadline(action: MeetingDraft['actions'][number], value: string): void {
  action.due_at = toIso(value)
}

function participantsFromInput(): string[] {
  return boardParticipants.value
    .split(/\r?\n/)
    .map((value) => value.trim())
    .filter(Boolean)
}

function applyActionContext(
  loadedActions: ActionItem[],
  loadedDashboard: ActionDashboard,
): void {
  actions.value = loadedActions
  dashboard.value = loadedDashboard
  if (
    boardActionId.value
    && !loadedActions.some((item) => item.action_id === boardActionId.value)
  ) boardActionId.value = loadedActions[0]?.action_id ?? ''
  if (!boardActionId.value && loadedActions[0]) {
    boardActionId.value = loadedActions[0].action_id
  }
}

async function refresh(): Promise<void> {
  const requestVersion = ++actionContextRequestVersion
  try {
    const [loadedInboxes, loadedBoards, loadedActions, loadedDashboard] = await Promise.all([
      collectionApi.listInboxes(props.sessionToken),
      collectionApi.listBoards(props.sessionToken),
      actionApi.listActions(props.sessionToken),
      actionApi.dashboard(props.sessionToken),
    ])
    inbox.value = loadedInboxes.find((item) => item.status === 'draft') ?? null
    boards.value = loadedBoards
    if (!selectedBoardId.value && loadedBoards[0]) selectedBoardId.value = loadedBoards[0].board_id
    if (requestVersion === actionContextRequestVersion) {
      applyActionContext(loadedActions, loadedDashboard)
    }
  } catch (error) {
    report(error)
  } finally {
    loading.value = false
  }
}

async function refreshActionContext(
  reason: 'action_saved' | 'subject_deleted',
): Promise<void> {
  const requestVersion = ++actionContextRequestVersion
  try {
    const [loadedActions, loadedDashboard] = await Promise.all([
      actionApi.listActions(props.sessionToken),
      actionApi.dashboard(props.sessionToken),
    ])
    if (requestVersion !== actionContextRequestVersion) return
    applyActionContext(loadedActions, loadedDashboard)
  } catch (error) {
    if (requestVersion === actionContextRequestVersion) {
      reportActionContextRefresh(error, reason)
    }
  }
}

async function importMeeting(): Promise<void> {
  if (!rawMeeting.value.trim()) return
  saving.value = true
  localMessage.value = ''
  try {
    inbox.value = await collectionApi.importMeeting(props.sessionToken, rawMeeting.value)
    rawMeeting.value = ''
    emit('activity')
  } catch (error) {
    report(error)
  } finally {
    saving.value = false
  }
}

function splitDraft(index: number): void {
  const current = inbox.value?.drafts[index]
  if (!current || !inbox.value) return
  const clone: MeetingDraft = {
    ...structuredClone(toRaw(current)),
    meeting_draft_id: globalThis.crypto.randomUUID(),
    title: `${current.title}（拆分项）`,
  }
  inbox.value.drafts.splice(index + 1, 0, clone)
}

function removeDraft(index: number): void {
  inbox.value?.drafts.splice(index, 1)
}

function mergeWithNext(index: number): void {
  if (!inbox.value) return
  const first = inbox.value.drafts[index]
  const second = inbox.value.drafts[index + 1]
  if (!first || !second) return
  const sameDeadline = first.final_deadline === second.final_deadline
  const combinedActions = [...first.actions, ...second.actions].map((action, actionIndex) => ({
    ...structuredClone(toRaw(action)),
    draft_action_id: `merged-${actionIndex + 1}`,
    due_at: sameDeadline ? action.due_at : null,
    depends_on_draft_action_ids: actionIndex ? [`merged-${actionIndex}`] : [],
  }))
  inbox.value.drafts.splice(index, 2, {
    ...first,
    title: `${first.title} / ${second.title}`,
    objective: `${first.objective}\n${second.objective}`,
    final_deadline: sameDeadline ? first.final_deadline : null,
    deadline_date: sameDeadline ? first.deadline_date : null,
    deadline_source: sameDeadline ? first.deadline_source : 'merge_conflict',
    actions: combinedActions,
    unknowns: sameDeadline
      ? [...new Set([...first.unknowns, ...second.unknowns])]
      : ['合并前两项截止时间不同，请重新确认最终截止时间和各行动时间'],
    sensitive_findings: [...new Set([
      ...first.sensitive_findings,
      ...second.sensitive_findings,
    ])],
  })
}

async function saveInbox(): Promise<void> {
  if (!inbox.value) return
  saving.value = true
  try {
    inbox.value = await collectionApi.updateInbox(props.sessionToken, inbox.value)
    localMessage.value = '会议任务草稿已保存，仍未进入正式日历。'
    emit('activity')
  } catch (error) {
    report(error)
  } finally {
    saving.value = false
  }
}

async function confirmInbox(): Promise<void> {
  if (!inbox.value || !canConfirmInbox.value) return
  saving.value = true
  try {
    const saved = await collectionApi.updateInbox(props.sessionToken, inbox.value)
    await collectionApi.confirmInbox(props.sessionToken, saved)
    inbox.value = null
    localMessage.value = '会议任务已一次写入正式行动；外部请求仍为 0。'
    emit('activity')
    emit('confirmed')
    await refresh()
  } catch (error) {
    report(error)
  } finally {
    saving.value = false
  }
}

async function cancelInbox(): Promise<void> {
  if (!inbox.value) return
  saving.value = true
  try {
    await collectionApi.cancelInbox(props.sessionToken, inbox.value)
    inbox.value = null
    localMessage.value = '会议草稿已取消，原文已从草稿中清除。'
    emit('activity')
  } catch (error) {
    report(error)
  } finally {
    saving.value = false
  }
}

async function createBoard(): Promise<void> {
  const participants = participantsFromInput()
  if (!boardTitle.value.trim() || !boardActionId.value || !participants.length) return
  saving.value = true
  try {
    const created = await collectionApi.createBoard(props.sessionToken, {
      action_id: boardActionId.value,
      title: boardTitle.value,
      participant_refs: participants,
    })
    boards.value = [created, ...boards.value]
    selectedBoardId.value = created.board_id
    boardTitle.value = ''
    boardParticipants.value = ''
    localMessage.value = '匿名收集板已建立；群提醒仍未发送。'
    emit('activity')
  } catch (error) {
    report(error)
  } finally {
    saving.value = false
  }
}

async function updateItem(itemId: string, status: CollectionStatus): Promise<void> {
  const board = selectedBoard.value
  if (!board) return
  saving.value = true
  try {
    const updated = await collectionApi.updateItem(
      props.sessionToken,
      board,
      itemId,
      status,
    )
    boards.value = boards.value.map((item) => (
      item.board_id === updated.board_id ? updated : item
    ))
    emit('activity')
  } catch (error) {
    report(error)
  } finally {
    saving.value = false
  }
}

async function previewIndividual(itemId: string): Promise<void> {
  const board = selectedBoard.value
  if (!board) return
  try {
    individualPreview.value = await collectionApi.individualReminder(
      props.sessionToken,
      board.board_id,
      itemId,
    )
  } catch (error) {
    report(error)
  }
}

onMounted(() => {
  void refresh()
})

watch(
  () => props.actionRefreshKey,
  (current, previous) => {
    if (current !== previous) {
      void refreshActionContext(props.actionRefreshReason ?? 'action_saved')
    }
  },
)
</script>

<template>
  <section class="collection-inbox">
    <header class="collection-inbox__heading">
      <div>
        <p class="section-kicker">B04 · 会议收件箱与收集板</p>
        <h2>先把散落任务夹进来，再逐项确认</h2>
        <p>会议原文、待确认草稿、正式行动和匿名收集状态分别保存，不会混成一份记录。</p>
      </div>
      <div class="inbox-tabs" role="tablist" aria-label="B04 状态">
        <span>会议草稿 {{ inbox ? 1 : 0 }}</span>
        <span>收集板 {{ boards.length }}</span>
        <span>等待复查 {{ dashboard?.waiting.length ?? 0 }}</span>
      </div>
    </header>

    <p v-if="localMessage" class="local-message" role="status">{{ localMessage }}</p>
    <p v-if="loading" class="empty-state">正在读取加密会议草稿和收集板…</p>

    <div v-else class="b04-grid">
      <section class="meeting-pocket">
        <div class="pocket-label">
          <span>会议任务口袋</span>
          <strong>0 次外部请求</strong>
        </div>

        <form v-if="!inbox" class="meeting-import" @submit.prevent="importMeeting">
          <label>
            <span>粘贴会议任务，每行一项</span>
            <textarea
              v-model="rawMeeting"
              rows="5"
              maxlength="20000"
              placeholder="2026-08-14 收齐回执并报年级&#10;2026-08-18 完成活动材料准备"
            />
          </label>
          <button
            class="button button--primary"
            type="submit"
            :disabled="saving || !rawMeeting.trim()"
          >
            本地拆成待确认任务
          </button>
        </form>

        <div v-else class="meeting-drafts">
          <article
            v-for="(draft, index) in inbox.drafts"
            :key="draft.meeting_draft_id"
            class="meeting-card"
          >
            <div class="meeting-card__index">{{ index + 1 }}</div>
            <div class="meeting-card__body">
              <label>
                <span>任务名称</span>
                <input v-model="draft.title" maxlength="240">
              </label>
              <label>
                <span>最终截止时间</span>
                <input
                  :value="toLocalInput(draft.final_deadline)"
                  type="datetime-local"
                  @input="setDraftDeadline(draft, ($event.target as HTMLInputElement).value)"
                >
              </label>
              <label class="meeting-card__wide">
                <span>任务说明</span>
                <textarea v-model="draft.objective" rows="2" maxlength="4000" />
              </label>
              <details class="meeting-card__wide">
                <summary>检查并修改 {{ draft.actions.length }} 个行动</summary>
                <div
                  v-for="action in draft.actions"
                  :key="action.draft_action_id"
                  class="meeting-action"
                >
                  <input v-model="action.title" maxlength="240" aria-label="行动名称">
                  <input
                    :value="toLocalInput(action.due_at)"
                    type="datetime-local"
                    aria-label="行动截止时间"
                    @input="setActionDeadline(action, ($event.target as HTMLInputElement).value)"
                  >
                </div>
              </details>
              <p v-if="draft.sensitive_findings.length" class="card-alert meeting-card__wide">
                普通任务不能确认：{{ draft.sensitive_findings.join('、') }}
              </p>
              <p v-else-if="!draft.final_deadline || draft.actions.some((item) => !item.due_at)" class="card-alert meeting-card__wide">
                请补齐最终截止时间和每个行动时间。
              </p>
              <div class="meeting-card__tools meeting-card__wide">
                <button type="button" @click="splitDraft(index)">拆成两项</button>
                <button
                  type="button"
                  :disabled="index === inbox.drafts.length - 1"
                  @click="mergeWithNext(index)"
                >
                  与下一项合并
                </button>
                <button type="button" @click="removeDraft(index)">删除此项</button>
              </div>
            </div>
          </article>

          <label class="source-choice">
            <input v-model="inbox.delete_source_after_confirm" type="checkbox">
            <span>确认后删除会议原文，只保留结构化任务和正式行动</span>
          </label>
          <div class="meeting-drafts__actions">
            <button class="button button--secondary" type="button" :disabled="saving" @click="saveInbox">
              保存待确认草稿
            </button>
            <button class="button button--primary" type="button" :disabled="saving || !canConfirmInbox" @click="confirmInbox">
              一次写入全部正式行动
            </button>
            <button class="button button--text" type="button" :disabled="saving" @click="cancelInbox">
              取消并清除原文
            </button>
          </div>
        </div>
      </section>

      <aside class="waiting-pocket">
        <p class="section-kicker">等待复查</p>
        <h3>不让“等回复”变成遗忘</h3>
        <p v-if="!dashboard?.waiting.length" class="empty-state">
          暂无等待事项。可在行动账本中选择“等待中”并设置复查时间。
        </p>
        <ol v-else>
          <li v-for="item in dashboard.waiting" :key="item.action_id">
            <strong>{{ item.title }}</strong>
            <span>{{ item.waiting_for_kind }}</span>
            <time>{{ toLocalInput(item.review_at).replace('T', ' ') }}</time>
          </li>
        </ol>
      </aside>
    </div>

    <section class="collection-board">
      <header>
        <div>
          <p class="section-kicker">匿名收集夹</p>
          <h3>只显示内部引用和当前状态</h3>
        </div>
        <select v-if="boards.length" v-model="selectedBoardId" aria-label="选择收集板">
          <option v-for="item in boards" :key="item.board_id" :value="item.board_id">
            {{ item.title }}
          </option>
        </select>
      </header>

      <form v-if="!selectedBoard" class="board-create" @submit.prevent="createBoard">
        <label>
          <span>关联正式行动</span>
          <select v-model="boardActionId">
            <option value="">选择行动</option>
            <option v-for="item in actions" :key="item.action_id" :value="item.action_id">
              {{ item.title }}
            </option>
          </select>
        </label>
        <label>
          <span>收集板名称</span>
          <input v-model="boardTitle" maxlength="240">
        </label>
        <label class="board-create__wide">
          <span>匿名参与引用，每行一个</span>
          <textarea v-model="boardParticipants" rows="4" placeholder="student-ref-01&#10;student-ref-02" />
        </label>
        <button
          class="button button--primary"
          type="submit"
          :disabled="saving || !boardActionId || !boardTitle.trim() || !participantsFromInput().length"
        >
          建立匿名收集板
        </button>
      </form>

      <template v-else>
        <div class="collection-tally">
          <span v-for="(label, status) in STATUS_LABELS" :key="status">
            <strong>{{ selectedBoard.counts[status] }}</strong>
            {{ label }}
          </span>
          <span><strong>{{ selectedBoard.total_count }}</strong> 总数</span>
        </div>

        <div class="collection-columns">
          <section v-for="(label, status) in STATUS_LABELS" :key="status">
            <header>{{ label }}</header>
            <article
              v-for="item in selectedBoard.items.filter((entry) => entry.status === status)"
              :key="item.collection_item_id"
            >
              <strong>{{ item.participant_ref }}</strong>
              <select
                :value="item.status"
                aria-label="更新收集状态"
                @change="updateItem(item.collection_item_id, ($event.target as HTMLSelectElement).value as CollectionStatus)"
              >
                <option v-for="(nextLabel, nextStatus) in STATUS_LABELS" :key="nextStatus" :value="nextStatus">
                  {{ nextLabel }}
                </option>
              </select>
              <button type="button" @click="previewIndividual(item.collection_item_id)">
                查看单人提醒
              </button>
            </article>
            <p v-if="!selectedBoard.items.some((entry) => entry.status === status)">空</p>
          </section>
        </div>

        <div class="reminder-proof">
          <div>
            <span>群提醒草稿 · {{ selectedBoard.group_reminder.audience }}</span>
            <strong>尚未发送</strong>
          </div>
          <p>{{ selectedBoard.group_reminder.content }}</p>
          <small>依据：{{ selectedBoard.group_reminder.basis }}；不包含个别未交姓名。</small>
        </div>

        <div v-if="individualPreview" class="reminder-proof reminder-proof--individual">
          <div>
            <span>单人提醒 · {{ individualPreview.audience_ref }}</span>
            <strong>尚未发送</strong>
          </div>
          <p>{{ individualPreview.content }}</p>
          <button type="button" @click="individualPreview = null">关闭预览</button>
        </div>
      </template>
    </section>
  </section>
</template>

<style scoped>
.collection-inbox {
  display: grid;
  gap: var(--space-6);
  margin-bottom: var(--space-8);
  padding: var(--space-7);
  border: var(--border-width) solid var(--color-border-default);
  border-radius: var(--radius-panel);
  background: var(--color-bg-surface);
}

.collection-inbox__heading,
.collection-board > header,
.meeting-drafts__actions,
.reminder-proof > div {
  display: flex;
  justify-content: space-between;
  gap: var(--space-4);
}

.collection-inbox__heading h2,
.collection-board h3,
.waiting-pocket h3 {
  margin: var(--space-1) 0 var(--space-2);
}

.collection-inbox__heading p:last-child {
  max-width: 720px;
  margin: 0;
  color: var(--color-text-secondary);
}

.inbox-tabs {
  display: flex;
  align-self: flex-start;
  overflow: hidden;
  border: var(--border-width) solid var(--color-border-strong);
  border-radius: var(--radius-control);
}

.inbox-tabs span {
  padding: var(--space-2) var(--space-3);
  border-inline-end: var(--border-width) solid var(--color-border-default);
  color: var(--color-text-secondary);
  font-size: var(--font-size-caption);
}

.inbox-tabs span:last-child {
  border: 0;
}

.local-message {
  margin: 0;
  color: var(--color-success);
}

.b04-grid {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(240px, 0.32fr);
  gap: var(--space-5);
}

.meeting-pocket,
.waiting-pocket,
.collection-board {
  border: var(--border-width) solid var(--color-border-default);
  border-radius: var(--radius-panel);
}

.meeting-pocket {
  overflow: hidden;
}

.pocket-label {
  display: flex;
  justify-content: space-between;
  padding: var(--space-3) var(--space-5);
  background:
    repeating-linear-gradient(
      -45deg,
      var(--color-accent-subtle),
      var(--color-accent-subtle) 8px,
      var(--color-bg-surface) 8px,
      var(--color-bg-surface) 16px
    );
  color: var(--color-accent);
  font-size: var(--font-size-dense);
}

.meeting-import,
.meeting-drafts,
.waiting-pocket,
.collection-board {
  padding: var(--space-5);
}

.meeting-import {
  display: grid;
  gap: var(--space-4);
}

.meeting-card {
  display: grid;
  grid-template-columns: 40px minmax(0, 1fr);
  gap: var(--space-3);
  padding: var(--space-5) 0;
  border-bottom: var(--border-width) solid var(--color-border-default);
}

.meeting-card__index {
  display: grid;
  place-items: center;
  width: 32px;
  height: 32px;
  border-radius: 50%;
  background: var(--color-accent);
  color: var(--color-bg-surface);
  font: 600 var(--font-size-dense)/1 ui-monospace, "Cascadia Mono", Consolas, monospace;
}

.meeting-card__body {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(220px, 0.42fr);
  gap: var(--space-3);
}

.meeting-card__wide {
  grid-column: 1 / -1;
}

.meeting-card details {
  padding: var(--space-3);
  border: var(--border-width) solid var(--color-border-subtle);
  border-radius: var(--radius-control);
}

.meeting-card summary {
  cursor: pointer;
  color: var(--color-accent);
}

.meeting-action {
  display: grid;
  grid-template-columns: 1fr 220px;
  gap: var(--space-2);
  margin-top: var(--space-2);
}

.meeting-card__tools {
  display: flex;
  gap: var(--space-2);
}

.meeting-card__tools button,
.collection-columns article button,
.reminder-proof button {
  padding: var(--space-1) var(--space-2);
  border: 0;
  background: transparent;
  color: var(--color-accent);
  cursor: pointer;
}

.card-alert {
  margin: 0;
  padding: var(--space-2) var(--space-3);
  border-inline-start: 3px solid var(--color-warning);
  background: var(--color-warning-subtle);
  color: var(--color-warning);
}

.source-choice {
  display: flex;
  align-items: flex-start;
  gap: var(--space-2);
  margin: var(--space-4) 0;
}

.source-choice input {
  width: auto;
  min-height: auto;
}

.meeting-drafts__actions {
  justify-content: flex-start;
  align-items: center;
}

.waiting-pocket ol {
  display: grid;
  gap: var(--space-3);
  margin: var(--space-4) 0 0;
  padding: 0;
  list-style: none;
}

.waiting-pocket li {
  display: grid;
  gap: var(--space-1);
  padding: var(--space-3);
  border-inline-start: 3px solid var(--color-warning);
  background: var(--color-warning-subtle);
}

.waiting-pocket span,
.waiting-pocket time,
.empty-state {
  color: var(--color-text-secondary);
  font-size: var(--font-size-dense);
}

.board-create {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: var(--space-3);
  margin-top: var(--space-5);
}

.board-create__wide {
  grid-column: 1 / -1;
}

.collection-tally {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-1);
  margin: var(--space-5) 0;
}

.collection-tally span {
  padding: var(--space-2) var(--space-3);
  border: var(--border-width) solid var(--color-border-default);
  background: var(--color-bg-subtle);
  color: var(--color-text-secondary);
}

.collection-tally strong {
  color: var(--color-text-primary);
}

.collection-columns {
  display: grid;
  grid-template-columns: repeat(5, minmax(150px, 1fr));
  gap: var(--space-2);
  overflow-x: auto;
}

.collection-columns > section {
  min-height: 160px;
  padding: var(--space-2);
  border-top: 4px solid var(--color-border-strong);
  background: var(--color-bg-subtle);
}

.collection-columns > section > header {
  padding: var(--space-2);
  color: var(--color-text-secondary);
  font-weight: var(--font-weight-medium);
}

.collection-columns article {
  display: grid;
  gap: var(--space-2);
  margin-top: var(--space-2);
  padding: var(--space-3);
  border: var(--border-width) solid var(--color-border-default);
  border-radius: var(--radius-control);
  background: var(--color-bg-surface);
}

.collection-columns > section > p {
  padding: var(--space-3);
  color: var(--color-text-muted);
  text-align: center;
}

.reminder-proof {
  margin-top: var(--space-5);
  padding: var(--space-4);
  border: var(--border-width) dashed var(--color-border-strong);
  border-radius: var(--radius-control);
}

.reminder-proof strong {
  color: var(--color-warning);
}

.reminder-proof small {
  color: var(--color-text-muted);
}

.reminder-proof--individual {
  border-color: var(--color-accent);
  background: var(--color-accent-subtle);
}

@media (max-width: 980px) {
  .b04-grid {
    grid-template-columns: 1fr;
  }
}

@media (max-width: 820px) {
  .collection-inbox {
    padding: var(--space-5);
  }

  .collection-inbox__heading,
  .collection-board > header,
  .meeting-drafts__actions {
    align-items: flex-start;
    flex-direction: column;
  }

  .inbox-tabs {
    flex-wrap: wrap;
  }

  .meeting-card__body,
  .meeting-action,
  .board-create {
    grid-template-columns: 1fr;
  }

  .meeting-card__wide,
  .board-create__wide {
    grid-column: auto;
  }
}
</style>
