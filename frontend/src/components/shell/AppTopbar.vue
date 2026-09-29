<script setup lang="ts">
import { computed } from 'vue'
import { useRoute } from 'vue-router'

import AppIconButton from '../design-system/AppIconButton.vue'

const props = defineProps<{
  navigationOpen: boolean
}>()

const emit = defineEmits<{
  toggleNavigation: []
}>()

const route = useRoute()
const pageTitle = computed(() => String(route.meta.title ?? '工作台'))
</script>

<template>
  <header
    class="app-topbar"
    data-testid="app-topbar"
  >
    <a class="app-topbar__skip-link" href="#main-workspace">跳到主要工作区</a>

    <div class="app-topbar__page">
      <AppIconButton
        class="app-topbar__navigation-toggle"
        :label="props.navigationOpen ? '收起导航' : '展开导航'"
        :icon="props.navigationOpen ? 'close' : 'menu'"
        variant="secondary"
        :aria-expanded="props.navigationOpen"
        aria-controls="application-sidebar"
        @click="emit('toggleNavigation')"
      />
      <div class="app-topbar__page-copy">
        <strong>{{ pageTitle }}</strong>
      </div>
    </div>
  </header>
</template>
