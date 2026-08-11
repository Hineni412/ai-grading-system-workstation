<script setup lang="ts">
import { computed, ref } from 'vue'

import AppButton from '../../../../components/design-system/AppButton.vue'
import StatusBadge from '../../../../components/design-system/StatusBadge.vue'
import { teachingPrepCatalogApi } from '../../api/catalog'
import { useTeachingPrepCatalogStore, type LibraryChapterFolder } from '../../stores/catalog'
import type { LibrarySelection } from './libraryShared'

const props = defineProps<{ folderKey: string }>()
const emit = defineEmits<{
  select: [selection: LibrarySelection]
  notice: [message: string]
}>()

const catalog = useTeachingPrepCatalogStore()
const togglingCollection = ref(false)

const folder = computed<LibraryChapterFolder | null>(() => (
  catalog.libraryChapterFolders.find(item => item.key === props.folderKey) ?? null
))

const valueHint = computed(() => (
  folder.value ? catalog.chapterValueHints.get(folder.value.name) ?? null : null
))

const folderStatus = computed(() => {
  const files = folder.value?.files ?? []
  if (!folder.value?.collectionActive) return { tone: 'neutral' as const, label: '已停用' }
  if (files.some(file => file.parsing)) return { tone: 'info' as const, label: '正在逐页处理' }
  if (files.some(file => file.needsContinue)) return { tone: 'warning' as const, label: '部分未完成' }
  return { tone: 'success' as const, label: '已处理好' }
})

function fileStatusLabel(file: LibraryChapterFolder['files'][number]): string {
  if (file.parsing) return '解析中'
  if (file.needsContinue) return '需继续解析'
  return '已处理好'
}

const valueHintText = computed(() => {
  const current = folder.value
  if (!current) return ''
  const hint = valueHint.value
  const parts: string[] = []
  if (hint?.lessonCount) {
    parts.push(`本章 ${current.files.length} 份课件已对应 ${hint.lessonCount} 个课时`)
  } else {
    parts.push(`本章共 ${current.files.length} 份课件`)
  }
  const ranges: string[] = []
  if (hint?.textbookRange) ranges.push(`教材 ${hint.textbookRange}`)
  if (hint?.exerciseRange) ranges.push(`教辅练习 ${hint.exerciseRange}`)
  if (ranges.length) {
    parts.push(`${ranges.join('、')}已关联——备课时打开本章任一课时，这些资料都在那儿等着。`)
  } else {
    parts.push('确认课时树并对应教材/教辅后，备课时打开本章课时即可直接取用这些资料。')
  }
  return parts.join('，')
})

async function setCollectionActive(isActive: boolean): Promise<void> {
  const current = folder.value
  if (!current || togglingCollection.value) return
  if (!isActive && !window.confirm(
    `停用后合集不再显示章文件夹，可随时恢复；不影响已完成的课件改编。确定停用「${current.collectionName}」吗？`,
  )) return
  togglingCollection.value = true
  try {
    await teachingPrepCatalogApi.updateReferencePptCollection(current.collectionId, {
      is_active: isActive,
    })
    await catalog.refreshReferencePptCollections()
    emit('notice', isActive
      ? `已恢复合集「${current.collectionName}」。`
      : `已停用合集「${current.collectionName}」。`)
  } catch {
    emit('notice', '合集状态没有保存，请刷新后重试。')
  } finally {
    togglingCollection.value = false
  }
}
</script>

<template>
  <section v-if="folder" class="tp-panel" aria-label="章文件夹">
    <div class="tp-panel__body">
      <div class="tp-main-head">
        <div>
          <span class="tp-eyebrow">课件 · 章文件夹</span>
          <h2>{{ folder.name }}</h2>
          <p>
            {{ folder.collectionName }} · {{ folder.files.length }} 份课件 · 共 {{ folder.totalPages }} 页
          </p>
        </div>
        <StatusBadge :tone="folderStatus.tone" :label="folderStatus.label" />
      </div>

      <p class="tp-value-hint">{{ valueHintText }}</p>

      <div class="tp-file-grid" data-testid="chapter-file-grid">
        <button
          v-for="file in folder.files"
          :key="file.recordId"
          type="button"
          class="tp-file-cell"
          :disabled="!file.materialVersionId"
          @click="emit('select', { kind: 'material', materialId: file.materialVersionId })"
        >
          <b>{{ file.name }}</b>
          <small>PPTX · {{ file.unitCount }} 页 · {{ fileStatusLabel(file) }}</small>
        </button>
      </div>

      <div class="tp-inline-actions">
        <AppButton
          v-if="folder.collectionActive"
          variant="ghost"
          :disabled="togglingCollection"
          @click="setCollectionActive(false)"
        >
          停用此课件文件夹
        </AppButton>
        <AppButton
          v-else
          variant="ghost"
          :disabled="togglingCollection"
          @click="setCollectionActive(true)"
        >
          恢复此课件文件夹
        </AppButton>
      </div>
    </div>
  </section>
</template>
