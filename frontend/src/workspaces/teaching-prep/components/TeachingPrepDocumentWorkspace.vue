<script setup lang="ts">
defineProps<{
  title: string
  subtitle?: string
  previewUrl?: string | null
  emptyMessage?: string
}>()
</script>

<template>
  <section class="tp-document-workspace">
    <aside class="tp-document-workspace__rail">
      <slot name="rail" />
    </aside>
    <main class="tp-document-workspace__canvas">
      <header>
        <div>
          <h2>{{ title }}</h2>
          <p v-if="subtitle">{{ subtitle }}</p>
        </div>
        <slot name="toolbar" />
      </header>
      <img
        v-if="previewUrl"
        class="tp-document-workspace__preview"
        :src="previewUrl"
        :alt="`${title}原页预览`"
      >
      <div v-else class="tp-document-workspace__empty">
        <p>{{ emptyMessage ?? '选择资料页后在这里核对原内容。' }}</p>
      </div>
      <slot name="overlay" />
    </main>
    <aside class="tp-document-workspace__inspector">
      <slot name="inspector" />
    </aside>
  </section>
</template>
