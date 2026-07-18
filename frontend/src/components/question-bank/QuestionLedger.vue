<script setup lang="ts">
import { computed } from 'vue'
import { useRouter } from 'vue-router'

import { useAssemblyStore } from '../../stores/assembly'
import { useQuestionBankStore } from '../../stores/question-bank'

const store = useQuestionBankStore()
const assembly = useAssemblyStore()
const router = useRouter()
const allOnPage = computed(() => (
  store.questions.length > 0
  && store.selectedOnPageCount === store.questions.length
))

function changePage(next: number): void {
  if (next < 1 || next > store.totalPages || next === store.page) return
  void store.loadQuestions({ ...store.appliedFilters, page: next })
}

async function addSelectionToAssembly(): Promise<void> {
  if (store.selectedCount === 0 || assembly.saveState === 'saving') return
  if (assembly.loadState === 'idle') await assembly.load()
  const added = await assembly.addQuestions(store.selectedQuestionIds)
  if (added) await router.push('/question-assembly')
}
</script>

<template>
  <section class="qb-ledger" aria-labelledby="qb-ledger-title">
    <header class="qb-section-heading">
      <div>
        <p class="qb-eyebrow">Question ledger</p>
        <h2 id="qb-ledger-title">连续题目台账</h2>
      </div>
      <strong>共 {{ store.total }} 题</strong>
    </header>

    <div v-if="store.selectedCount" class="qb-selection-bar" role="status">
      <span>
        已选 <strong>{{ store.selectedCount }}</strong> 题
        <template v-if="store.hiddenSelectionCount">，其中 {{ store.hiddenSelectionCount }} 题在其他页</template>
      </span>
      <span class="qb-selection-bar__actions">
        <button
          type="button"
          class="qb-button qb-button--primary"
          :disabled="assembly.saveState === 'saving'"
          @click="addSelectionToAssembly"
        >
          加入组卷篮
        </button>
        <button type="button" class="qb-link" @click="store.clearSelection">清空选择</button>
      </span>
    </div>

    <p v-if="store.listState === 'stale-error'" class="qb-feedback is-warning" role="alert">
      新数据暂时无法读取，当前仍显示上一次成功结果。
    </p>
    <div v-if="store.listState === 'loading' && store.questions.length === 0" class="qb-empty" role="status">
      正在读取题库...
    </div>
    <div v-else-if="store.listState === 'error'" class="qb-empty" role="alert">
      <span>{{ store.listError }}</span>
      <button type="button" class="qb-link" @click="store.loadQuestions(store.appliedFilters)">重新读取</button>
    </div>
    <div v-else-if="store.listState === 'empty'" class="qb-empty">
      当前条件下没有题目。可以清除筛选，或从“导入与任务”加入试卷。
    </div>
    <div v-else class="qb-ledger__table">
      <table>
        <thead>
          <tr>
            <th class="qb-binding" scope="col">
              <input
                type="checkbox"
                aria-label="选择当前页全部题目"
                :checked="allOnPage"
                @change="store.selectCurrentPage(($event.currentTarget as HTMLInputElement).checked)"
              >
            </th>
            <th scope="col">题号</th>
            <th scope="col">题目摘要</th>
            <th scope="col">来源</th>
            <th scope="col">标签</th>
            <th scope="col"><span class="sr-only">操作</span></th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="question in store.questions"
            :key="question.id"
            :class="{ 'is-current': store.selectedQuestionId === question.id }"
          >
            <td class="qb-binding">
              <input
                type="checkbox"
                :aria-label="`选择第 ${question.question_number || question.id} 题`"
                :checked="store.selectedQuestionIds.includes(question.id)"
                :disabled="
                  store.selectionIsFull
                  && !store.selectedQuestionIds.includes(question.id)
                "
                @change="store.toggleQuestionSelection(
                  question.id,
                  ($event.currentTarget as HTMLInputElement).checked,
                )"
              >
            </td>
            <td class="qb-ledger__number">
              <strong>{{ question.question_number || '-' }}</strong>
              <small>#{{ question.id }}</small>
            </td>
            <td>
              <button
                type="button"
                class="qb-question-link"
                :aria-label="`打开第 ${question.question_number || question.id} 题详情`"
                @click="store.selectQuestion(question.id)"
              >
                {{ question.question_text || '题干暂缺' }}
              </button>
              <span class="qb-inline-meta">
                {{ question.question_type || '未分类' }}
                · 难度 {{ question.difficulty || '-' }}
                <template v-if="question.has_images"> · 含图片</template>
              </span>
            </td>
            <td>
              <strong>{{ question.paper_title || '未命名试卷' }}</strong>
              <span class="qb-inline-meta">
                {{ [question.year, question.grade, question.exam_type].filter(Boolean).join(' · ') || '来源信息待补' }}
              </span>
            </td>
            <td>
              <span v-if="question.tags.length" class="qb-tag-count">{{ question.tags.length }} 个</span>
              <span v-else class="qb-tag-count is-empty">待标注</span>
            </td>
            <td>
              <button type="button" class="qb-link" @click="store.selectQuestion(question.id)">查看</button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <footer v-if="store.totalPages > 1" class="qb-pagination">
      <button type="button" class="qb-link" :disabled="store.page <= 1" @click="changePage(store.page - 1)">
        上一页
      </button>
      <span>第 {{ store.page }} / {{ store.totalPages }} 页</span>
      <button type="button" class="qb-link" :disabled="store.page >= store.totalPages" @click="changePage(store.page + 1)">
        下一页
      </button>
    </footer>
  </section>
</template>
