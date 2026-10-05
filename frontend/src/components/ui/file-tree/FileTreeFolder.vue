<!--
  可折叠文件树文件夹节点：头部触发器（箭头 + 文件夹图标 + 名称/自定义插槽）+ 左侧竖线缩进子级。
  生产化自原型 ui-component-proposals · inspira/file-tree（Folder.vue 与 TreeIndicator.vue 合并）；
  原 Tree/File 的 provide/inject 选中机制未移植——资料柜条目本身是业务按钮，选中态由调用方维护。
-->
<script setup lang="ts">
import { ChevronRight, Folder, FolderOpen } from '@lucide/vue'
import { ref } from 'vue'

const props = withDefaults(defineProps<{
  name: string
  defaultExpanded?: boolean
}>(), {
  defaultExpanded: false,
})

const expanded = ref(props.defaultExpanded)

function toggle(): void {
  expanded.value = !expanded.value
}
</script>

<template>
  <div class="file-tree-folder">
    <button
      type="button"
      class="file-tree-folder__trigger"
      :aria-expanded="expanded"
      @click="toggle"
    >
      <ChevronRight class="file-tree-folder__chevron" :class="{ 'is-expanded': expanded }" :size="14" aria-hidden="true" />
      <slot name="header" :expanded="expanded">
        <component :is="expanded ? FolderOpen : Folder" :size="15" aria-hidden="true" />
        <span class="file-tree-folder__name">{{ name }}</span>
      </slot>
    </button>
    <div v-if="expanded" class="file-tree-folder__children">
      <span class="file-tree-folder__indicator" aria-hidden="true" />
      <div class="file-tree-folder__items">
        <slot />
      </div>
    </div>
  </div>
</template>

<style scoped>
.file-tree-folder__trigger {
  display: flex;
  align-items: center;
  gap: 5px;
  width: 100%;
  margin: 8px 0 2px;
  padding: 2px 4px;
  border: none;
  border-radius: calc(var(--radius) - 2px);
  background: none;
  color: var(--muted-foreground);
  font: inherit;
  font-size: var(--font-size-caption, 12px);
  font-weight: var(--font-weight-semibold);
  letter-spacing: 0.06em;
  text-align: left;
  cursor: pointer;
}

.file-tree-folder__trigger:hover {
  background: var(--muted);
  color: var(--foreground);
}

.file-tree-folder__chevron {
  flex: none;
  transition: transform 0.15s ease-in-out;
}

.file-tree-folder__chevron.is-expanded {
  transform: rotate(90deg);
}

.file-tree-folder__name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.file-tree-folder__children {
  position: relative;
}

.file-tree-folder__indicator {
  position: absolute;
  top: 0;
  bottom: 4px;
  left: 10px;
  width: 1px;
  border-radius: 1px;
  background: var(--border);
}

.file-tree-folder__items {
  display: grid;
  gap: 1px;
  padding: 2px 0 4px 16px;
}
</style>
