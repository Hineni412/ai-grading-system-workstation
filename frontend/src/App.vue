<script setup lang="ts">
import { ElConfigProvider } from 'element-plus'
import zhCn from 'element-plus/es/locale/lang/zh-cn'

import ComponentShowcase from './components/design-system/ComponentShowcase.vue'
</script>

<template>
  <ElConfigProvider :locale="zhCn">
    <ComponentShowcase />
  </ElConfigProvider>
</template>
