<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'

import AppButton from '../../../components/design-system/AppButton.vue'
import FeedbackBanner from '../../../components/design-system/FeedbackBanner.vue'
import StatePanel from '../../../components/design-system/StatePanel.vue'
import { useCurriculumScopeStore } from '../../../stores/curriculum-scope'
import { useTeachingPrepCatalogStore } from '../stores/catalog'
import {
  defaultSchoolYear,
  useTeachingPrepSemesterScope,
} from '../workbench/semesterScope'
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
const curriculumScope = useCurriculumScopeStore()
const semesterScope = useTeachingPrepSemesterScope()
const importQueue = useMaterialImportQueue()
const notice = ref('')
const selection = ref<LibrarySelection>({ kind: 'import' })
const userChoseImport = ref(false)
const creatingSemester = ref(false)
const createSetup = reactive({
  schoolYear: defaultSchoolYear(),
  plannedLessonCount: 60,
})
const createMessage = ref('')

const selectedVolume = computed(() => curriculumScope.selectedVolume)
const needsPrepSemester = computed(() => (
  Boolean(selectedVolume.value && !catalog.selectedSemester)
))
const canBrowseLibrary = computed(() => !needsPrepSemester.value)

function showNotice(message: string): void {
  notice.value = message
}

function selectFromRail(next: LibrarySelection): void {
  userChoseImport.value = next.kind === 'import'
  selection.value = next
}

function defaultSelection(): LibrarySelection {
  if (catalog.selectedSemester && catalog.lessonTreeStatus.state === 'pending') {
    return { kind: 'tree' }
  }
  const firstFolder = catalog.libraryChapterFolders[0]
  if (firstFolder) return { kind: 'chapter', folderKey: firstFolder.key }
  const firstBook = catalog.libraryMaterialRecords.find(item => (
    item.is_active && isBookRole(item.material_role)
  ))
  if (firstBook) {
    return { kind: 'material', materialId: firstBook.current_material_version_id }
  }
  return { kind: 'import' }
}

onMounted(() => { void catalog.ensureMaterialData() })

watch(
  () => [
    catalog.libraryChapterFolders.map(folder => folder.key).join('|'),
    catalog.lessonTreeStatus.state,
    catalog.libraryMaterialRecords.map(item => item.id).join('|'),
  ] as const,
  () => {
    const current = selection.value
    if (current.kind === 'chapter'
      && !catalog.libraryChapterFolders.some(folder => folder.key === current.folderKey)) {
      selection.value = defaultSelection()
      return
    }
    if (
      current.kind === 'import'
      && catalog.libraryMaterialRecords.length > 0
      && !userChoseImport.value
    ) {
      selection.value = defaultSelection()
    }
  },
  { immediate: true },
)

watch(
  () => importQueue.pendingPptFolders.value.map(batch => `${batch.id}:${batch.state}`).join('|'),
  () => { void catalog.refreshReferencePptCollections() },
)

const selectedMaterialIsBook = computed(() => {
  if (!catalog.selectedSemester || selection.value.kind !== 'material') return false
  const record = catalog.semesterMaterials.find(item => (
    item.current_material_version_id === (selection.value as { materialId: string }).materialId
  ))
  return record ? isBookRole(record.material_role) : false
})

async function createPrepSemester(): Promise<void> {
  creatingSemester.value = true
  createMessage.value = ''
  try {
    await semesterScope.createSemesterForGlobalVolume({
      schoolYear: createSetup.schoolYear,
      plannedLessonCount: createSetup.plannedLessonCount,
    })
    await catalog.ensureMaterialData()
    showNotice('本学期已建立，可以导入资料。')
  } catch {
    createMessage.value = catalog.errorMessage || '学期没有建立，请检查填写内容。'
  } finally {
    creatingSemester.value = false
  }
}
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
    <FeedbackBanner
      v-if="!selectedVolume && canBrowseLibrary"
      tone="info"
      title="当前是浏览全部资料"
      description="导入和对应需要先在顶部选择教学学期，并确认已建立对应的备课学期。"
    />

    <template v-if="needsPrepSemester">
      <StatePanel
        kind="empty"
        :title="`还没有「${selectedVolume?.label}」的备课学期`"
        description="确认后只建立资料归属，不会调用 AI。现有其他学期的书不会自动搬过来，建立后再逐份加入本学期。"
      />
      <section class="tp-panel" aria-label="建立本学期">
        <div class="tp-panel__body tp-form-line">
          <label class="tp-field">
            学年
            <input v-model="createSetup.schoolYear" type="text" placeholder="2026-2027">
          </label>
          <label class="tp-field">
            计划课时数
            <input v-model.number="createSetup.plannedLessonCount" type="number" min="1" max="300">
          </label>
          <AppButton
            variant="primary"
            :disabled="creatingSemester"
            @click="createPrepSemester"
          >
            {{ creatingSemester ? '正在建立…' : `为「${selectedVolume?.label}」建立本学期` }}
          </AppButton>
        </div>
        <p v-if="createMessage" class="tp-inline-message" role="status">{{ createMessage }}</p>
      </section>
    </template>
    <template v-else>
      <ProgressStrip
        v-if="catalog.selectedSemester"
        @open-import="selectFromRail({ kind: 'import' })"
      />
      <div class="tp-library-layout">
        <ShelfRail :selection="selection" @select="selectFromRail" />
        <div class="tp-step-canvas">
          <ImportPanel
            v-if="selection.kind === 'import'"
            :queue="importQueue"
          />
          <ChapterGrid
            v-else-if="selection.kind === 'chapter'"
            :folder-key="selection.folderKey"
            @select="selectFromRail"
            @notice="showNotice"
          />
          <LessonTreeConfirm
            v-else-if="selection.kind === 'tree'"
            @select="selectFromRail"
            @notice="showNotice"
          />
          <MaterialCorrespondence
            v-else-if="selection.kind === 'material' && selectedMaterialIsBook"
            :material-id="selection.materialId"
            @notice="showNotice"
            @open-import="selectFromRail({ kind: 'import' })"
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
