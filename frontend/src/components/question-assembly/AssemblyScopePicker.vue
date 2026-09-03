<script setup lang="ts">
import { computed } from 'vue'

import type { CurriculumCatalog } from '../../api/question-bank'

const props = defineProps<{
  catalog: CurriculumCatalog | null
  selectedKeys: string[]
}>()

const emit = defineEmits<{
  toggle: [key: string, checked: boolean, descendants: string[]]
}>()

const selected = computed(() => new Set(props.selectedKeys))

interface ScopeNode {
  key: string
  label: string
  children: ScopeNode[]
}

const volumes = computed(() => (props.catalog?.volumes ?? []).map((volume) => ({
  key: volume.id,
  label: volume.label,
  chapters: volume.chapters.map((chapter): ScopeNode => ({
    key: chapter.id,
    label: chapter.display_name || chapter.title,
    children: chapter.sections.map((section): ScopeNode => ({
      key: section.id,
      label: section.display_name || section.title,
      children: section.knowledge_points.map((point): ScopeNode => ({
        key: point.id,
        label: point.display_name,
        children: [],
      })),
    })),
  })),
})))

const labelByKey = computed(() => {
  const map = new Map<string, string>()
  const walk = (node: ScopeNode) => {
    map.set(node.key, node.label)
    node.children.forEach(walk)
  }
  for (const volume of volumes.value) volume.chapters.forEach(walk)
  return map
})

const selectedLabels = computed(() => (
  props.selectedKeys
    .map((key) => ({ key, label: labelByKey.value.get(key) ?? key }))
))

function descendantKeys(node: ScopeNode): string[] {
  const keys: string[] = []
  const walk = (child: ScopeNode) => {
    keys.push(child.key)
    child.children.forEach(walk)
  }
  node.children.forEach(walk)
  return keys
}

function onToggle(node: ScopeNode, event: Event): void {
  const checked = (event.target as HTMLInputElement).checked
  emit('toggle', node.key, checked, descendantKeys(node))
}

function remove(key: string): void {
  emit('toggle', key, false, [])
}
</script>

<template>
  <div class="ai-scope-picker" data-testid="ai-scope-picker">
    <div v-if="selectedLabels.length" class="ai-scope-picker__tags" aria-label="已选范围">
      <span v-for="item in selectedLabels" :key="item.key" class="ai-scope-picker__tag">
        {{ item.label }}
        <button type="button" :aria-label="`取消 ${item.label}`" @click="remove(item.key)">×</button>
      </span>
    </div>
    <p v-if="!catalog" class="ai-scope-picker__hint">正在读取教材目录…</p>
    <div v-else class="ai-scope-picker__tree">
      <details v-for="volume in volumes" :key="volume.key" class="ai-scope-picker__volume">
        <summary>{{ volume.label }}</summary>
        <div v-for="chapter in volume.chapters" :key="chapter.key" class="ai-scope-picker__chapter">
          <label>
            <input
              type="checkbox"
              :checked="selected.has(chapter.key)"
              @change="onToggle(chapter, $event)"
            >
            <strong>{{ chapter.label }}</strong>
          </label>
          <details v-if="chapter.children.length">
            <summary>小节与知识点（{{ chapter.children.length }}）</summary>
            <div v-for="section in chapter.children" :key="section.key" class="ai-scope-picker__section">
              <label>
                <input
                  type="checkbox"
                  :checked="selected.has(section.key)"
                  @change="onToggle(section, $event)"
                >
                {{ section.label }}
              </label>
              <div v-if="section.children.length" class="ai-scope-picker__points">
                <label v-for="point in section.children" :key="point.key">
                  <input
                    type="checkbox"
                    :checked="selected.has(point.key)"
                    @change="onToggle(point, $event)"
                  >
                  {{ point.label }}
                </label>
              </div>
            </div>
          </details>
        </div>
      </details>
    </div>
  </div>
</template>

<style scoped>
.ai-scope-picker {
  background: var(--card);
  border: 1px solid var(--border);
  border-radius: var(--radius-panel, 10px);
  display: grid;
  gap: 10px;
  max-height: 420px;
  overflow: auto;
  padding: 12px;
}

.ai-scope-picker__tags {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.ai-scope-picker__tag {
  align-items: center;
  background: var(--muted, #f1f5f9);
  border-radius: 999px;
  display: inline-flex;
  font-size: 12px;
  gap: 4px;
  padding: 2px 8px;
}

.ai-scope-picker__tag button {
  background: none;
  border: 0;
  color: var(--color-text-secondary);
  cursor: pointer;
  padding: 0 2px;
}

.ai-scope-picker__hint {
  color: var(--color-text-secondary);
  font-size: 13px;
  margin: 0;
}

.ai-scope-picker__tree {
  display: grid;
  font-size: 13px;
  gap: 6px;
}

.ai-scope-picker__chapter {
  display: grid;
  gap: 4px;
  padding-left: 12px;
}

.ai-scope-picker__section {
  display: grid;
  gap: 2px;
  padding-left: 18px;
}

.ai-scope-picker__points {
  display: flex;
  flex-wrap: wrap;
  gap: 4px 12px;
  padding-left: 24px;
}

.ai-scope-picker label {
  align-items: center;
  cursor: pointer;
  display: inline-flex;
  gap: 6px;
}
</style>
