<script setup lang="ts">
import { ref, watch } from 'vue'

type SaveStatus = 'idle' | 'success' | 'conflict' | 'failure'
type MappingStatus = 'not_present' | 'refreshed' | 'reconfirm_required'

const props = withDefaults(defineProps<{
  status?: SaveStatus
  mappingStatus?: MappingStatus | null
}>(), {
  status: 'idle',
  mappingStatus: null,
})

const emit = defineEmits<{ reload: [] }>()
const confirmingReload = ref(false)

function mappingCopy(): string {
  if (props.mappingStatus === 'refreshed') return '评分依据已保存，样卷映射已刷新'
  if (props.mappingStatus === 'reconfirm_required') {
    return '评分依据已保存；样卷映射需要回旧入口重新确认'
  }
  return '评分依据已保存'
}

watch(() => props.status, () => { confirmingReload.value = false })
</script>

<template>
  <div v-if="status === 'success'" class="config-save-result config-save-result--success" role="status">
    {{ mappingCopy() }}
  </div>
  <div v-else-if="status === 'failure'" class="config-save-result config-save-result--failure" role="alert">
    <strong>本次修改未保存。</strong>
    <span>服务器中的当前版本没有改变，本地修改已保留，可以修正问题后再次保存。</span>
  </div>
  <div v-else-if="status === 'conflict'" class="config-save-result config-save-result--conflict" role="alert">
    <strong>服务器已有较新版本，本地修改尚未保存。</strong>
    <span v-if="!confirmingReload">可以继续核对本地修改，或重新加载服务器版本。</span>
    <span v-else>重新加载后，本地修改将被丢弃，且不会自动合并。</span>
    <button v-if="!confirmingReload" type="button" name="重新加载最新版本" @click="confirmingReload = true">重新加载最新版本</button>
    <button v-else type="button" name="确认丢弃并重新加载" @click="emit('reload')">确认丢弃并重新加载</button>
  </div>
</template>
