<script setup lang="ts">
import { computed } from 'vue'

import StatusBadge from '../../../../components/design-system/StatusBadge.vue'
import { useTeachingPrepCatalogStore } from '../../stores/catalog'

const catalog = useTeachingPrepCatalogStore()

// 正式课时树的只读视图：按章/节层级分组展示已生效课时
const formalChapters = computed(() => {
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

// 没有章节层级时（教师手工建的扁平课时），直接按课时列出
const flatLessons = computed(() => (
  catalog.lessonNodes
    .filter(item => item.is_active && item.node_type === 'lesson')
    .sort((left, right) => left.sort_order - right.sort_order)
    .map(item => item.title)
))
</script>

<template>
  <section class="tp-panel" aria-label="本学期课时树">
    <div class="tp-panel__body">
      <template v-if="formalChapters.length">
        <div class="tp-main-head">
          <div>
            <span class="tp-eyebrow">课时树 · 已生效</span>
            <h2>本学期课时树</h2>
            <p>{{ catalog.activeLessonCount }} 个课时已生效，备课页打开课时即可直接备课。</p>
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

      <template v-else-if="flatLessons.length">
        <div class="tp-main-head">
          <div>
            <span class="tp-eyebrow">课时树 · 已生效</span>
            <h2>本学期课时</h2>
            <p>{{ flatLessons.length }} 个课时已生效，备课页打开课时即可直接备课。</p>
          </div>
          <StatusBadge tone="success" label="已生效" />
        </div>
        <div class="tp-lesson-chips">
          <span v-for="lesson in flatLessons" :key="lesson">{{ lesson }}</span>
        </div>
      </template>

      <template v-else>
        <div class="tp-main-head">
          <div>
            <span class="tp-eyebrow">课时树</span>
            <h2>本学期还没有课时</h2>
            <p>回到备课首页可以手工新建课时；课时生效后这里会按章展示。</p>
          </div>
        </div>
      </template>
    </div>
  </section>
</template>
