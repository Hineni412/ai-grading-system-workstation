<script setup lang="ts">
import { computed, ref, watch } from 'vue'

import AppButton from '../../../../components/design-system/AppButton.vue'
import StatusBadge from '../../../../components/design-system/StatusBadge.vue'
import type { ReferencePptCollection } from '../../api/catalog'
import { teachingPrepCatalogApi } from '../../api/catalog'
import { useTeachingPrepCatalogStore } from '../../stores/catalog'
import type { MaterialImportQueue, PendingPptFolderBatch } from './importQueue'

const props = defineProps<{ queue: MaterialImportQueue }>()
const catalog = useTeachingPrepCatalogStore()
const emit = defineEmits<{ notice: [message: string] }>()

const referencePptCollections = ref<ReferencePptCollection[]>([])
const showInactiveCollections = ref(false)
const togglingCollectionId = ref<string | null>(null)

const activeCollections = computed(() => referencePptCollections.value.filter(
  item => item.is_active !== false,
))
const inactiveCollections = computed(() => referencePptCollections.value.filter(
  item => item.is_active === false,
))
const visibleCollections = computed(() => (
  showInactiveCollections.value
    ? referencePptCollections.value
    : activeCollections.value
))

async function loadReferencePptCollections(): Promise<void> {
  const semesterId = catalog.selectedSemester?.id
  if (!semesterId) {
    referencePptCollections.value = []
    return
  }
  try {
    // 拉取包含停用合集的完整列表，正常列表只显示启用项，停用项可折叠查看并恢复
    referencePptCollections.value = await (
      teachingPrepCatalogApi.listReferencePptCollections(semesterId, { includeInactive: true })
    )
  } catch {
    // A collection panel failure must not hide the rest of the material library.
  }
}

watch(
  () => catalog.selectedSemester?.id ?? '',
  () => { void loadReferencePptCollections() },
  { immediate: true },
)

// 导入队列完成合集建立后同步刷新列表
watch(
  () => props.queue.pendingPptFolders.value.map(batch => `${batch.id}:${batch.state}`).join('|'),
  () => { void loadReferencePptCollections() },
)

async function setCollectionActive(
  collection: ReferencePptCollection,
  isActive: boolean,
): Promise<void> {
  if (!isActive && !window.confirm(
    `停用后合集不再显示在列表中，可随时恢复；不影响已完成的课件改编。确定停用「${collection.display_name}」吗？`,
  )) return
  togglingCollectionId.value = collection.id
  try {
    const updated = await teachingPrepCatalogApi.updateReferencePptCollection(
      collection.id,
      { is_active: isActive },
    )
    const index = referencePptCollections.value.findIndex(item => item.id === updated.id)
    if (index >= 0) referencePptCollections.value[index] = updated
    emit('notice', isActive ? `已恢复合集「${collection.display_name}」。` : `已停用合集「${collection.display_name}」。`)
  } catch {
    emit('notice', '合集状态没有保存，请刷新后重试。')
  } finally {
    togglingCollectionId.value = null
  }
}

function collectionFolderGroups(collection: ReferencePptCollection): Array<{
  name: string
  members: ReferencePptCollection['members']
}> {
  const grouped = new Map<string, ReferencePptCollection['members']>()
  for (const member of collection.members) {
    const parts = member.relative_path.split('/').filter(Boolean)
    const name = parts.length > 1 ? parts[0]! : '未分章课件'
    grouped.set(name, [...(grouped.get(name) ?? []), member])
  }
  return [...grouped.entries()].map(([name, members]) => ({ name, members }))
}

function batchStateLabel(batch: PendingPptFolderBatch): string {
  return batch.state === 'done' ? '合集已建立'
    : batch.state === 'creating' ? '正在建立虚拟目录'
      : batch.state === 'failed' ? '合集未完成'
        : '等待导入'
}
</script>

<template>
  <section class="tp-panel" aria-label="参考课件合集">
    <div class="tp-panel__head">
      <h2>④ 参考课件合集</h2>
      <span class="tp-panel__hint">供 AI 改编时参考风格的 PPT 集合；系统只保存受控副本和虚拟目录</span>
    </div>
    <div class="tp-panel__body">
      <article
        v-for="batch in queue.pendingPptFolders.value"
        :key="batch.id"
        class="tp-collection-batch"
      >
        <div>
          <strong>{{ batch.displayName }}</strong>
          <small>
            {{ batch.pptCount }} 份 PPTX
            <template v-if="batch.ignoredFileCount"> · 忽略 {{ batch.ignoredFileCount }} 个其他文件</template>
            <template v-if="batch.duplicatePptCount"> · {{ batch.duplicatePptCount }} 份重复课件仅收录一次</template>
          </small>
          <span v-if="batch.error" class="tp-error-text">{{ batch.error }}</span>
        </div>
        <StatusBadge
          :tone="batch.state === 'done' ? 'success' : batch.state === 'failed' ? 'danger' : 'info'"
          :label="batchStateLabel(batch)"
        />
        <AppButton
          v-if="batch.state === 'failed'"
          variant="secondary"
          @click="queue.retryPptCollection(batch)"
        >
          重试建立
        </AppButton>
      </article>

      <p v-if="!visibleCollections.length && !queue.pendingPptFolders.value.length" class="tp-muted">
        还没有参考课件合集。通过「导入课件文件夹」把整个 PPT 文件夹收录为合集。
      </p>

      <table v-if="visibleCollections.length" class="tp-grid">
        <thead>
          <tr><th class="tp-grid__main">合集</th><th>包含课件</th><th>状态</th><th class="tp-grid__actions">操作</th></tr>
        </thead>
        <tbody>
          <tr v-for="collection in visibleCollections" :key="collection.id">
            <td>
              <div class="tp-cell-main">{{ collection.display_name }}</div>
              <div class="tp-cell-sub">
                {{ collectionFolderGroups(collection).map(folder => `${folder.name}（${folder.members.length}）`).join(' · ') }}
              </div>
            </td>
            <td>{{ collection.members.length }} 份</td>
            <td>
              <StatusBadge
                :tone="collection.is_active === false ? 'neutral' : 'success'"
                :label="collection.is_active === false ? '已停用' : '可用'"
              />
            </td>
            <td>
              <div class="tp-row-actions">
                <AppButton
                  v-if="collection.is_active === false"
                  variant="ghost"
                  :disabled="togglingCollectionId === collection.id"
                  @click="setCollectionActive(collection, true)"
                >
                  恢复
                </AppButton>
                <AppButton
                  v-else
                  variant="ghost"
                  :disabled="togglingCollectionId === collection.id"
                  @click="setCollectionActive(collection, false)"
                >
                  停用
                </AppButton>
              </div>
            </td>
          </tr>
        </tbody>
      </table>
      <div v-if="inactiveCollections.length" class="tp-panel__foot">
        <AppButton variant="ghost" :aria-expanded="showInactiveCollections" @click="showInactiveCollections = !showInactiveCollections">
          {{ showInactiveCollections ? '收起已停用合集' : `查看已停用合集（${inactiveCollections.length}）` }}
        </AppButton>
      </div>
    </div>
  </section>
</template>
