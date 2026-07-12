<script setup lang="ts">
import { ElConfigProvider } from 'element-plus'
import zhCn from 'element-plus/es/locale/lang/zh-cn'

import ApplicationErrorBoundary from './components/ApplicationErrorBoundary.vue'
import AppShell from './layouts/AppShell.vue'
</script>

<template>
  <ElConfigProvider :locale="zhCn">
    <ApplicationErrorBoundary>
      <AppShell />
    </ApplicationErrorBoundary>
  </ElConfigProvider>
</template>
