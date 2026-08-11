<script setup lang="ts">
import { computed, ref } from 'vue'

import AppButton from '../../../../components/design-system/AppButton.vue'
import StatusBadge from '../../../../components/design-system/StatusBadge.vue'
import { teachingPrepWorkbenchApi } from '../../api/workbench'
import { useTeachingPrepCatalogStore, type LessonTreeChapterState } from '../../stores/catalog'
import type { LibrarySelection } from './libraryShared'

const emit = defineEmits<{
  select: [selection: LibrarySelection]
  notice: [message: string]
}>()

const catalog = useTeachingPrepCatalogStore()

const proposal = computed(() => catalog.localLessonTreeProposal)
const chapters = computed(() => catalog.lessonTreeChapters)
const confirmingKey = ref<string | null>(null)

const pptFileTotal = computed(() => (
  catalog.libraryChapterFolders
    .filter(folder => folder.collectionActive)
    .reduce((total, folder) => total + folder.files.length, 0)
))

const pendingChapterCount = computed(() => (
  chapters.value.filter(chapter => !chapter.applied).length
))

// 已有正式课时树且没有待确认建议时，展示正式树（只读）
const formalChapters = computed(() => {
  if (proposal.value) return []
  const nodes = catalog.lessonNodes.filter(item => item.is_active)
  const childrenOf = (parentId: string | null) => nodes
    .filter(item => item.parent_id === parentId)
    .sort((left, right) => left.sort_order - right.sort_order)
  return childrenOf(null)
    .filter(item => item.node_type === 'chapter')
    .map(chapter => ({
      title: chapter.title,
      lessons: childrenOf(chapter.id)
        .flatMap(section => (
          section.node_type === 'lesson' ? [section] : childrenOf(section.id)
        ))
        .filter(item => item.node_type === 'lesson')
        .map(item => item.title),
    }))
})

function chapterStatus(chapter: LessonTreeChapterState): {
  tone: 'success' | 'warning'
  label: string
} {
  return chapter.applied
    ? { tone: 'success', label: '已生效' }
    : { tone: 'warning', label: '待确认' }
}

/** 逐条接受该章待决定映射，直到本章没有 pending；返回 false 表示建议已失效。 */
async function acceptChapterMappings(chapter: LessonTreeChapterState): Promise<boolean> {
  const lessonRefs = new Set(chapter.lessons.map(lesson => `proposal:${lesson.key}`))
  while (true) {
    const current = catalog.semesterMappingProposals.find(
      item => item.id === proposal.value?.id,
    )
    if (!current) return false
    const pending = current.payload.mappings.find(item => (
      lessonRefs.has(item.lesson_ref) && item.decision === 'pending'
    ))
    if (!pending) return true
    const updated = await teachingPrepWorkbenchApi.reviewMapping(
      current.id,
      pending.mapping_id,
      {
        expected_revision: current.revision,
        decision: 'accepted',
        lesson_ref: pending.lesson_ref,
        start_unit: pending.start_unit,
        end_unit: pending.end_unit,
        reason: null,
      },
    )
    const index = catalog.semesterMappingProposals.findIndex(item => item.id === updated.id)
    if (index >= 0) catalog.semesterMappingProposals[index] = updated
  }
}

async function applyChapter(chapter: LessonTreeChapterState): Promise<void> {
  if (!(await acceptChapterMappings(chapter))) {
    emit('notice', '这份课时树建议已失效，请刷新后重新核对。')
    return
  }
  const latest = catalog.semesterMappingProposals.find(
    item => item.id === proposal.value?.id,
  )
  if (!latest) {
    emit('notice', '这份课时树建议已失效，请刷新后重新核对。')
    return
  }
  await catalog.applySemesterMapping(latest, chapter.key)
  emit('notice', `「${chapter.title}」已确认：本章课时与课件关联已生效。`)
}

async function confirmChapter(chapterKey: string): Promise<void> {
  const chapter = chapters.value.find(item => item.key === chapterKey)
  if (!chapter || chapter.applied || confirmingKey.value) return
  confirmingKey.value = chapterKey
  try {
    await applyChapter(chapter)
  } catch {
    emit('notice', catalog.errorMessage || '这一章还没有确认成功；已保存的决定仍保留，可直接重试。')
  } finally {
    confirmingKey.value = null
  }
}

async function confirmAllChapters(): Promise<void> {
  if (confirmingKey.value || pendingChapterCount.value === 0) return
  confirmingKey.value = 'all'
  let failed = false
  try {
    for (const chapter of chapters.value) {
      if (chapter.applied) continue
      try {
        // 逐章串行确认：任一章失败即停下，已确认章节保持生效
        await applyChapter(chapter)
      } catch {
        failed = true
        break
      }
    }
    if (failed) {
      emit('notice', catalog.errorMessage || '有章节没有确认成功；已确认的章节保持生效，可继续逐章确认。')
    }
  } finally {
    confirmingKey.value = null
  }
}
</script>

<template>
  <section class="tp-panel" aria-label="本学期课时树">
    <div class="tp-panel__body">
      <template v-if="proposal">
        <div class="tp-main-head">
          <div>
            <span class="tp-eyebrow">课时树 · 唯一主干</span>
            <h2>由你的 {{ pptFileTotal }} 份课件确定的课时树</h2>
            <p>按章文件夹和课件命名生成，逐章确认即生效；<b>不需要 AI、不产生费用</b>。</p>
          </div>
          <AppButton
            variant="primary"
            data-testid="confirm-all-chapters"
            :disabled="Boolean(confirmingKey) || pendingChapterCount === 0"
            @click="confirmAllChapters"
          >
            {{ confirmingKey ? '正在逐章确认…' : `全部确认（${pendingChapterCount} 章）` }}
          </AppButton>
        </div>

        <div class="tp-banner tp-banner--ai">
          确认后，教材和教辅会对应到这棵树上：每个课时都能直接看到挂好的课件与书页。
        </div>

        <details
          v-for="(chapter, index) in chapters"
          :key="chapter.key"
          class="tp-chapter-folder"
          :open="index === 0"
        >
          <summary>
            <span>{{ chapter.title }}</span>
            <span class="tp-inline-actions">
              <span class="tp-rail__count">{{ chapter.lessons.length }} 课时</span>
              <StatusBadge
                :tone="chapterStatus(chapter).tone"
                :label="chapterStatus(chapter).label"
              />
            </span>
          </summary>
          <div class="tp-lesson-chips">
            <span v-for="lesson in chapter.lessons" :key="lesson.key">{{ lesson.title }}</span>
          </div>
          <div class="tp-chapter-folder__actions">
            <small class="tp-muted">
              <template v-if="chapter.applied">本章课时已写入正式课时树。</template>
              <template v-else>确认后本章 {{ chapter.lessons.length }} 个课时与对应课件立即生效。</template>
            </small>
            <AppButton
              v-if="!chapter.applied"
              variant="secondary"
              :data-testid="`confirm-chapter-${chapter.key}`"
              :disabled="Boolean(confirmingKey)"
              @click="confirmChapter(chapter.key)"
            >
              {{ confirmingKey === chapter.key ? '正在确认…' : '确认这一章' }}
            </AppButton>
          </div>
        </details>
      </template>

      <template v-else-if="formalChapters.length">
        <div class="tp-main-head">
          <div>
            <span class="tp-eyebrow">课时树 · 已生效</span>
            <h2>本学期课时树</h2>
            <p>{{ catalog.activeLessonCount }} 个课时已生效，备课页可直接取用。</p>
          </div>
          <StatusBadge tone="success" label="已生效" />
        </div>
        <details
          v-for="(chapter, index) in formalChapters"
          :key="chapter.title"
          class="tp-chapter-folder"
          :open="index === 0"
        >
          <summary>
            <span>{{ chapter.title }}</span>
            <span class="tp-rail__count">{{ chapter.lessons.length }} 课时</span>
          </summary>
          <div class="tp-lesson-chips">
            <span v-for="lesson in chapter.lessons" :key="lesson">{{ lesson }}</span>
          </div>
        </details>
      </template>

      <template v-else>
        <div class="tp-main-head">
          <div>
            <span class="tp-eyebrow">课时树 · 唯一主干</span>
            <h2>还没有课时树建议</h2>
            <p>导入课件文件夹后，系统会按文件夹与课件命名自动生成课时树建议；不需要 AI、不产生费用。</p>
          </div>
        </div>
        <div class="tp-inline-actions">
          <AppButton variant="primary" @click="emit('select', { kind: 'import' })">
            去导入课件文件夹
          </AppButton>
        </div>
      </template>
    </div>
  </section>
</template>
