<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'

import {
  TAXONOMY_DIMENSIONS,
  type TaxonomyDimension,
  type TaxonomyProposal,
  type TaxonomyTerm,
} from '../../api/question-bank-taxonomy'
import { useTaxonomyReviewStore } from '../../stores/taxonomy-review'

interface CandidateDraft {
  editedName: string
  mergeSearch: string
  targetTermId: string
}

const props = defineProps<{
  open: boolean
}>()

const emit = defineEmits<{
  close: []
}>()

const store = useTaxonomyReviewStore()
const closeButton = ref<HTMLButtonElement | null>(null)
const activeDimension = ref<'all' | TaxonomyDimension>('all')
const drafts = reactive<Record<string, CandidateDraft>>({})

const dimensionLabels: Record<TaxonomyDimension, string> = {
  curriculum: '教材归属',
  knowledge: '知识点',
  ability: '能力',
  method: '思想方法',
  model: '数学模型',
}

const pendingProposals = computed(() => (
  store.proposals.filter(({ status }) => status === 'pending')
))

const dimensionCounts = computed<Record<TaxonomyDimension, number>>(() => (
  Object.fromEntries(TAXONOMY_DIMENSIONS.map((key) => [
    key,
    pendingProposals.value.filter(({ dimension }) => dimension === key).length,
  ])) as Record<TaxonomyDimension, number>
))

const visibleGroups = computed(() => TAXONOMY_DIMENSIONS
  .filter((dimension) => (
    activeDimension.value === 'all' || activeDimension.value === dimension
  ))
  .map((dimension) => ({
    dimension,
    label: dimensionLabels[dimension],
    items: pendingProposals.value.filter((item) => item.dimension === dimension),
  }))
  .filter(({ items }) => items.length > 0))

watch(
  () => store.proposals,
  (items) => {
    for (const proposal of items) {
      if (!drafts[proposal.id]) {
        drafts[proposal.id] = {
          editedName: proposal.edited_name || proposal.proposed_name,
          mergeSearch: '',
          targetTermId: proposal.nearest_id || '',
        }
      }
    }
  },
  { deep: true, immediate: true },
)

watch(
  () => props.open,
  (open) => {
    if (!open) return
    if (store.loadState !== 'loading' && store.writeState !== 'saving') void store.load()
    void nextTick(() => closeButton.value?.focus())
  },
)

function draftFor(proposal: TaxonomyProposal): CandidateDraft {
  const existing = drafts[proposal.id]
  if (existing) return existing
  const created = {
    editedName: proposal.edited_name || proposal.proposed_name,
    mergeSearch: '',
    targetTermId: proposal.nearest_id || '',
  }
  drafts[proposal.id] = created
  return created
}

function termFor(proposal: TaxonomyProposal, termId: string | null): TaxonomyTerm | null {
  if (!termId) return null
  return store.termsFor(proposal.dimension).find(({ id }) => id === termId) ?? null
}

function matchingTerms(proposal: TaxonomyProposal): TaxonomyTerm[] {
  const draft = draftFor(proposal)
  const search = draft.mergeSearch.trim().toLocaleLowerCase()
  const selected = termFor(proposal, draft.targetTermId)
  const matches = store.termsFor(proposal.dimension)
    .filter((term) => {
      if (!search) return true
      return [term.name, ...term.aliases].some(
        (value) => value.toLocaleLowerCase().includes(search),
      )
    })
    .slice(0, 30)
  if (selected && !matches.some(({ id }) => id === selected.id)) matches.unshift(selected)
  return matches
}

function formatDate(value: string | null): string {
  if (!value) return '时间未知'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return '时间未知'
  return new Intl.DateTimeFormat('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  }).format(date)
}

async function approve(proposal: TaxonomyProposal): Promise<void> {
  if (await store.review(proposal.id, { decision: 'approve' })) {
    delete drafts[proposal.id]
  }
}

async function approveEdited(proposal: TaxonomyProposal): Promise<void> {
  const editedName = draftFor(proposal).editedName.trim()
  if (!editedName) return
  if (await store.review(proposal.id, {
    decision: 'edit',
    edited_name: editedName,
  })) {
    delete drafts[proposal.id]
  }
}

async function merge(proposal: TaxonomyProposal): Promise<void> {
  const targetTermId = draftFor(proposal).targetTermId
  if (!targetTermId || !termFor(proposal, targetTermId)) return
  if (await store.review(proposal.id, {
    decision: 'merge',
    target_term_id: targetTermId,
  })) {
    delete drafts[proposal.id]
  }
}

async function reject(proposal: TaxonomyProposal): Promise<void> {
  const confirmed = window.confirm(
    `确认拒绝“${proposal.proposed_name}”吗？拒绝后它不会进入正式词表。`,
  )
  if (!confirmed) return
  if (await store.review(proposal.id, { decision: 'reject' })) {
    delete drafts[proposal.id]
  }
}

function useNearest(proposal: TaxonomyProposal): void {
  if (!proposal.nearest_id) return
  const draft = draftFor(proposal)
  draft.targetTermId = proposal.nearest_id
  const term = termFor(proposal, proposal.nearest_id)
  if (term) draft.mergeSearch = term.name
}

function onKeydown(event: KeyboardEvent): void {
  if (props.open && event.key === 'Escape') emit('close')
}

onMounted(() => window.addEventListener('keydown', onKeydown))
onBeforeUnmount(() => window.removeEventListener('keydown', onKeydown))
</script>

<template>
  <Teleport to="body">
    <div
      v-if="open"
      class="qb-drawer-layer taxonomy-review-layer"
      role="presentation"
      @click.self="emit('close')"
    >
      <aside
        class="taxonomy-review"
        role="dialog"
        aria-modal="true"
        aria-labelledby="taxonomy-review-title"
      >
        <header class="taxonomy-review__header">
          <div>
            <p class="qb-eyebrow">CONTROLLED VOCABULARY</p>
            <h2 id="taxonomy-review-title">审核 AI 新造词</h2>
            <p>这些词尚未进入正式词表，也不会自动写入题目。请逐个批准、修改、合并或拒绝。</p>
          </div>
          <button
            ref="closeButton"
            type="button"
            class="qb-drawer-close"
            aria-label="关闭新词审核"
            @click="emit('close')"
          >
            ×
          </button>
        </header>

        <div class="taxonomy-review__toolbar">
          <div class="taxonomy-review__summary">
            <strong>{{ store.pendingCount }}</strong>
            <span>个词等待教师确认</span>
            <small>词表版本 {{ store.revision }}</small>
          </div>
          <button
            type="button"
            class="qb-button is-quiet"
            :disabled="store.loadState === 'loading' || store.writeState === 'saving'"
            @click="store.load()"
          >
            {{ store.loadState === 'loading' ? '正在读取…' : '刷新候选' }}
          </button>
        </div>

        <nav class="taxonomy-review__dimensions" aria-label="按标签维度筛选">
          <button
            type="button"
            :class="{ 'is-active': activeDimension === 'all' }"
            @click="activeDimension = 'all'"
          >
            全部 <span>{{ store.pendingCount }}</span>
          </button>
          <button
            v-for="dimension in TAXONOMY_DIMENSIONS"
            :key="dimension"
            type="button"
            :class="{ 'is-active': activeDimension === dimension }"
            @click="activeDimension = dimension"
          >
            {{ dimensionLabels[dimension] }}
            <span>{{ dimensionCounts[dimension] }}</span>
          </button>
        </nav>

        <p
          v-if="store.message"
          class="qb-feedback taxonomy-review__feedback"
          :class="{
            'is-warning': store.writeState === 'conflict',
            'is-error': store.writeState === 'error',
          }"
          role="status"
        >
          {{ store.message }}
        </p>

        <div v-if="store.loadState === 'loading' && !store.proposals.length" class="taxonomy-review__state">
          正在读取新词候选…
        </div>
        <div v-else-if="store.loadState === 'error' && !store.proposals.length" class="taxonomy-review__state is-error">
          <strong>候选清单暂时无法读取</strong>
          <p>可以保留当前窗口，稍后重新读取。</p>
          <button type="button" class="qb-button" @click="store.load()">重新读取</button>
        </div>
        <div v-else-if="store.loadState === 'empty' || pendingProposals.length === 0" class="taxonomy-review__state">
          <strong>当前没有待审核新词</strong>
          <p>AI 继续使用现有规范词；以后出现新候选时会在这里集中显示。</p>
        </div>
        <div v-else-if="visibleGroups.length === 0" class="taxonomy-review__state">
          <strong>这个维度暂时没有候选</strong>
          <button type="button" class="qb-link" @click="activeDimension = 'all'">查看全部候选</button>
        </div>

        <div v-else class="taxonomy-review__groups">
          <section
            v-for="group in visibleGroups"
            :key="group.dimension"
            class="taxonomy-review__group"
            :aria-labelledby="`taxonomy-group-${group.dimension}`"
          >
            <header>
              <h3 :id="`taxonomy-group-${group.dimension}`">{{ group.label }}</h3>
              <span>{{ group.items.length }} 个待确认</span>
            </header>

            <article
              v-for="proposal in group.items"
              :key="proposal.id"
              class="taxonomy-candidate"
              :class="{ 'is-busy': store.busyProposalId === proposal.id }"
            >
              <div class="taxonomy-candidate__identity">
                <span class="taxonomy-candidate__flag">AI 新造词</span>
                <h4>{{ proposal.proposed_name }}</h4>
                <time>{{ formatDate(proposal.created_at) }}</time>
              </div>

              <dl class="taxonomy-candidate__evidence">
                <div v-if="proposal.reason">
                  <dt>提出理由</dt>
                  <dd>{{ proposal.reason }}</dd>
                </div>
                <div v-if="proposal.why_not_reuse">
                  <dt>未复用现有词</dt>
                  <dd>{{ proposal.why_not_reuse }}</dd>
                </div>
                <div v-if="proposal.question_refs.length">
                  <dt>关联题目</dt>
                  <dd>
                    <span v-for="questionId in proposal.question_refs" :key="questionId">
                      #{{ questionId }}
                    </span>
                  </dd>
                </div>
              </dl>

              <div
                v-if="termFor(proposal, proposal.nearest_id)"
                class="taxonomy-candidate__nearest"
              >
                <span>系统找到近似规范词</span>
                <strong>{{ termFor(proposal, proposal.nearest_id)?.name }}</strong>
                <button type="button" class="qb-link" @click="useNearest(proposal)">
                  选为合并目标
                </button>
              </div>

              <div class="taxonomy-candidate__decision">
                <label>
                  <span>规范名称</span>
                  <input
                    v-model="draftFor(proposal).editedName"
                    type="text"
                    maxlength="36"
                    autocomplete="off"
                    :disabled="store.busyProposalId === proposal.id"
                  >
                </label>
                <div class="taxonomy-candidate__primary-actions">
                  <button
                    type="button"
                    class="qb-button is-primary"
                    :disabled="store.busyProposalId === proposal.id"
                    @click="approve(proposal)"
                  >
                    直接批准
                  </button>
                  <button
                    type="button"
                    class="qb-button"
                    :disabled="
                      !draftFor(proposal).editedName.trim()
                      || store.busyProposalId === proposal.id
                    "
                    @click="approveEdited(proposal)"
                  >
                    修改后批准
                  </button>
                </div>
              </div>

              <details class="taxonomy-candidate__merge">
                <summary>合并到已有规范词</summary>
                <div>
                  <label>
                    <span>搜索已有词</span>
                    <input
                      v-model="draftFor(proposal).mergeSearch"
                      type="search"
                      placeholder="输入名称或别名"
                      autocomplete="off"
                    >
                  </label>
                  <label>
                    <span>合并目标</span>
                    <select v-model="draftFor(proposal).targetTermId">
                      <option value="">请选择同维度规范词</option>
                      <option
                        v-for="term in matchingTerms(proposal)"
                        :key="term.id"
                        :value="term.id"
                      >
                        {{ term.name }}
                      </option>
                    </select>
                  </label>
                  <button
                    type="button"
                    class="qb-button"
                    :disabled="
                      !draftFor(proposal).targetTermId
                      || !termFor(proposal, draftFor(proposal).targetTermId)
                      || store.busyProposalId === proposal.id
                    "
                    @click="merge(proposal)"
                  >
                    确认合并
                  </button>
                </div>
              </details>

              <footer>
                <span>批准后才会成为 AI 今后可选的规范词。</span>
                <button
                  type="button"
                  class="qb-link is-danger"
                  :disabled="store.busyProposalId === proposal.id"
                  @click="reject(proposal)"
                >
                  拒绝这个新词
                </button>
              </footer>
            </article>
          </section>
        </div>
      </aside>
    </div>
  </Teleport>
</template>
