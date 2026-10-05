<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import {
  ListboxContent,
  ListboxItem,
  ListboxItemIndicator,
  ListboxRoot,
  PopoverContent,
  PopoverPortal,
  PopoverRoot,
  PopoverTrigger,
} from 'reka-ui'
import { Check, ChevronDown, ClipboardList, Plus, Search, Settings2 } from '@lucide/vue'

import { sessionRouteDefinition } from '../../navigation'
import { curriculumVolumeAbbrev } from '../../lib/curriculum-label'
import { formatSessionDate } from '../../lib/session-date'
import { sessionStatusLabel, sessionStatusTone } from '../../lib/session-status'
import { useConfigWorkspaceStore } from '../../stores/config-workspace'
import { useCurriculumScopeStore } from '../../stores/curriculum-scope'
import { useSessionStore } from '../../stores/session'
import { useConfirm } from '../../composables/useConfirm'
import { useSessionSwitch } from '../../composables/useSessionSwitch'
import SessionManagementDrawer from '../sessions/SessionManagementDrawer.vue'
import FeedbackBanner from '../design-system/FeedbackBanner.vue'

type SwitcherMode = 'expanded' | 'rail' | 'drawer'

const props = defineProps<{ mode?: SwitcherMode }>()
const emit = defineEmits<{ navigate: [] }>()

const route = useRoute()
const sessionStore = useSessionStore()
const curriculumScope = useCurriculumScopeStore()
const configStore = useConfigWorkspaceStore()
const { switchSession } = useSessionSwitch()
const { confirm, alert } = useConfirm()

const isRail = computed(() => props.mode === 'rail')
/* 桌面（展开/图标轨）从侧栏右侧弹出，不遮挡触发卡；≤900px 抽屉内向下展开 */
const popoverSide = computed(() => (props.mode === 'drawer' ? 'bottom' : 'right'))

const open = ref(false)
const managementOpen = ref(false)
const searchQuery = ref('')
const showOtherSessions = ref(false)
const searchInputRef = ref<HTMLInputElement | null>(null)
const rootRef = ref<HTMLElement | null>(null)
const interactedOutside = ref(false)

const knowledgeTraining = computed(() => ['knowledge-graph', 'training', 'student-evidence'].includes(String(route.name)))
const showCurriculumScope = computed(() => route.meta.curriculumScope !== false)

const scopedSessions = computed(() => {
  const selectedVolumeId = curriculumScope.selectedVolumeId
  if (!showCurriculumScope.value || !selectedVolumeId || showOtherSessions.value) {
    return sessionStore.sessions
  }
  return sessionStore.sessions.filter((session) => (
    session.curriculum_volume_id === selectedVolumeId
    || session.id === sessionStore.selectedSessionId
  ))
})
const otherSessionCount = computed(() => {
  const selectedVolumeId = curriculumScope.selectedVolumeId
  if (!selectedVolumeId) return 0
  return sessionStore.sessions.filter(
    session => session.curriculum_volume_id !== selectedVolumeId,
  ).length
})
const currentSessionOutsideScope = computed(() => Boolean(
  showCurriculumScope.value
  && curriculumScope.selectedVolumeId
  && sessionStore.currentSession
  && sessionStore.currentSession.curriculum_volume_id !== curriculumScope.selectedVolumeId,
))

/* 触发卡片 */
const currentSession = computed(() => sessionStore.currentSession)
const iconAriaLabel = computed(() =>
  currentSession.value ? `当前考试：${currentSession.value.name}` : '当前考试：未选择',
)
const currentStatusTone = computed(() => {
  const status = currentSession.value?.status
  return status ? sessionStatusTone(status) : null
})
const currentStatusLabel = computed(() => {
  const status = currentSession.value?.status
  // 侧栏不渲染未知状态；最近考试表仍使用状态标签的兼容显示。
  return status && currentStatusTone.value ? sessionStatusLabel(status) : null
})
const volumeLabelById = computed(() => {
  const map = new Map<string, string>()
  for (const volume of curriculumScope.volumes) map.set(volume.id, volume.label)
  return map
})
const currentVolumeLabel = computed(() => {
  const id = currentSession.value?.curriculum_volume_id
  return id ? volumeLabelById.value.get(id) ?? null : null
})
/* 图标轨触发钮下图标下方的学期缩写（「八年级上册」→「八上」） */
const railVolumeAbbrev = computed(() => curriculumVolumeAbbrev(currentVolumeLabel.value))
const currentDateLabel = computed(() => formatSessionDate(currentSession.value?.created_at))
const sessionListFailed = computed(() => sessionStore.loadState === 'error')
const cardAttention = computed(() => sessionListFailed.value || currentSessionOutsideScope.value)

const currentMetaText = computed(() => {
  if (sessionListFailed.value) return '考试列表加载失败'
  if (currentSessionOutsideScope.value) return '不在当前学期'
  /* 未选考试时只保留全局已选学期，没有就整行不渲染 */
  if (!currentSession.value) return curriculumScope.selectedVolume?.label ?? ''
  const parts = [currentVolumeLabel.value ?? '全部学期']
  if (currentDateLabel.value) parts.push(currentDateLabel.value)
  return parts.join(' · ')
})
const currentMetaAttention = computed(
  () => sessionListFailed.value || currentSessionOutsideScope.value,
)

const filteredSessions = computed(() => {
  const keyword = searchQuery.value.trim().toLowerCase()
  if (!keyword) return scopedSessions.value
  return scopedSessions.value.filter((item) => item.name.toLowerCase().includes(keyword))
})

/* Listbox 受控选中；「不选择考试」行用字符串哨兵值 */
const NONE_VALUE = '__none__'
const listSelection = ref<number | string>(sessionStore.selectedSessionId ?? NONE_VALUE)

watch(() => curriculumScope.selectedVolumeId, () => {
  showOtherSessions.value = false
})

watch(showCurriculumScope, (visible) => {
  if (visible) void curriculumScope.initialize()
}, { immediate: true })

watch(open, (value) => {
  if (!value) {
    showOtherSessions.value = false
    return
  }
  searchQuery.value = ''
  listSelection.value = sessionStore.selectedSessionId ?? NONE_VALUE
  if (sessionStore.loadState === 'idle') void sessionStore.initialize()
})

watch(() => sessionStore.selectedSessionId, (value) => {
  listSelection.value = value ?? NONE_VALUE
})

/* 与原顶栏 selectCurriculumVolume 一致：待核对提交阻止、脏编辑确认丢弃、跨学期收起考试 */
async function selectCurriculumVolume(event: Event): Promise<void> {
  const selector = event.currentTarget as HTMLSelectElement
  const nextVolumeId = selector.value || null
  const selectedSession = sessionStore.currentSession
  const changesCurrentExam = Boolean(
    nextVolumeId
    && selectedSession
    && selectedSession.curriculum_volume_id !== nextVolumeId,
  )
  const restoreScopeSelection = () => {
    selector.value = curriculumScope.selectedVolumeId ?? ''
  }
  if (changesCurrentExam && configStore.hasPendingSubmission) {
    await alert({ title: '请先完成核对', message: '当前考试仍有上传或生成结果等待核对。请先完成核对，再切换教学学期。' })
    restoreScopeSelection()
    return
  }
  if (changesCurrentExam && configStore.hasDirtyEditor) {
    const discard = await confirm({
      title: '切换教学学期？',
      message: '当前评分依据有未保存修改，切换教学学期会收起当前考试并丢弃这些修改。',
      confirmLabel: '切换',
      danger: true,
    })
    if (!discard) {
      restoreScopeSelection()
      return
    }
    configStore.discardEditorDraft()
  }
  if (changesCurrentExam) {
    if (!configStore.selectSession(null, true)) {
      restoreScopeSelection()
      return
    }
    sessionStore.clearSelection()
  }
  curriculumScope.selectVolume(nextVolumeId)
}

async function pickSession(id: number | null): Promise<void> {
  if (!await switchSession(id)) {
    // 守卫拒绝：保持浮层打开、勾选回退到当前考试
    listSelection.value = sessionStore.selectedSessionId ?? NONE_VALUE
    return
  }
  open.value = false
  emit('navigate')
}

function retrySessions(): void {
  void sessionStore.initialize()
}

function openManagement(): void {
  open.value = false
  managementOpen.value = true
  emit('navigate')
}

function goNewSession(): void {
  open.value = false
  emit('navigate')
}

function onOpenAutoFocus(event: Event): void {
  event.preventDefault()
  searchInputRef.value?.focus()
}

function onInteractOutside(): void {
  interactedOutside.value = true
}

/* reka 缓存的 trigger 节点在模式切换（图标轨 ↔ 展开卡）后已脱离 DOM，
   这里对非外部交互的关闭（Esc / 编程关闭）自行把焦点还给当前 trigger；
   外部交互关闭保持焦点在用户点击处（reka 的默认语义） */
function onCloseAutoFocus(event: Event): void {
  if (!interactedOutside.value) {
    event.preventDefault()
    /* Primitive as-child 会接管 trigger 的 ref，改为查询当前真实节点
       （模式切换后 reka 缓存的节点可能已脱离 DOM） */
    rootRef.value
      ?.querySelector<HTMLElement>('.exam-switcher__card, .exam-switcher__icon')
      ?.focus()
  }
  interactedOutside.value = false
}

function onDocumentKeydown(event: KeyboardEvent): void {
  if (!managementOpen.value || event.key !== 'Escape') return
  event.preventDefault()
  event.stopPropagation()
  managementOpen.value = false
}

/* 冒泡阶段即可：抽屉内控件（如行内重命名输入框）的 Esc 能先 stopPropagation 自己消化 */
onMounted(() => document.addEventListener('keydown', onDocumentKeydown))
onBeforeUnmount(() => document.removeEventListener('keydown', onDocumentKeydown))
</script>

<template>
  <div ref="rootRef" class="exam-switcher" :class="{ 'exam-switcher--rail': isRail }">
    <PopoverRoot v-model:open="open">
      <PopoverTrigger as-child>
        <!-- 单个持久按钮：模式切换只改内容与样式，元素不被替换，
             否则 reka 缓存的 trigger 节点脱离 DOM，popover 锚点会漂移到 0,0 -->
        <button
          :class="isRail ? 'exam-switcher__icon' : 'exam-switcher__card'"
          type="button"
          :aria-label="isRail ? iconAriaLabel : undefined"
          :title="isRail ? iconAriaLabel : undefined"
        >
          <template v-if="isRail">
            <span class="exam-switcher__icon-box" aria-hidden="true">
              <ClipboardList :size="18" :stroke-width="1.8" />
              <span v-if="currentSession" class="exam-switcher__icon-dot"></span>
            </span>
            <span v-if="railVolumeAbbrev" class="exam-switcher__term">{{ railVolumeAbbrev }}</span>
          </template>
          <template v-else>
            <span class="exam-switcher__label-row">
              <span class="exam-switcher__label">当前考试</span>
              <span
                v-if="currentStatusLabel"
                class="exam-switcher__status"
                :data-tone="currentStatusTone"
              >{{ currentStatusLabel }}</span>
            </span>
            <span class="exam-switcher__name-row">
              <span class="exam-switcher__name" :class="{ 'is-empty': !currentSession }">
                {{ currentSession?.name ?? '未选择考试' }}
              </span>
              <span v-if="cardAttention" class="exam-switcher__attention" aria-hidden="true"></span>
              <ChevronDown :size="14" :stroke-width="1.8" aria-hidden="true" class="exam-switcher__chevron" />
            </span>
            <span
              v-if="currentMetaText"
              class="exam-switcher__meta"
              :class="{ 'is-attention': currentMetaAttention }"
              :title="currentMetaText"
            >{{ currentMetaText }}</span>
          </template>
        </button>
      </PopoverTrigger>

      <PopoverPortal>
        <PopoverContent
          class="exam-switcher-popover fx-popover"
          :side="popoverSide"
          align="start"
          :side-offset="8"
          :collision-padding="8"
          @open-auto-focus="onOpenAutoFocus"
          @close-auto-focus="onCloseAutoFocus"
          @interact-outside="onInteractOutside"
        >
          <div v-if="showCurriculumScope" class="exam-switcher-popover__section">
            <label class="exam-switcher-popover__field-label" for="current-curriculum-volume">教学学期</label>
            <select class="app-input"
              id="current-curriculum-volume"
              :value="curriculumScope.selectedVolumeId ?? ''"
              :disabled="curriculumScope.loadState === 'loading' || curriculumScope.loadState === 'error'"
              @change="selectCurriculumVolume"
            >
              <option value="">{{ knowledgeTraining ? '请选择教学学期' : '未选择（显示全部）' }}</option>
              <option v-for="volume in curriculumScope.volumes" :key="volume.id" :value="volume.id">
                {{ volume.label }}
              </option>
            </select>
            <FeedbackBanner
              v-if="curriculumScope.loadState === 'error'"
              role="alert"
              tone="error"
              :description="curriculumScope.errorMessage"
              action-label="重新加载"
              @action="curriculumScope.initialize()"
            />
          </div>

          <div class="exam-switcher-popover__search">
            <Search :size="14" :stroke-width="1.8" aria-hidden="true" />
            <input
              ref="searchInputRef"
              v-model="searchQuery"
              class="exam-switcher-popover__search-input app-input"
              type="search"
              placeholder="搜索考试"
              aria-label="搜索考试"
            />
          </div>

          <p
            v-if="sessionStore.loadState === 'loading'"
            class="exam-switcher-popover__status"
            role="status"
          >正在读取考试列表</p>
          <FeedbackBanner
            v-else-if="sessionListFailed"
            role="alert"
            tone="error"
            description="考试列表加载失败。"
            action-label="重新加载"
            @action="retrySessions"
          />
          <template v-else>
            <p
              v-if="currentSessionOutsideScope"
              class="exam-switcher-popover__status is-attention"
              role="status"
            >当前考试不属于所选教学学期，已保留当前选择。</p>
            <ListboxRoot
              v-model="listSelection"
              class="exam-switcher-popover__list"
              highlight-on-hover
              selection-behavior="replace"
            >
              <ListboxContent class="exam-switcher-popover__list-content">
                <ListboxItem
                  class="exam-switcher-popover__row"
                  :value="NONE_VALUE"
                  @select="pickSession(null)"
                >
                  <span class="exam-switcher-popover__row-main">
                    <span class="exam-switcher-popover__row-name is-empty">不选择考试</span>
                  </span>
                  <span v-if="sessionStore.selectedSessionId === null" class="exam-switcher-popover__check">
                    <Check :size="14" :stroke-width="2.2" aria-hidden="true" />
                  </span>
                </ListboxItem>
                <ListboxItem
                  v-for="item in filteredSessions"
                  :key="item.id"
                  class="exam-switcher-popover__row"
                  :value="item.id"
                  @select="pickSession(item.id)"
                >
                  <span class="exam-switcher-popover__row-main">
                    <span class="exam-switcher-popover__row-name">{{ item.name }}</span>
                    <span class="exam-switcher-popover__row-meta">
                      {{ formatSessionDate(item.created_at) || '日期未知' }}<template v-if="item.curriculum_volume_id && volumeLabelById.get(item.curriculum_volume_id)"> · {{ volumeLabelById.get(item.curriculum_volume_id) }}</template>
                    </span>
                  </span>
                  <ListboxItemIndicator class="exam-switcher-popover__check">
                    <Check :size="14" :stroke-width="2.2" aria-hidden="true" />
                  </ListboxItemIndicator>
                </ListboxItem>
                <p v-if="filteredSessions.length === 0" class="exam-switcher-popover__empty">没有匹配的考试</p>
              </ListboxContent>
            </ListboxRoot>
            <button
              v-if="curriculumScope.selectedVolumeId && otherSessionCount > 0"
              type="button"
              class="exam-switcher-popover__toggle"
              :aria-expanded="showOtherSessions"
              @click="showOtherSessions = !showOtherSessions"
            >
              {{ showOtherSessions ? '收起其他学期' : `其他学期 ${otherSessionCount}` }}
            </button>
          </template>

          <footer class="exam-switcher-popover__footer">
            <RouterLink
              class="exam-switcher-popover__footer-link"
              :to="sessionRouteDefinition.path"
              @click="goNewSession"
            >
              <Plus :size="14" :stroke-width="1.8" aria-hidden="true" />
              新建考试
            </RouterLink>
            <button type="button" class="exam-switcher-popover__footer-link" @click="openManagement">
              <Settings2 :size="14" :stroke-width="1.8" aria-hidden="true" />
              考试管理
            </button>
          </footer>
        </PopoverContent>
      </PopoverPortal>
    </PopoverRoot>

    <SessionManagementDrawer v-model:open="managementOpen" />
  </div>
</template>
