<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'

import FeedbackBanner from '../../../components/design-system/FeedbackBanner.vue'
import StatePanel from '../../../components/design-system/StatePanel.vue'
import { useTeachingPrepCatalogStore } from '../stores/catalog'
import ImportPanel from './library/ImportPanel.vue'
import ShelfRail from './library/ShelfRail.vue'
import ProgressStrip from './library/ProgressStrip.vue'
import ChapterGrid from './library/ChapterGrid.vue'
import LessonTreeConfirm from './library/LessonTreeConfirm.vue'
import MaterialCorrespondence from './library/MaterialCorrespondence.vue'
import MaterialDetail from './library/MaterialDetail.vue'
import { isBookRole, type LibrarySelection } from './library/libraryShared'
import { useMaterialImportQueue } from './library/importQueue'

const catalog = useTeachingPrepCatalogStore()
const importQueue = useMaterialImportQueue()
const notice = ref('')
const selection = ref<LibrarySelection>({ kind: 'import' })

function showNotice(message: string): void {
  notice.value = message
}

// 默认选中：课时树待确认时优先课时树，否则第一个章文件夹，最后退回导入
function defaultSelection(): LibrarySelection {
  if (catalog.lessonTreeStatus.state === 'pending') return { kind: 'tree' }
  const firstFolder = catalog.libraryChapterFolders[0]
  if (firstFolder) return { kind: 'chapter', folderKey: firstFolder.key }
  return { kind: 'import' }
}

// 从备课首页切到资料库时组件才挂载（外壳按 v-if 切换视图），此处补拉资料数据；
// ensureMaterialData 按“教材:学期”缓存幂等，直达资料库时不会重复请求。
onMounted(() => { void catalog.ensureMaterialData() })

// 合集与课时树状态就绪后，把占位选中态切换到有意义的内容；
// 当前选中的章文件夹被停用合集移除时回退到默认选中。
watch(
  () => [
    catalog.libraryChapterFolders.map(folder => folder.key).join('|'),
    catalog.lessonTreeStatus.state,
  ] as const,
  () => {
    const current = selection.value
    if (current.kind === 'chapter'
      && !catalog.libraryChapterFolders.some(folder => folder.key === current.folderKey)) {
      selection.value = defaultSelection()
      return
    }
    if (current.kind === 'import' && catalog.semesterMaterials.length > 0) {
      selection.value = defaultSelection()
    }
  },
  { immediate: true },
)

// 导入队列完成合集建立后同步刷新章文件夹分组
watch(
  () => importQueue.pendingPptFolders.value.map(batch => `${batch.id}:${batch.state}`).join('|'),
  () => { void catalog.refreshReferencePptCollections() },
)

// 单份资料：教材/教辅打开“对应到课时树”，其余打开资料管理二级视图
const selectedMaterialIsBook = computed(() => {
  if (selection.value.kind !== 'material') return false
  const record = catalog.semesterMaterials.find(item => (
    item.current_material_version_id === (selection.value as { materialId: string }).materialId
  ))
  return record ? isBookRole(record.material_role) : false
})
</script>

<template>
  <section class="tp-page" aria-label="资料库">
    <header class="tp-page-head">
      <div>
        <h1 data-workbench-title tabindex="-1">资料库</h1>
        <p>左边选资料，右边看内容和下一步：课件确定课时树，教材教辅对应页码。</p>
      </div>
    </header>

    <FeedbackBanner
      v-if="catalog.errorMessage"
      tone="warning"
      title="部分资料信息未更新"
      :description="catalog.errorMessage"
    />
    <FeedbackBanner
      v-if="notice"
      tone="info"
      title="资料库提示"
      :description="notice"
      dismissible
      @dismiss="notice = ''"
    />

    <StatePanel
      v-if="!catalog.selectedSemester"
      kind="empty"
      title="还没有本学期"
      description="请先回到备课首页建立本学期，再导入资料。"
    />
    <template v-else>
      <ProgressStrip />
      <div class="tp-library-layout">
        <ShelfRail :selection="selection" @select="selection = $event" />
        <div class="tp-step-canvas">
          <ImportPanel
            v-if="selection.kind === 'import'"
            :queue="importQueue"
          />
          <ChapterGrid
            v-else-if="selection.kind === 'chapter'"
            :folder-key="selection.folderKey"
            @select="selection = $event"
            @notice="showNotice"
          />
          <LessonTreeConfirm
            v-else-if="selection.kind === 'tree'"
            @select="selection = $event"
            @notice="showNotice"
          />
          <MaterialCorrespondence
            v-else-if="selection.kind === 'material' && selectedMaterialIsBook"
            :material-id="selection.materialId"
            @notice="showNotice"
          />
          <MaterialDetail
            v-else-if="selection.kind === 'material'"
            :material-id="selection.materialId"
            @notice="showNotice"
          />
        </div>
      </div>
    </template>
  </section>
</template>
