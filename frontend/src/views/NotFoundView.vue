<script setup lang="ts">
import { useRouter } from 'vue-router'

import StatePanel from '../components/design-system/StatePanel.vue'

const router = useRouter()

function returnToWorkbench(): void {
  void router.push('/workbench')
}
</script>

<template>
  <section class="not-found-view" aria-labelledby="not-found-title">
    <h1 id="not-found-title" tabindex="-1">页面未找到</h1>
    <StatePanel
      kind="error"
      title="无法打开这个页面"
      description="请求的地址不存在；当前考试选择和业务数据都没有改变。"
      retry-label="返回工作台"
      @retry="returnToWorkbench"
    />
  </section>
</template>
