<script setup lang="ts">
import ApplicationErrorBoundary from './components/ApplicationErrorBoundary.vue'
import ConfirmDialogHost from './components/design-system/ConfirmDialogHost.vue'
import AppShell from './layouts/AppShell.vue'
</script>

<template>
  <ApplicationErrorBoundary>
    <AppShell />
    <ConfirmDialogHost />
  </ApplicationErrorBoundary>
</template>
