<script setup lang="ts">
import { computed, nextTick, onMounted, ref } from 'vue'
import { Check, Pencil, Plus, Search, Trash2 } from '@lucide/vue'

import { sessionRouteDefinition } from '../../navigation'
import { curriculumVolumeAbbrev } from '../../lib/curriculum-label'
import { formatSessionDate } from '../../lib/session-date'
import { sessionStatusLabel, sessionStatusTone } from '../../lib/session-status'
import { useSessionDeletion } from '../../composables/useSessionDeletion'
import { useSessionSwitch } from '../../composables/useSessionSwitch'
import { useCurriculumScopeStore } from '../../stores/curriculum-scope'
import { useSessionStore } from '../../stores/session'
import type { SessionSummary } from '../../api/sessions'
import FeedbackBanner from '../design-system/FeedbackBanner.vue'
import AppButton from '../design-system/AppButton.vue'
import StatePanel from '../design-system/StatePanel.vue'

const emit = defineEmits<{ navigate: [] }>()

const sessionStore = useSessionStore()
const curriculumScope = useCurriculumScopeStore()
const { switchSession } = useSessionSwitch()
const deletion = useSessionDeletion()

const query = ref('')
/* 学期筛选是抽屉内的本地筛选，不改动全局教学学期 */
const volumeFilter = ref('')
const renamingId = ref<number | null>(null)
const renameValue = ref('')
const renameError = ref('')
let renameInputEl: HTMLInputElement | null = null

const volumeLabelById = computed(() => {
  const map = new Map<string, string>()
  for (const volume of curriculumScope.volumes) map.set(volume.id, volume.label)
  return map
})

const sortedSessions = computed(() => [...sessionStore.sessions].sort((left, right) => {
  const leftTime = left.created_at ? new Date(left.created_at).getTime() : 0
  const rightTime = right.created_at ? new Date(right.created_at).getTime() : 0
  return rightTime - leftTime
}))

const filteredSessions = computed(() => {
  const keyword = query.value.trim().toLowerCase()
  return sortedSessions.value.filter((session) => {
    if (volumeFilter.value && session.curriculum_volume_id !== volumeFilter.value) return false
    return !keyword || session.name.toLowerCase().includes(keyword)
  })
})

const impactLine = computed(() => {
  const reviewed = deletion.impact.value
  if (!reviewed) return ''
  return [
    `${reviewed.permanent_counts.answer_sheets ?? 0} 份答卷`,
    `${reviewed.permanent_counts.grading_results ?? 0} 份批改结果`,
    `${reviewed.storage_counts.owned_files ?? 0} 个文件`,
    `${deletion.deletionRecordTotal.value} 条记录`,
  ].join(' · ')
})

function volumeAbbrev(session: SessionSummary): string {
  const label = session.curriculum_volume_id
    ? volumeLabelById.value.get(session.curriculum_volume_id)
    : null
  return curriculumVolumeAbbrev(label)
}

function volumeFullLabel(session: SessionSummary): string {
  return session.curriculum_volume_id
    ? volumeLabelById.value.get(session.curriculum_volume_id) ?? '未归类'
    : '未归类'
}

function setCurrent(session: SessionSummary): void {
  // 守卫拒绝（待核对提交 / 未保存修改）时保持现状，由 switchSession 自己提示
  void switchSession(session.id)
}

/* 函数 ref：v-for 内的 ref 属性会被收集成数组，改用回调拿到单个输入框 */
function setRenameInput(el: unknown): void {
  renameInputEl = el instanceof HTMLInputElement ? el : null
}

function startRename(session: SessionSummary): void {
  deletion.collapse()
  renamingId.value = session.id
  renameValue.value = session.name
  renameError.value = ''
  void nextTick(() => {
    renameInputEl?.focus()
    renameInputEl?.select()
  })
}

function cancelRename(): void {
  renamingId.value = null
  renameError.value = ''
}

async function saveRename(session: SessionSummary): Promise<void> {
  const normalized = renameValue.value.trim()
  if (!normalized) {
    renameError.value = '考试名称不能为空'
    return
  }
  try {
    await sessionStore.renameSessionById(session.id, normalized)
    cancelRename()
  } catch {
    renameError.value = '重命名结果未能确认，请稍后重试。'
  }
}

onMounted(() => {
  if (sessionStore.loadState === 'idle') void sessionStore.initialize()
  void curriculumScope.initialize()
})
</script>

<template>
  <div class="session-manager">
    <div class="session-manager__toolbar">
      <label class="session-manager__search">
        <Search :size="14" :stroke-width="1.8" aria-hidden="true" />
        <input class="app-input"
          v-model="query"
          type="search"
          placeholder="搜索考试"
          aria-label="搜索考试"
        />
      </label>
      <select class="app-input" v-model="volumeFilter" aria-label="按学期筛选">
        <option value="">全部学期</option>
        <option v-for="volume in curriculumScope.volumes" :key="volume.id" :value="volume.id">
          {{ volume.label }}
        </option>
      </select>
      <RouterLink
        class="session-manager__new"
        :to="sessionRouteDefinition.path"
        @click="emit('navigate')"
      >
        <Plus :size="14" :stroke-width="2" aria-hidden="true" />
        新建考试
      </RouterLink>
    </div>

    <FeedbackBanner v-if="deletion.notice.value" role="status" tone="info" :description="deletion.notice.value" />

    <div v-if="deletion.pendingCleanups.value.length" class="session-manager__cleanup">
      <span class="session-manager__cleanup-text">
        {{ deletion.pendingCleanups.value.length }} 场已删除考试的文件待清理
      </span>
      <button
        v-for="pending in deletion.pendingCleanups.value"
        :key="pending.session_id"
        type="button"
        class="session-manager__cleanup-retry"
        :disabled="deletion.cleanupBusy.value"
        :aria-label="`重试清理考试 #${pending.session_id}`"
        :title="`考试 #${pending.session_id} · ${pending.deleted_files} 个文件待清理`"
        @click="deletion.retryPendingCleanup(pending)"
      >重试清理</button>
    </div>

    <StatePanel
      v-if="sessionStore.loadState === 'loading'"
      kind="loading"
      title="正在读取考试列表"
    />
    <StatePanel
      v-else-if="sessionStore.loadState === 'error'"
      kind="error"
      :title="sessionStore.errorMessage || '考试列表暂时无法读取'"
      retry-label="重新加载"
      @retry="sessionStore.initialize()"
    />
    <StatePanel v-else-if="filteredSessions.length === 0" kind="empty" title="还没有考试">
      <template #actions>
        <RouterLink
          class="session-manager__new"
          :to="sessionRouteDefinition.path"
          @click="emit('navigate')"
        >
          <Plus :size="14" :stroke-width="2" aria-hidden="true" />
          新建考试
        </RouterLink>
      </template>
    </StatePanel>
    <ul v-else class="session-manager__list">
      <li
        v-for="session in filteredSessions"
        :key="session.id"
        class="session-manager__row"
        :class="{
          'is-current': session.id === sessionStore.selectedSessionId,
          'is-expanded': deletion.expandedId.value === session.id,
        }"
      >
        <div class="session-manager__row-line">
          <div class="session-manager__name-cell">
            <input
              v-if="renamingId === session.id"
              :ref="setRenameInput"
              v-model="renameValue"
              class="session-manager__rename-input app-input"
              type="text"
              aria-label="重命名考试"
              @keydown.enter.prevent="saveRename(session)"
              @keydown.esc.prevent.stop="cancelRename"
              @blur="cancelRename"
            />
            <template v-else>
              <span class="session-manager__name" :title="session.name">{{ session.name }}</span>
              <span
                v-if="session.id === sessionStore.selectedSessionId"
                class="session-manager__tag"
              >当前</span>
            </template>
          </div>
          <span
            v-if="volumeAbbrev(session)"
            class="session-manager__term"
            :title="volumeFullLabel(session)"
          >{{ volumeAbbrev(session) }}</span>
          <span class="session-manager__date">{{ formatSessionDate(session.created_at) }}</span>
          <span
            v-if="sessionStatusTone(session.status)"
            class="session-manager__state"
            :data-tone="sessionStatusTone(session.status)"
          >{{ sessionStatusLabel(session.status) }}</span>
          <span class="session-manager__actions">
            <button
              v-if="session.id !== sessionStore.selectedSessionId"
              type="button"
              aria-label="设为当前考试"
              title="设为当前考试"
              @click="setCurrent(session)"
            ><Check :size="15" :stroke-width="2" aria-hidden="true" /></button>
            <button
              type="button"
              aria-label="重命名"
              title="重命名"
              @click="startRename(session)"
            ><Pencil :size="15" :stroke-width="1.8" aria-hidden="true" /></button>
            <button
              type="button"
              class="is-danger"
              :aria-label="`删除 ${session.name}`"
              :title="`删除 ${session.name}`"
              :aria-expanded="deletion.expandedId.value === session.id"
              @click="deletion.toggle(session.id)"
            ><Trash2 :size="15" :stroke-width="1.8" aria-hidden="true" /></button>
          </span>
        </div>
        <p v-if="renamingId === session.id && renameError" class="session-manager__row-error" role="alert">
          {{ renameError }}
        </p>
        <div
          v-if="deletion.expandedId.value === session.id"
          class="session-manager__detail"
        >
          <p
            v-if="deletion.state.value === 'loading'"
            class="session-manager__detail-line"
            role="status"
          >正在核对删除范围…</p>
          <template v-else-if="deletion.impact.value && deletion.state.value !== 'error'">
            <p class="session-manager__detail-line">{{ impactLine }}</p>
            <p class="session-manager__detail-danger">永久删除，无法恢复；学生名单和题库试题保留。</p>
            <div class="session-manager__detail-actions">
              <p
                v-if="!deletion.impact.value.can_permanently_delete"
                class="session-manager__detail-blocked"
              >{{ deletion.rowMessage.value }}</p>
              <template v-else>
                <button
                  type="button"
                  class="session-manager__delete"
                  :disabled="deletion.state.value === 'working'"
                  @click="deletion.submitDeletion()"
                >{{ deletion.state.value === 'working' ? '正在彻底删除…' : '彻底删除' }}</button>
              </template>
              <button
                type="button"
                class="session-manager__cancel"
                @click="deletion.collapse()"
              >取消</button>
            </div>
          </template>
          <div v-else class="session-manager__detail-actions">
            <p class="session-manager__detail-error" role="alert">{{ deletion.rowMessage.value }}</p>
            <AppButton
              type="button"
             
              variant="ghost" size="small" @click="deletion.review(session.id)"
            >重新查看影响</AppButton>
          </div>
        </div>
      </li>
    </ul>
  </div>
</template>

<style scoped>
.session-manager {
  display: grid;
  min-width: 0;
  gap: var(--space-3);
}

.session-manager p {
  margin: 0;
}

.session-manager__toolbar {
  display: flex;
  min-width: 0;
  align-items: center;
  gap: var(--space-2);
}

.session-manager__search {
  display: flex;
  min-width: 0;
  flex: 1 1 auto;
  align-items: center;
  gap: 6px;
  padding-inline: 10px;
  border: var(--border-width) solid var(--color-border-default);
  border-radius: var(--radius-control);
  color: var(--color-text-muted);
}

.session-manager__search:focus-within {
  border-color: var(--color-accent);
}

.session-manager__search input {
  width: 100%;
  min-width: 0;
  min-height: var(--control-height-small);
  border: 0;
  background: transparent;
  color: var(--color-text-primary);
  font: inherit;
  font-size: var(--font-size-dense);
  outline: none;
}

.session-manager__search input::placeholder {
  color: var(--color-text-muted);
}

.session-manager__toolbar select {
  flex: none;
  max-width: 150px;
  min-height: var(--control-height-default);
  padding: 0 24px 0 8px;
  border: var(--border-width) solid var(--color-border-default);
  border-radius: var(--radius-control);
  background: var(--color-bg-surface);
  color: var(--color-text-primary);
  font: inherit;
  font-size: var(--font-size-dense);
  text-overflow: ellipsis;
}

.session-manager__new {
  display: inline-flex;
  flex: none;
  min-height: var(--control-height-default);
  align-items: center;
  gap: 5px;
  padding-inline: var(--space-3);
  border: var(--border-width) solid var(--color-accent);
  border-radius: var(--radius-control);
  background: var(--color-accent);
  color: var(--color-bg-surface);
  font: inherit;
  font-size: var(--font-size-dense);
  font-weight: var(--font-weight-semibold);
  text-decoration: none;
}

.session-manager__new:hover {
  background: var(--color-accent-active);
}

.session-manager__notice {
  color: var(--color-text-secondary);
  font-size: var(--font-size-caption);
}

.session-manager__cleanup {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 4px var(--space-2);
  padding: 6px 10px;
  border: var(--border-width) solid color-mix(in srgb, var(--color-danger) 35%, var(--color-border-default));
  border-radius: var(--radius-control);
  background: var(--color-danger-subtle);
}

.session-manager__cleanup-text {
  color: var(--color-danger);
  font-size: var(--font-size-caption);
  font-weight: var(--font-weight-medium);
}

.session-manager__cleanup-retry {
  min-height: var(--control-height-small);
  padding-inline: 8px;
  border: var(--border-width) solid var(--color-danger);
  border-radius: var(--radius-tag);
  background: transparent;
  color: var(--color-danger);
  font: inherit;
  font-size: var(--font-size-caption);
  cursor: pointer;
}

.session-manager__cleanup-retry:hover:not(:disabled) {
  background: var(--color-bg-surface);
}


.session-manager__list {
  display: grid;
  min-width: 0;
  margin: 0;
  padding: 0;
  gap: 2px;
  list-style: none;
}

.session-manager__row {
  min-width: 0;
  border-radius: var(--radius-control);
}

.session-manager__row.is-expanded {
  background: var(--color-bg-subtle);
}

/* 固定五列：名称弹性 | 学期缩写 | 日期 | 状态 | 操作区（恒占 3 键位），
   保证当前行/悬停/展开各状态下所有列对齐 */
.session-manager__row-line {
  display: grid;
  min-width: 0;
  min-height: 44px;
  align-items: center;
  gap: var(--space-2);
  grid-template-columns: minmax(0, 1fr) 40px 76px 56px 82px;
  padding: 4px 8px;
}

.session-manager__name-cell {
  display: flex;
  min-width: 0;
  align-items: center;
  gap: 6px;
}

.session-manager__name {
  min-width: 0;
  overflow: hidden;
  color: var(--color-text-primary);
  font-size: var(--font-size-dense);
  font-weight: var(--font-weight-medium);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.session-manager__tag {
  flex: none;
  padding: 0 6px;
  border-radius: var(--radius-tag);
  background: var(--color-bg-selected);
  color: var(--color-accent);
  font-size: var(--font-size-caption);
  font-weight: var(--font-weight-semibold);
  line-height: 18px;
}

.session-manager__term,
.session-manager__date {
  overflow: hidden;
  color: var(--color-text-muted);
  font-size: var(--font-size-caption);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.session-manager__term {
  text-align: center;
}

.session-manager__date {
  font-variant-numeric: tabular-nums;
  text-align: end;
}

.session-manager__state {
  overflow: hidden;
  font-size: var(--font-size-caption);
  font-weight: var(--font-weight-medium);
  text-align: end;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.session-manager__state[data-tone='done'] { color: var(--color-success); }
.session-manager__state[data-tone='run'] { color: var(--color-accent-active); }
.session-manager__state[data-tone='danger'] { color: var(--color-danger); }

/* 恒占 82px（3×26 + 2×2）：当前行少一个“设为当前”按钮时其余按钮仍右对齐原位 */
.session-manager__actions {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 2px;
  opacity: 0;
  transition: opacity var(--duration-fast) ease;
}

.session-manager__row:hover .session-manager__actions,
.session-manager__row:focus-within .session-manager__actions,
.session-manager__row.is-expanded .session-manager__actions {
  opacity: 1;
}

.session-manager__actions button {
  display: grid;
  width: 26px;
  height: var(--control-height-small);
  place-items: center;
  border: 0;
  border-radius: var(--radius-control);
  background: transparent;
  color: var(--color-text-secondary);
  cursor: pointer;
}

.session-manager__actions button:hover {
  background: var(--color-bg-subtle);
  color: var(--color-text-primary);
}

.session-manager__actions button.is-danger:hover {
  background: var(--color-danger-subtle);
  color: var(--color-danger);
}

.session-manager__actions button:focus-visible {
  outline: none;
  box-shadow: var(--focus-ring);
  opacity: 1;
}

.session-manager__rename-input {
  flex: 1 1 auto;
  min-width: 0;
  min-height: var(--control-height-small);
  padding-inline: 8px;
  border: var(--border-width) solid var(--color-accent);
  border-radius: var(--radius-control);
  background: var(--color-bg-surface);
  color: var(--color-text-primary);
  font: inherit;
  font-size: var(--font-size-dense);
  outline: none;
}

.session-manager__row-error {
  padding: 0 8px 4px;
  color: var(--color-danger);
  font-size: var(--font-size-caption);
}

.session-manager__detail {
  display: grid;
  gap: 6px;
  padding: 2px 8px 10px;
}

.session-manager__detail-line {
  color: var(--color-text-secondary);
  font-size: var(--font-size-caption);
}

.session-manager__detail-danger {
  color: var(--color-danger);
  font-size: var(--font-size-caption);
  font-weight: var(--font-weight-medium);
}

.session-manager__detail-blocked,
.session-manager__detail-error {
  color: var(--color-danger);
  font-size: var(--font-size-caption);
}

.session-manager__detail-actions {
  display: flex;
  align-items: center;
  gap: var(--space-2);
}

.session-manager__delete {
  min-height: var(--control-height-small);
  padding-inline: var(--space-3);
  border: var(--border-width) solid var(--color-danger);
  border-radius: var(--radius-control);
  background: var(--color-danger);
  color: var(--destructive-foreground);
  font: inherit;
  font-size: var(--font-size-caption);
  font-weight: var(--font-weight-semibold);
  cursor: pointer;
}

.session-manager__delete:hover:not(:disabled) {
  background: color-mix(in srgb, var(--color-danger) 88%, var(--color-text-primary));
}

.session-manager__cancel {
  min-height: var(--control-height-small);
  padding-inline: var(--space-3);
  border: var(--border-width) solid var(--color-border-default);
  border-radius: var(--radius-control);
  background: transparent;
  color: var(--color-text-secondary);
  font: inherit;
  font-size: var(--font-size-caption);
  cursor: pointer;
}

.session-manager__cancel:hover {
  background: var(--color-bg-subtle);
}
</style>
