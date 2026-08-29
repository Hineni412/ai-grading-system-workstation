import { computed, ref } from 'vue'

import { workApi, type WorkNode, type WorkNodeDetail, type WorkSnapshot, type WorkView } from '../api/work'

export function createOrdinaryWorkModule() {
  const snapshot = ref<WorkSnapshot | null>(null)
  const selected = ref<WorkNodeDetail | null>(null)
  const loading = ref(false)
  const error = ref('')
  let requestVersion = 0

  async function load(view: WorkView, anchor?: string): Promise<boolean> {
    const version = ++requestVersion
    loading.value = true
    error.value = ''
    try {
      const value = await workApi.read(view, anchor)
      if (version !== requestVersion) return false
      snapshot.value = value
      return true
    } catch {
      if (version === requestVersion) error.value = '普通工作暂时无法读取，请稍后重试。'
      return false
    } finally {
      if (version === requestVersion) loading.value = false
    }
  }

  async function inspect(node: WorkNode): Promise<void> {
    selected.value = await workApi.detail(node.node_id)
  }

  async function command(node: WorkNode, name: string, fields: Record<string, unknown> = {}): Promise<Record<string, unknown>> {
    const result = await workApi.command(node, name, fields)
    await load(snapshot.value?.view ?? 'today')
    // 删除后节点已不存在，不能再读取详情；打开受保护事项会直接跳转。
    if (name !== 'open_restricted_projection' && name !== 'delete') await inspect(node)
    return result
  }

  return {
    snapshot,
    selected,
    loading,
    error,
    active: computed(() => snapshot.value?.nodes.filter((item) => !['completed', 'cancelled'].includes(item.status)) ?? []),
    load,
    inspect,
    command,
    clearSelection: () => { selected.value = null },
  }
}

export type OrdinaryWorkModule = ReturnType<typeof createOrdinaryWorkModule>
