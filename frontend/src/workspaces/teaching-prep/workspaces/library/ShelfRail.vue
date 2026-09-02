<script setup lang="ts">
import { computed } from 'vue'
import { Folder, FolderOpen } from '@lucide/vue'

import StatusBadge from '../../../../components/design-system/StatusBadge.vue'
import { FileTreeFolder } from '@/components/ui/file-tree'
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
  catalog.libraryMaterialRecords.filter(item => item.is_active && isBookRole(item.material_role))
))

const looseRecords = computed(() => (
  catalog.libraryMaterialRecords.filter(item => (
    !isBookRole(item.material_role)
    && !catalog.collectionByRecordId.has(item.id)
  ))
))
const attachedVersionIds = computed(() => new Set(
  (catalog.selectedSemester
    ? catalog.semesterMaterials
    : catalog.allSemesterMaterialRecords
  ).map(item => item.current_material_version_id),
))
const unattachedMaterials = computed(() => (
  catalog.materials.filter(item => !attachedVersionIds.value.has(item.id))
))
const unattachedLabel = computed(() => (
  catalog.selectedSemester ? '未加入本学期' : '未归类'
))

const treeStatus = computed(() => catalog.lessonTreeStatus)
const treeBadge = computed(() => {
  if (treeStatus.value.state === 'active') return { tone: 'success' as const, label: '已生效' }
  return { tone: 'neutral' as const, label: '未开始' }
})
const treeSubLabel = computed(() => {
  if (treeStatus.value.state === 'active') {
    return `${treeStatus.value.activeLessonCount} 个课时已生效`
  }
  return '还没有课时，可回备课首页新建'
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
      class="tp-rail__item tp-rail__import"
      :class="{ 'is-active': isActive({ kind: 'import' }) }"
      data-testid="shelf-import-entry"
      @click="select({ kind: 'import' })"
    >
      <span class="tp-rail__item-name">导入资料</span>
    </button>

    <div class="tp-rail__body">
    <FileTreeFolder v-if="chapterFolders.length" name="课件" default-expanded>
      <template #header="{ expanded }">
        <component :is="expanded ? FolderOpen : Folder" :size="15" aria-hidden="true" />
        <span>课件 · {{ pptFileTotal }} 份</span>
      </template>
      <button
        v-for="folder in chapterFolders"
        :key="folder.key"
        type="button"
        class="tp-rail__item"
        :class="{ 'is-active': isActive({ kind: 'chapter', folderKey: folder.key }) }"
        @click="select({ kind: 'chapter', folderKey: folder.key })"
      >
        <span class="tp-rail__item-name" :title="folder.name">{{ folder.name }}</span>
        <span class="tp-rail__count">{{ folder.files.length }}</span>
      </button>
    </FileTreeFolder>

    <FileTreeFolder v-if="bookRecords.length" name="书" default-expanded>
      <template #header="{ expanded }">
        <component :is="expanded ? FolderOpen : Folder" :size="15" aria-hidden="true" />
        <span>书 · {{ bookRecords.length }} 本</span>
      </template>
      <button
        v-for="record in bookRecords"
        :key="record.id"
        type="button"
        class="tp-rail__item"
        :class="{ 'is-active': isActive({ kind: 'material', materialId: record.current_material_version_id }) }"
        @click="select({ kind: 'material', materialId: record.current_material_version_id })"
      >
        <span class="tp-rail__item-name" :title="`${roleLabel(record.material_role)} · ${record.display_name}`">{{ roleLabel(record.material_role) }} · {{ record.display_name }}</span>
        <span class="tp-rail__count">{{ record.current_unit_count ?? 0 }} 页</span>
      </button>
    </FileTreeFolder>

    <FileTreeFolder v-if="looseRecords.length || unattachedMaterials.length" name="其他资料" default-expanded>
      <template #header="{ expanded }">
        <component :is="expanded ? FolderOpen : Folder" :size="15" aria-hidden="true" />
        <span>其他资料</span>
      </template>
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
        <span class="tp-rail__item-name" :title="record.is_active ? record.display_name : `${record.display_name}（已移出）`">
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
        <span class="tp-rail__item-name" :title="item.display_name">{{ item.display_name }}</span>
        <span class="tp-rail__count">{{ unattachedLabel }}</span>
      </button>
    </FileTreeFolder>

    <FileTreeFolder v-if="catalog.selectedSemester" name="课时树" default-expanded>
      <template #header="{ expanded }">
        <component :is="expanded ? FolderOpen : Folder" :size="15" aria-hidden="true" />
        <span>课时树</span>
      </template>
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
    </FileTreeFolder>
    </div>
  </aside>
</template>
