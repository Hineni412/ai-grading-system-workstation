<script setup lang="ts">
import { computed, ref, watch } from 'vue'

import type { GraphNode } from '../../api/graph'
import { buildEvidenceLaneModel } from '../../features/knowledge-graph/model'

const props = withDefaults(defineProps<{
  nodes: GraphNode[]
  selectedKey: string | null
  pageSize?: number
}>(), {
  pageSize: 50,
})

const emit = defineEmits<{ selectNode: [knowledgeKey: string] }>()
const search = ref('')
const page = ref(1)

const filteredNodes = computed(() => {
  const term = search.value.trim().toLocaleLowerCase('zh-CN')
  const models = buildEvidenceLaneModel(props.nodes)
  return term
    ? models.filter((node) => (
        node.label.toLocaleLowerCase('zh-CN').includes(term) ||
        node.knowledgeKey.toLocaleLowerCase('zh-CN').includes(term)
      ))
    : models
})

const totalPages = computed(() => Math.max(1, Math.ceil(filteredNodes.value.length / props.pageSize)))
const visibleNodes = computed(() => {
  const start = (page.value - 1) * props.pageSize
  return filteredNodes.value.slice(start, start + props.pageSize)
})

watch([search, () => props.nodes], () => { page.value = 1 }, { deep: true })
watch(totalPages, (value) => { page.value = Math.min(page.value, value) })

function select(knowledgeKey: string): void {
  emit('selectNode', knowledgeKey)
}

function onDirectoryKeydown(event: KeyboardEvent): void {
  const target = event.target
  if (!(target instanceof HTMLButtonElement) || !target.matches('[data-testid="graph-directory-item"]')) return
  const list = event.currentTarget
  if (!(list instanceof HTMLElement)) return
  const buttons = [...list.querySelectorAll<HTMLButtonElement>('[data-testid="graph-directory-item"]')]
  const index = buttons.indexOf(target)
  if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
    event.preventDefault()
    const direction = event.key === 'ArrowDown' ? 1 : -1
    buttons[(index + direction + buttons.length) % buttons.length]?.focus()
  } else if (event.key === 'Enter') {
    event.preventDefault()
    const key = target.dataset.knowledgeKey
    if (key) select(key)
  }
}
</script>

<template>
  <section class="knowledge-graph-directory" aria-labelledby="knowledge-graph-directory-title">
    <header>
      <div>
        <h2 id="knowledge-graph-directory-title">知识标签文字目录</h2>
        <p>可使用键盘方向键浏览，按回车查看证据。</p>
      </div>
      <label>
        <span>搜索知识标签</span>
        <input v-model="search" type="search" autocomplete="off" placeholder="输入标签名称">
      </label>
    </header>

    <p v-if="filteredNodes.length === 0" class="knowledge-graph-empty-copy">没有匹配的知识标签</p>
    <ul v-else class="knowledge-graph-directory-list" @keydown="onDirectoryKeydown">
      <li v-for="node in visibleNodes" :key="node.knowledgeKey">
        <button
          type="button"
          data-testid="graph-directory-item"
          :data-knowledge-key="node.knowledgeKey"
          :class="{ 'is-selected': selectedKey === node.knowledgeKey }"
          :aria-current="selectedKey === node.knowledgeKey ? 'true' : undefined"
          :aria-label="`${node.label}，得分率 ${node.percentLabel}，${node.bandLabel}，${node.itemCount} 条证据`"
          @click="select(node.knowledgeKey)"
        >
          <span class="knowledge-graph-directory-list__name">{{ node.label }}</span>
          <span>{{ node.percentLabel }} · {{ node.bandLabel }}</span>
          <span>{{ node.itemCount }} 条证据 · {{ node.studentCount }} 名学生</span>
        </button>
      </li>
    </ul>

    <footer v-if="totalPages > 1" class="knowledge-graph-directory-pagination">
      <button type="button" :disabled="page <= 1" @click="page -= 1">上一页</button>
      <span>第 {{ page }} / {{ totalPages }} 页，共 {{ filteredNodes.length }} 个标签</span>
      <button type="button" :disabled="page >= totalPages" @click="page += 1">下一页</button>
    </footer>
  </section>
</template>
