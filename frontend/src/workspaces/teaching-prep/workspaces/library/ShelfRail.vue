<script setup lang="ts">
import { computed } from 'vue'

import StatusBadge from '../../../../components/design-system/StatusBadge.vue'
import { useTeachingPrepCatalogStore } from '../../stores/catalog'
import { isBookRole, roleLabel, type LibrarySelection } from './libraryShared'

const props = defineProps<{ selection: LibrarySelection }>()
const emit = defineEmits<{ select: [selection: LibrarySelection] }>()

const catalog = useTeachingPrepCatalogStore()

// 已停用合集的章文件夹不在资料柜出现（ChapterGrid 仍通过 store 全量列表管理停用/恢复）
const chapterFolders = computed(() => (
  catalog.libraryChapterFolders.filter(folder => folder.collectionActive)
))
const pptFileTotal = computed(() => (
  chapterFolders.value.reduce((total, folder) => total + folder.files.length, 0)
))

// 教材/教辅：启用中的书记录，各一行
const bookRecords = computed(() => (
  catalog.semesterMaterials.filter(item => item.is_active && isBookRole(item.material_role))
))

// 其他资料：启用中的散装记录（不属于任何启用合集、也不是书）与未加入本学期的资料
const looseRecords = computed(() => (
  catalog.semesterMaterials.filter(item => (
    !isBookRole(item.material_role)
    && !catalog.collectionByRecordId.has(item.id)
  ))
))
const unattachedMaterials = computed(() => (
  catalog.materials.filter(item => (
    !catalog.semesterMaterials.some(record => record.current_material_version_id === item.id)
  ))
))

const treeStatus = computed(() => catalog.lessonTreeStatus)
const treeBadge = computed(() => {
  if (treeStatus.value.state === 'active') return { tone: 'success' as const, label: '已生效' }
  if (treeStatus.value.state === 'pending') return { tone: 'warning' as const, label: '待确认' }
  return { tone: 'neutral' as const, label: '未开始' }
})
const treeSubLabel = computed(() => {
  if (treeStatus.value.state === 'active') {
    return `${treeStatus.value.activeLessonCount} 个课时已生效`
  }
  if (treeStatus.value.state === 'pending') {
    return `${treeStatus.value.proposalChapterCount} 章 ${treeStatus.value.proposalLessonCount} 课时 · 来自课件文件夹与命名`
  }
  return '导入课件文件夹后自动生成建议'
})

function isActive(selection: LibrarySelection): boolean {
  const current = props.selection
  if (selection.kind !== current.kind) return false
  if (selection.kind === 'chapter' && current.kind === 'chapter') {
    return selection.folderKey === current.folderKey
  }
  if (selection.kind === 'material' && current.kind === 'material') {
    return selection.materialId === current.materialId
  }
  return true
}

function select(selection: LibrarySelection): void {
  emit('select', selection)
}
</script>

<template>
  <aside class="tp-rail" aria-label="资料柜">
    <button
      type="button"
      class="tp-rail__item"
      :class="{ 'is-active': isActive({ kind: 'import' }) }"
      @click="select({ kind: 'import' })"
    >
      <span class="tp-rail__item-name">导入资料</span>
    </button>

    <template v-if="chapterFolders.length">
      <p class="tp-rail__group-label">课件 · {{ pptFileTotal }} 份</p>
      <button
        v-for="folder in chapterFolders"
        :key="folder.key"
        type="button"
        class="tp-rail__item"
        :class="{ 'is-active': isActive({ kind: 'chapter', folderKey: folder.key }) }"
        @click="select({ kind: 'chapter', folderKey: folder.key })"
      >
        <span class="tp-rail__item-name">{{ folder.name }}</span>
        <span class="tp-rail__count">{{ folder.files.length }}</span>
      </button>
    </template>

    <template v-if="bookRecords.length">
      <p class="tp-rail__group-label">书 · {{ bookRecords.length }} 本</p>
      <button
        v-for="record in bookRecords"
        :key="record.id"
        type="button"
        class="tp-rail__item"
        :class="{ 'is-active': isActive({ kind: 'material', materialId: record.current_material_version_id }) }"
        @click="select({ kind: 'material', materialId: record.current_material_version_id })"
      >
        <span class="tp-rail__item-name">{{ roleLabel(record.material_role) }} · {{ record.display_name }}</span>
        <span class="tp-rail__count">{{ record.current_unit_count ?? 0 }} 页</span>
      </button>
    </template>

    <template v-if="looseRecords.length || unattachedMaterials.length">
      <p class="tp-rail__group-label">其他资料</p>
      <button
        v-for="record in looseRecords"
        :key="record.id"
        type="button"
        class="tp-rail__item"
        :class="{
          'is-active': isActive({ kind: 'material', materialId: record.current_material_version_id }),
          'is-inactive': !record.is_active,
        }"
        @click="select({ kind: 'material', materialId: record.current_material_version_id })"
      >
        <span class="tp-rail__item-name">
          {{ record.display_name }}<template v-if="!record.is_active">（已移出）</template>
        </span>
        <span class="tp-rail__count">{{ roleLabel(record.material_role) }}</span>
      </button>
      <button
        v-for="item in unattachedMaterials"
        :key="item.id"
        type="button"
        class="tp-rail__item"
        :class="{ 'is-active': isActive({ kind: 'material', materialId: item.id }) }"
        @click="select({ kind: 'material', materialId: item.id })"
      >
        <span class="tp-rail__item-name">{{ item.display_name }}</span>
        <span class="tp-rail__count">未归类</span>
      </button>
    </template>

    <p class="tp-rail__group-label">课时树</p>
    <button
      type="button"
      class="tp-rail__item"
      :class="{ 'is-active': isActive({ kind: 'tree' }) }"
      data-testid="shelf-tree-entry"
      @click="select({ kind: 'tree' })"
    >
      <span class="tp-rail__item-name">本学期课时树</span>
      <StatusBadge :tone="treeBadge.tone" :label="treeBadge.label" />
    </button>
    <p class="tp-rail__sub">{{ treeSubLabel }}</p>
  </aside>
</template>
