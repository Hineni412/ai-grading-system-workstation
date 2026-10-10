<script setup lang="ts">
import '../../styles/knowledge-overview.css'
withDefaults(defineProps<{ volumeLabel: string; sessionCount: number; selectedClass?: string }>(), { selectedClass: '' })
</script>
<template>
  <div class="knowledge-overview-scope-bar training-scope-bar" role="group" aria-label="训练来源范围">
    <strong>{{ volumeLabel }}</strong><span>本学期 {{ sessionCount }} 场考试 + 已发布训练</span>
    <span>{{ selectedClass ? selectedClass + '学生' : '全部学生' }}</span>
    <details class="overview-scope-definition"><summary>口径说明</summary><p>考试作答和已发布训练共同提供掌握度证据。班级与考试得分率条件在左栏选学生时设置。</p></details>
  </div>
</template>
<style scoped>
.training-scope-bar{padding:var(--space-2) var(--space-3);gap:var(--space-2)}
strong{font-size:var(--font-size-dense)}details{font-size:var(--font-size-caption);color:var(--color-text-secondary);margin-left:auto}summary{cursor:pointer}p{max-width:480px;line-height:1.7}
</style>
