<script setup lang="ts">
import ApplicationErrorBoundary from './components/ApplicationErrorBoundary.vue'
import AppShell from './layouts/AppShell.vue'
</script>

<template>
  <ApplicationErrorBoundary>
    <AppShell />
  </ApplicationErrorBoundary>
</template>
