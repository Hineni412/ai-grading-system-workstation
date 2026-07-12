<script setup lang="ts">
import { nextTick, onErrorCaptured, onUnmounted, ref } from 'vue'
import { useRouter } from 'vue-router'

const router = useRouter()
const failed = ref(false)
const contentKey = ref(0)
const failedNavigationTarget = ref<string | null>(null)

const removeRouterErrorHandler = router.onError((_error, to) => {
  failedNavigationTarget.value = to.fullPath
  failed.value = true
})

onUnmounted(removeRouterErrorHandler)

onErrorCaptured(() => {
  failedNavigationTarget.value = null
  failed.value = true
  return false
})

async function retryCurrentPage(): Promise<void> {
  const navigationTarget = failedNavigationTarget.value
  if (navigationTarget !== null) {
    try {
      await router.push(navigationTarget)
    } catch {
      return
    }
    failedNavigationTarget.value = null
  }
  failed.value = false
  contentKey.value += 1
  await nextTick()
}

async function returnToWorkbench(): Promise<void> {
  try {
    await router.replace('/workbench')
  } catch {
    return
  }
  failedNavigationTarget.value = null
  failed.value = false
  contentKey.value += 1
  await nextTick()
}
</script>

<template>
  <section
    v-if="failed"
    class="application-error"
    role="alert"
    aria-labelledby="application-error-title"
  >
    <h1 id="application-error-title" tabindex="-1">当前页面暂时无法显示</h1>
    <p>当前页面的操作已中断；当前考试选择和业务数据不会改变。</p>
    <div class="application-error__actions">
      <button type="button" @click="retryCurrentPage">重新加载当前页面</button>
      <button type="button" @click="returnToWorkbench">返回工作台</button>
    </div>
  </section>
  <div v-else :key="contentKey" class="application-boundary__content">
    <slot />
  </div>
</template>
