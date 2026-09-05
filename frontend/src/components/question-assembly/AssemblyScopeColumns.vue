<script setup lang="ts">
// 考察范围双栏选择器：跟随页面顶部"教学学期"（curriculum-scope store 的 selectedVolumeId），
// 只展示当前册的章；左栏章列表带三态勾选框，右栏为当前章的小节组块与知识点 chips。
// 顶部已选摘要条恒定高度（无选择显示占位），章/节粒度摘要行 + "管理"浮层给知识点级
// 明细；选择区固定高度内滚，任何勾选操作都不改变卡片总高。
// 勾选结果写入 ai-assembly store 的 params.scopeKeys（章/节/知识点 id，
// 后端 resolve_scope_knowledge_points 负责解析）。
import { computed, onMounted, ref } from 'vue'

import {
  knowledgeLeafLabel,
  questionBankApi,
  type CurriculumCatalog,
} from '../../api/question-bank'
import { useAiAssemblyStore } from '../../stores/ai-assembly'
import { useCurriculumScopeStore } from '../../stores/curriculum-scope'

interface ScopeNode {
  key: string
  leaf: string
  fullPath: string
  children: ScopeNode[]
}

interface SummaryRow {
  key: string
  kind: 'chapter' | 'section' | 'points'
  label: string
  removeKeys: string[]
}

interface DetailChip {
  key: string
  label: string
  fullPath: string
  removeKeys: string[]
}

interface DetailSection {
  key: string
  label: string
  chips: DetailChip[]
}

interface DetailChapter {
  key: string
  label: string
  sections: DetailSection[]
}

const store = useAiAssemblyStore()
const curriculumScope = useCurriculumScopeStore()

const catalog = ref<CurriculumCatalog | null>(null)
const loadState = ref<'loading' | 'ready' | 'error'>('loading')
const activeChapterKey = ref('')
const managerOpen = ref(false)

// 摘要条直接铺出的行数上限；超出的折成 "+n"，点它或"管理"打开明细浮层。
const VISIBLE_ROWS = 3

async function loadCatalog(): Promise<void> {
  loadState.value = 'loading'
  try {
    catalog.value = await questionBankApi.getCurriculum()
    loadState.value = 'ready'
  } catch {
    catalog.value = null
    loadState.value = 'error'
  }
}

onMounted(() => {
  void loadCatalog()
})

const currentVolume = computed(() => (
  catalog.value?.volumes.find((volume) => volume.id === curriculumScope.selectedVolumeId) ?? null
))

const chapters = computed<ScopeNode[]>(() => (
  (currentVolume.value?.chapters ?? []).map((chapter) => ({
    key: chapter.id,
    leaf: knowledgeLeafLabel(chapter.display_name || chapter.title),
    fullPath: chapter.display_name || chapter.title,
    children: chapter.sections.map((section) => ({
      key: section.id,
      leaf: knowledgeLeafLabel(section.display_name || section.title),
      fullPath: section.display_name || section.title,
      children: section.knowledge_points.map((point) => ({
        key: point.id,
        leaf: knowledgeLeafLabel(point.display_name),
        fullPath: point.display_name,
        children: [],
      })),
    })),
  }))
))

const activeChapter = computed(() => (
  chapters.value.find((chapter) => chapter.key === activeChapterKey.value)
  ?? chapters.value[0]
  ?? null
))

const selectedKeys = computed(() => new Set(store.params.scopeKeys))

function descendantKeys(node: ScopeNode): string[] {
  const keys: string[] = []
  const walk = (child: ScopeNode): void => {
    keys.push(child.key)
    child.children.forEach(walk)
  }
  node.children.forEach(walk)
  return keys
}

// 小节是否整节选中：有知识点时看知识点是否全选；无细分知识点的小节以自身勾选充当"本节"。
function isSectionFullySelected(section: ScopeNode, keys: Set<string>): boolean {
  if (!section.children.length) return keys.has(section.key)
  return section.children.every((point) => keys.has(point.key))
}

function isChapterFullySelected(chapter: ScopeNode, keys: Set<string>): boolean {
  if (!chapter.children.length) return keys.has(chapter.key)
  return chapter.children.every((section) => isSectionFullySelected(section, keys))
}

type CheckState = 'all' | 'some' | 'none'

// 三态只看叶子：全章 = 每节都全选；半选 = 章内有任何已选 key。
// 章/节 key 本身只是整选连选时写入的标记，不作为"全选"的判定依据。
function chapterCheckState(chapter: ScopeNode): CheckState {
  const keys = selectedKeys.value
  if (isChapterFullySelected(chapter, keys)) return 'all'
  const any = keys.has(chapter.key) || descendantKeys(chapter).some((key) => keys.has(key))
  return any ? 'some' : 'none'
}

function chapterSelectedCount(chapter: ScopeNode): number {
  const keys = selectedKeys.value
  return descendantKeys(chapter).filter((key) => keys.has(key)).length
    + (keys.has(chapter.key) ? 1 : 0)
}

function toggleChapter(chapter: ScopeNode): void {
  store.setScopeChecked(chapter.key, chapterCheckState(chapter) !== 'all', descendantKeys(chapter))
}

// 后端把章/节 id 展开为其下全部知识点：取消子级时必须把祖先标记 key 一并移除，
// 否则残留的章 id 会把提交范围重新放大到全章，与摘要行不一致。
function toggleSection(chapter: ScopeNode, section: ScopeNode): void {
  const checked = !isSectionFullySelected(section, selectedKeys.value)
  store.setScopeChecked(
    section.key,
    checked,
    checked ? descendantKeys(section) : [...descendantKeys(section), chapter.key],
  )
}

function togglePoint(chapter: ScopeNode, section: ScopeNode, point: ScopeNode): void {
  const checked = !selectedKeys.value.has(point.key)
  store.setScopeChecked(point.key, checked, checked ? [] : [section.key, chapter.key])
}

function removeKeys(keys: string[]): void {
  const [first, ...rest] = keys
  if (!first) return
  store.setScopeChecked(first, false, rest)
}

// 已选摘要：章全选给"（全章）"，节全选给"（本节）"，其余按散知识点计数；
// 明细浮层始终按章/节分组给知识点级 chips，可单个删除。
const selectionView = computed(() => {
  const keys = selectedKeys.value
  const rows: SummaryRow[] = []
  const groups: DetailChapter[] = []
  let chapterRows = 0
  let sectionRows = 0
  let loosePoints = 0
  for (const chapter of chapters.value) {
    if (isChapterFullySelected(chapter, keys)) {
      chapterRows += 1
      rows.push({
        key: chapter.key,
        kind: 'chapter',
        label: `${chapter.leaf}（全章）`,
        removeKeys: [chapter.key, ...descendantKeys(chapter)],
      })
    } else {
      const loosePointKeys: string[] = []
      const looseAncestorKeys: string[] = []
      for (const section of chapter.children) {
        if (isSectionFullySelected(section, keys)) {
          sectionRows += 1
          rows.push({
            key: section.key,
            kind: 'section',
            label: `${chapter.leaf} · ${section.leaf}（本节）`,
            removeKeys: [section.key, ...descendantKeys(section), chapter.key],
          })
        } else {
          let sectionHasLoosePoint = false
          for (const point of section.children) {
            if (keys.has(point.key)) {
              loosePointKeys.push(point.key)
              sectionHasLoosePoint = true
            }
          }
          if (sectionHasLoosePoint) looseAncestorKeys.push(section.key)
        }
      }
      if (loosePointKeys.length) {
        loosePoints += loosePointKeys.length
        rows.push({
          key: `${chapter.key}::points`,
          kind: 'points',
          label: `${chapter.leaf} · ${loosePointKeys.length} 个知识点`,
          removeKeys: [...loosePointKeys, ...looseAncestorKeys, chapter.key],
        })
      }
    }

    const sections: DetailSection[] = []
    for (const section of chapter.children) {
      const chips: DetailChip[] = section.children.length
        ? section.children
          .filter((point) => keys.has(point.key))
          .map((point) => ({
            key: point.key,
            label: point.leaf,
            fullPath: point.fullPath,
            removeKeys: [point.key, section.key, chapter.key],
          }))
        : keys.has(section.key)
          ? [{
            key: section.key,
            label: '本节（无细分知识点）',
            fullPath: section.fullPath,
            removeKeys: [section.key, chapter.key],
          }]
          : []
      if (chips.length) sections.push({ key: section.key, label: section.leaf, chips })
    }
    if (sections.length) groups.push({ key: chapter.key, label: chapter.leaf, sections })
  }

  const parts: string[] = []
  if (chapterRows) parts.push(`${chapterRows} 章`)
  if (sectionRows) parts.push(`${sectionRows} 节`)
  if (loosePoints) parts.push(`${loosePoints} 知识点`)
  return { caption: parts.length ? `已选 ${parts.join(' · ')}` : '', rows, groups }
})

const visibleRows = computed(() => selectionView.value.rows.slice(0, VISIBLE_ROWS))
const hiddenRowCount = computed(() => Math.max(0, selectionView.value.rows.length - VISIBLE_ROWS))
</script>

<template>
  <div class="scope-columns" data-testid="assembly-scope-columns">
    <p v-if="loadState === 'loading'" class="scope-columns__hint">正在读取教材目录…</p>
    <p v-else-if="loadState === 'error'" class="scope-columns__hint" role="alert">
      教材目录读取失败。
      <button type="button" class="scope-columns__retry" @click="loadCatalog">重试</button>
    </p>
    <p v-else-if="!currentVolume" class="scope-columns__hint">
      未选择教学学期：请先在页面顶部选择学期，这里只显示该册的章节。
    </p>
    <template v-else>
      <div class="scope-columns__summary">
        <div class="scope-columns__bar">
          <span v-if="!selectionView.rows.length" class="scope-columns__placeholder">
            未选择（默认全库范围）
          </span>
          <template v-else>
            <span class="scope-columns__caption">{{ selectionView.caption }}</span>
            <div class="scope-columns__rows" aria-label="已选范围摘要">
              <span v-for="row in visibleRows" :key="row.key" class="scope-columns__row">
                {{ row.label }}
                <button
                  type="button"
                  :aria-label="`取消 ${row.label}`"
                  @click="removeKeys(row.removeKeys)"
                >×</button>
              </span>
              <button
                v-if="hiddenRowCount"
                type="button"
                class="scope-columns__row scope-columns__row--more"
                :aria-label="`还有 ${hiddenRowCount} 项，打开管理面板`"
                @click="managerOpen = true"
              >+{{ hiddenRowCount }}</button>
            </div>
            <button
              type="button"
              class="scope-columns__manage"
              :aria-expanded="managerOpen"
              @click="managerOpen = !managerOpen"
            >{{ managerOpen ? '收起' : '管理' }}</button>
          </template>
        </div>

        <div v-if="managerOpen" class="scope-columns__panel" role="dialog" aria-label="已选范围明细管理">
          <div class="scope-columns__panel-head">
            <strong>已选范围明细</strong>
            <button type="button" aria-label="关闭明细面板" @click="managerOpen = false">×</button>
          </div>
          <div class="scope-columns__panel-body">
            <section
              v-for="chapter in selectionView.groups"
              :key="chapter.key"
              class="scope-columns__group"
            >
              <p class="scope-columns__group-title">{{ chapter.label }}</p>
              <div
                v-for="section in chapter.sections"
                :key="section.key"
                class="scope-columns__group-section"
              >
                <p class="scope-columns__section-title">{{ section.label }}</p>
                <div class="scope-columns__chips">
                  <span
                    v-for="chip in section.chips"
                    :key="chip.key"
                    class="scope-columns__chip"
                    :title="chip.fullPath"
                  >
                    {{ chip.label }}
                    <button
                      type="button"
                      :aria-label="`取消 ${chip.label}`"
                      @click="removeKeys(chip.removeKeys)"
                    >×</button>
                  </span>
                </div>
              </div>
            </section>
          </div>
        </div>
      </div>

      <div class="scope-columns__body">
        <div class="scope-columns__nav" aria-label="章列表">
          <div
            v-for="chapter in chapters"
            :key="chapter.key"
            class="scope-columns__chapter"
            :class="{ 'is-active': activeChapter?.key === chapter.key }"
          >
            <input
              type="checkbox"
              class="scope-columns__chapter-check"
              :checked="chapterCheckState(chapter) === 'all'"
              :indeterminate.prop="chapterCheckState(chapter) === 'some'"
              :aria-label="`全选 ${chapter.leaf}`"
              :title="`全选 ${chapter.leaf}`"
              @change="toggleChapter(chapter)"
            >
            <button
              type="button"
              class="scope-columns__chapter-open"
              :title="chapter.fullPath"
              @click="activeChapterKey = chapter.key"
            >
              <span class="scope-columns__chapter-name">{{ chapter.leaf }}</span>
              <span
                v-if="chapterSelectedCount(chapter)"
                class="scope-columns__badge"
              >{{ chapterSelectedCount(chapter) }}</span>
            </button>
          </div>
        </div>

        <div class="scope-columns__detail" aria-label="当前章小节与知识点">
          <template v-if="activeChapter">
            <p class="scope-columns__detail-title" :title="activeChapter.fullPath">
              {{ activeChapter.leaf }}
            </p>
            <div
              v-for="section in activeChapter.children"
              :key="section.key"
              class="scope-columns__section"
            >
              <div class="scope-columns__section-head">
                <strong :title="section.fullPath">{{ section.leaf }}</strong>
                <button
                  type="button"
                  class="scope-columns__section-all"
                  @click="toggleSection(activeChapter, section)"
                >
                  {{ isSectionFullySelected(section, selectedKeys) ? '取消本节' : '全选本节' }}
                </button>
              </div>
              <div class="scope-columns__points">
                <template v-if="section.children.length">
                  <button
                    v-for="point in section.children"
                    :key="point.key"
                    type="button"
                    class="scope-columns__point"
                    :class="{ 'is-active': selectedKeys.has(point.key) }"
                    :aria-pressed="selectedKeys.has(point.key)"
                    :title="point.fullPath"
                    @click="togglePoint(activeChapter, section, point)"
                  >{{ point.leaf }}</button>
                </template>
                <button
                  v-else
                  type="button"
                  class="scope-columns__point"
                  :class="{ 'is-active': selectedKeys.has(section.key) }"
                  :aria-pressed="selectedKeys.has(section.key)"
                  :title="section.fullPath"
                  @click="store.setScopeChecked(
                    section.key,
                    !selectedKeys.has(section.key),
                    selectedKeys.has(section.key) ? [activeChapter.key] : [],
                  )"
                >本节（无细分知识点）</button>
              </div>
            </div>
          </template>
        </div>
      </div>
    </template>
  </div>
</template>

<style scoped>
.scope-columns {
  display: grid;
  gap: 10px;
}

.scope-columns__hint {
  color: var(--color-text-secondary);
  font-size: 12px;
  margin: 0;
}

.scope-columns__retry {
  background: none;
  border: 0;
  color: var(--primary);
  cursor: pointer;
  font-size: 12px;
  padding: 0 2px;
  text-decoration: underline;
}

.scope-columns__summary {
  position: relative;
}

.scope-columns__bar {
  align-items: center;
  background: var(--muted);
  border: 1px solid var(--border);
  border-radius: 10px;
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  height: 64px;
  overflow: hidden;
  padding: 6px 10px;
}

.scope-columns__placeholder {
  color: var(--color-text-secondary);
  font-size: 12px;
}

.scope-columns__caption {
  color: var(--color-text-secondary);
  flex-shrink: 0;
  font-size: 12px;
  font-weight: 650;
}

.scope-columns__rows {
  align-items: center;
  display: flex;
  flex: 1;
  flex-wrap: wrap;
  gap: 6px;
  min-width: 0;
}

.scope-columns__row {
  align-items: center;
  background: color-mix(in srgb, var(--primary) 12%, transparent);
  border: 1px solid color-mix(in srgb, var(--primary) 25%, transparent);
  border-radius: 999px;
  color: var(--primary);
  display: inline-flex;
  font-size: 12px;
  gap: 2px;
  max-width: 100%;
  padding: 2px 6px 2px 10px;
  white-space: nowrap;
}

.scope-columns__row button {
  background: none;
  border: 0;
  border-radius: 999px;
  color: var(--primary);
  cursor: pointer;
  font-size: 13px;
  line-height: 1;
  padding: 1px 4px;
}

.scope-columns__row button:hover {
  background: color-mix(in srgb, var(--primary) 18%, transparent);
}

.scope-columns__row--more {
  cursor: pointer;
  font-weight: 650;
  padding: 2px 10px;
}

.scope-columns__manage {
  background: none;
  border: 0;
  color: var(--primary);
  cursor: pointer;
  flex-shrink: 0;
  font-size: 12px;
  padding: 2px 0;
}

.scope-columns__manage:hover {
  text-decoration: underline;
}

.scope-columns__panel {
  background: var(--card);
  border: 1px solid var(--border);
  border-radius: 10px;
  box-shadow: var(--shadow-overlay);
  display: grid;
  left: 0;
  max-height: 300px;
  overflow: hidden;
  position: absolute;
  right: 0;
  top: calc(100% + 4px);
  z-index: 20;
}

.scope-columns__panel-head {
  align-items: center;
  border-bottom: 1px solid var(--border);
  display: flex;
  font-size: 12px;
  justify-content: space-between;
  padding: 8px 12px;
}

.scope-columns__panel-head button {
  background: none;
  border: 0;
  border-radius: 999px;
  color: var(--color-text-secondary);
  cursor: pointer;
  font-size: 14px;
  line-height: 1;
  padding: 2px 6px;
}

.scope-columns__panel-head button:hover {
  background: var(--muted);
}

.scope-columns__panel-body {
  display: grid;
  gap: 10px;
  overflow: auto;
  padding: 10px 12px;
}

.scope-columns__group {
  display: grid;
  gap: 6px;
}

.scope-columns__group-title {
  font-size: 12px;
  font-weight: 650;
  margin: 0;
}

.scope-columns__group-section {
  border-left: 2px solid var(--border);
  display: grid;
  gap: 4px;
  padding-left: 8px;
}

.scope-columns__section-title {
  color: var(--color-text-secondary);
  font-size: 11px;
  margin: 0;
}

.scope-columns__chips {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.scope-columns__chip {
  align-items: center;
  background: color-mix(in srgb, var(--primary) 12%, transparent);
  border: 1px solid color-mix(in srgb, var(--primary) 25%, transparent);
  border-radius: 999px;
  color: var(--primary);
  display: inline-flex;
  font-size: 12px;
  gap: 2px;
  padding: 2px 6px 2px 10px;
}

.scope-columns__chip button {
  background: none;
  border: 0;
  border-radius: 999px;
  color: var(--primary);
  cursor: pointer;
  font-size: 13px;
  line-height: 1;
  padding: 1px 4px;
}

.scope-columns__chip button:hover {
  background: color-mix(in srgb, var(--primary) 18%, transparent);
}

.scope-columns__body {
  border: 1px solid var(--border);
  border-radius: 10px;
  display: grid;
  grid-template-columns: 210px minmax(0, 1fr);
  max-height: 340px;
  overflow: hidden;
}

.scope-columns__nav {
  background: var(--muted);
  border-right: 1px solid var(--border);
  overflow: auto;
  padding: 8px;
}

.scope-columns__chapter {
  align-items: center;
  border-radius: 8px;
  color: var(--color-text-primary);
  display: flex;
  font-size: 12px;
  gap: 6px;
  padding: 6px 8px;
  width: 100%;
}

.scope-columns__chapter:hover {
  background: color-mix(in srgb, var(--primary) 8%, transparent);
}

.scope-columns__chapter.is-active {
  background: var(--primary);
  color: var(--primary-foreground);
  font-weight: 650;
}

.scope-columns__chapter-check {
  accent-color: var(--primary);
  cursor: pointer;
  flex-shrink: 0;
  margin: 0;
}

.scope-columns__chapter-open {
  align-items: center;
  background: none;
  border: 0;
  color: inherit;
  cursor: pointer;
  display: flex;
  flex: 1;
  font: inherit;
  gap: 6px;
  min-width: 0;
  padding: 0;
  text-align: left;
}

.scope-columns__chapter-name {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.scope-columns__badge {
  background: color-mix(in srgb, var(--primary) 14%, transparent);
  border-radius: 999px;
  color: var(--primary);
  flex-shrink: 0;
  font-size: 11px;
  font-weight: 650;
  line-height: 1;
  padding: 2px 7px;
}

.scope-columns__chapter.is-active .scope-columns__badge {
  background: color-mix(in srgb, var(--primary-foreground) 22%, transparent);
  color: var(--primary-foreground);
}

.scope-columns__detail {
  display: grid;
  gap: 10px;
  overflow: auto;
  padding: 10px 12px;
}

.scope-columns__detail-title {
  color: var(--color-text-secondary);
  font-size: 12px;
  font-weight: 650;
  margin: 0;
}

.scope-columns__section {
  border-top: 1px dashed var(--border);
  display: grid;
  gap: 6px;
  padding-top: 8px;
}

.scope-columns__section-head {
  align-items: center;
  display: flex;
  font-size: 13px;
  gap: 8px;
}

.scope-columns__section-all {
  background: none;
  border: 0;
  color: var(--primary);
  cursor: pointer;
  font-size: 12px;
  padding: 0;
}

.scope-columns__section-all:hover {
  text-decoration: underline;
}

.scope-columns__points {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.scope-columns__point {
  background: var(--card);
  border: 1px solid var(--border);
  border-radius: 999px;
  color: var(--color-text-secondary);
  cursor: pointer;
  font-size: 12px;
  padding: 3px 10px;
}

.scope-columns__point:hover {
  border-color: var(--primary);
  color: var(--primary);
}

.scope-columns__point.is-active {
  background: var(--primary);
  border-color: var(--primary);
  color: var(--primary-foreground);
}
</style>
