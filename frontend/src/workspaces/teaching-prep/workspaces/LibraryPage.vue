<script setup lang="ts">
import { ref } from 'vue'

import FeedbackBanner from '../../../components/design-system/FeedbackBanner.vue'
import StatePanel from '../../../components/design-system/StatePanel.vue'
import { useTeachingPrepCatalogStore } from '../stores/catalog'
import ImportPanel from './library/ImportPanel.vue'
import MaterialTable from './library/MaterialTable.vue'
import MappingPanel from './library/MappingPanel.vue'
import ReferenceCollections from './library/ReferenceCollections.vue'
import { useMaterialImportQueue } from './library/importQueue'

const catalog = useTeachingPrepCatalogStore()
const importQueue = useMaterialImportQueue()
const notice = ref('')

function showNotice(message: string): void {
  notice.value = message
}
</script>

<template>
  <section class="tp-page" aria-label="资料库">
    <header class="tp-page-head">
      <div>
        <h1 data-workbench-title tabindex="-1">资料库</h1>
        <p>导入教材、教辅和课件，让 AI 帮忙把资料页面对应到课时。</p>
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
      <ImportPanel :queue="importQueue" />
      <MaterialTable @notice="showNotice" />
      <MappingPanel @notice="showNotice" />
      <ReferenceCollections :queue="importQueue" @notice="showNotice" />
    </template>
  </section>
</template>
