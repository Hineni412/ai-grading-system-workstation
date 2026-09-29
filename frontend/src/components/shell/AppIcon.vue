<script setup lang="ts">
import type { AppIconName } from '../../navigation'

withDefaults(defineProps<{
  name: AppIconName
  size?: number
}>(), {
  size: 18,
})
</script>

<template>
  <svg
    class="app-icon"
    :width="size"
    :height="size"
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    stroke-width="1.8"
    stroke-linecap="round"
    stroke-linejoin="round"
    aria-hidden="true"
    focusable="false"
  >
    <g v-if="name === 'archive'">
      <path d="M4 7.5h16v12H4z" />
      <path d="M3 4.5h18v3H3zM9.5 11.5h5" />
    </g>
    <g v-else-if="name === 'trash'">
      <path d="M4.5 7h15M9 7V4.5h6V7M7 7l.8 13h8.4L17 7M10 10.5v6M14 10.5v6" />
    </g>
    <g v-else-if="name === 'workbench'">
      <rect x="3" y="3" width="7" height="7" rx="1.5" />
      <rect x="14" y="3" width="7" height="7" rx="1.5" />
      <rect x="3" y="14" width="7" height="7" rx="1.5" />
      <rect x="14" y="14" width="7" height="7" rx="1.5" />
    </g>
    <g v-else-if="name === 'exam'">
      <path d="M7 3.5h8l3 3V20H7z" />
      <path d="M15 3.5V7h3M9.5 11h6M9.5 15h6" />
    </g>
    <g v-else-if="name === 'review'">
      <circle cx="12" cy="12" r="8.5" />
      <path d="m8.5 12 2.2 2.2 4.8-5" />
    </g>
    <g v-else-if="name === 'results'">
      <path d="M4 19.5h16" />
      <path d="M6.5 16V11h3v5zM10.5 16V7h3v9zM14.5 16V4h3v12z" />
    </g>
    <g v-else-if="name === 'files'">
      <path d="M3.5 6.5h6l2 2h9v10a2 2 0 0 1-2 2h-13a2 2 0 0 1-2-2z" />
      <path d="M3.5 9h17" />
    </g>
    <g v-else-if="name === 'question-bank'">
      <path d="M4 5.5A2.5 2.5 0 0 1 6.5 3H11v16H6.5A2.5 2.5 0 0 0 4 21.5z" />
      <path d="M20 5.5A2.5 2.5 0 0 0 17.5 3H13v16h4.5a2.5 2.5 0 0 1 2.5 2.5z" />
    </g>
    <g v-else-if="name === 'assembly'">
      <path d="m12 3 8.5 4.5L12 12 3.5 7.5z" />
      <path d="m5 11 7 3.8 7-3.8M5 15l7 3.8 7-3.8" />
    </g>
    <g v-else-if="name === 'graph'">
      <circle cx="6" cy="6" r="2.2" />
      <circle cx="18" cy="7" r="2.2" />
      <circle cx="9" cy="18" r="2.2" />
      <circle cx="18" cy="17" r="2.2" />
      <path d="m8 7 7.8-.2M7.2 8l1.2 7.8M11.2 18h4.6M17.6 9.2l.2 5.6" />
    </g>
    <g v-else-if="name === 'training'">
      <circle cx="12" cy="12" r="8.5" />
      <circle cx="12" cy="12" r="4.5" />
      <path d="m15.5 8.5 4.8-4.8M17 3.7h3.3V7" />
    </g>
    <g v-else-if="name === 'students'">
      <circle cx="9" cy="8" r="3" />
      <path d="M3.5 20v-1.5A4.5 4.5 0 0 1 8 14h2a4.5 4.5 0 0 1 4.5 4.5V20" />
      <path d="M15.5 5.5a3 3 0 0 1 0 5.5M16.5 14a4 4 0 0 1 4 4v2" />
    </g>
    <g v-else-if="name === 'model'">
      <rect x="6" y="6" width="12" height="12" rx="2" />
      <path d="M9.5 10.5h5v3h-5zM9 2.5v3M15 2.5v3M9 18.5v3M15 18.5v3M2.5 9h3M2.5 15h3M18.5 9h3M18.5 15h3" />
    </g>
    <g v-else-if="name === 'ai-trace'">
      <path d="M3.5 4v5.5H9" />
      <path d="M4.6 15.3a8.3 8.3 0 1 0 1.8-8.2L3.5 9.5" />
      <path d="M12 8v4.2l3 1.8" />
    </g>
    <g v-else-if="name === 'settings'">
      <circle cx="12" cy="12" r="3" />
      <path d="M19.4 15a1.7 1.7 0 0 0 .3 1.9l.1.1-2.8 2.8-.1-.1a1.7 1.7 0 0 0-1.9-.3 1.7 1.7 0 0 0-1 1.6v.2h-4V21a1.7 1.7 0 0 0-1-1.6 1.7 1.7 0 0 0-1.9.3l-.1.1L4.2 17l.1-.1a1.7 1.7 0 0 0 .3-1.9A1.7 1.7 0 0 0 3 14H2.8v-4H3a1.7 1.7 0 0 0 1.6-1 1.7 1.7 0 0 0-.3-1.9L4.2 7 7 4.2l.1.1a1.7 1.7 0 0 0 1.9.3A1.7 1.7 0 0 0 10 3v-.2h4V3a1.7 1.7 0 0 0 1 1.6 1.7 1.7 0 0 0 1.9-.3l.1-.1L19.8 7l-.1.1a1.7 1.7 0 0 0-.3 1.9 1.7 1.7 0 0 0 1.6 1h.2v4H21a1.7 1.7 0 0 0-1.6 1Z" />
    </g>
    <g v-else-if="name === 'menu'">
      <path d="M4 7h16M4 12h16M4 17h16" />
    </g>
    <g v-else-if="name === 'close'">
      <path d="m6 6 12 12M18 6 6 18" />
    </g>
    <g v-else-if="name === 'search'">
      <circle cx="11" cy="11" r="8" />
      <path d="m21 21-4.35-4.35" />
    </g>
    <path v-else-if="name === 'chevron-down'" d="m7 9.5 5 5 5-5" />
    <g v-else-if="name === 'arrow-up'">
      <path d="M12 19V5" />
      <path d="m5.5 11.5 6.5-6.5 6.5 6.5" />
    </g>
    <g v-else-if="name === 'book-open'">
      <path d="M2 4h6a4 4 0 0 1 4 4v12a3 3 0 0 0-3-3H2z" />
      <path d="M22 4h-6a4 4 0 0 0-4 4v12a3 3 0 0 1 3-3h7z" />
    </g>
    <g v-else-if="name === 'download'">
      <path d="M12 3v11" />
      <path d="m7 10 5 5 5-5" />
      <path d="M4.5 20h15" />
    </g>
  </svg>
</template>

<style scoped>
.app-icon {
  display: block;
  flex: none;
}
</style>
