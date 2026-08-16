<script setup lang="ts">
import { computed, ref, watch } from 'vue'

import AppButton from '../../../../components/design-system/AppButton.vue'
import StatusBadge from '../../../../components/design-system/StatusBadge.vue'
import { teachingPrepWorkbenchApi, type TrustedPptxVersion } from '../../api/workbench'
import type { UpClassPackage } from '../../api/catalog'
import { useTeachingPrepLessonWorkbenchContext } from '../../workbench/routeContext'

const workbench = useTeachingPrepLessonWorkbenchContext()
const catalog = workbench.catalog
const reviewMessage = ref('确认导出后的副本可在这里生成上课包。原课件不会被覆盖。')
const activatingId = ref<string | null>(null)
const previewVersionId = ref<string | null>(null)
const previewVersionSlide = ref(1)

const selectedPlan = computed(() => catalog.slidePlans.find(
  item => item.id === catalog.selectedSlidePlanId,
) ?? catalog.slidePlans[0] ?? null)
const preview = computed(() => catalog.slidePlanPreview)
const latestRun = computed(() => catalog.pptxExecutions[0] ?? null)
const pptxVersions = computed(() => workbench.pptxVersions.value)
const previewVersion = computed(() => pptxVersions.value.find(
  item => item.id === previewVersionId.value,
) ?? null)
const currentVersion = computed(() => pptxVersions.value.find(item => item.is_current) ?? null)
const pptxExecutionAvailable = computed(() => (
  catalog.moduleStatus?.wps_execution_available === true
))
const previewReady = computed(() => (
  latestRun.value?.status === 'verifying'
  && latestRun.value.published_version_id === null
  && Boolean(latestRun.value.verification_report)
  && Object.keys(latestRun.value.verification_report ?? {}).length > 0
))
const canExecute = computed(() => {
  if (
    !pptxExecutionAvailable.value
    || selectedPlan.value?.status !== 'approved'
    || !preview.value?.valid_for_execution
  ) return false
  if (previewReady.value) return true
  const status = latestRun.value?.status ?? ''
  if (!latestRun.value) return true
  return !['running', 'verifying', 'publishing'].includes(status)
})

const phases = computed(() => {
  const order = ['copying', 'executing', 'verifying', 'publishing'] as const
  const current = latestRun.value?.phase ?? 'copying'
  const currentIndex = current === 'done' ? order.length : order.indexOf(current)
  return order.map((id, index) => ({
    id,
    label: {
      copying: '创建安全副本',
      executing: '执行已批准操作',
      verifying: '验证输出',
      publishing: '发布可信版本',
    }[id],
    state: latestRun.value
      ? index < currentIndex || latestRun.value.status === 'published'
        ? 'complete'
        : index === currentIndex && latestRun.value.status !== 'failed'
          ? 'running'
          : latestRun.value.status === 'failed' && index === currentIndex
            ? 'failed'
            : 'pending'
      : 'pending',
  }))
})

watch(
  () => pptxVersions.value.map(item => `${item.id}:${item.is_current}`).join('|'),
  () => {
    if (previewVersion.value) return
    const current = pptxVersions.value.find(item => item.is_current)
      ?? pptxVersions.value[0]
    previewVersionId.value = current?.id ?? null
    previewVersionSlide.value = 1
  },
  { immediate: true },
)

async function executePlan(): Promise<void> {
  if (!selectedPlan.value) return
  if (previewReady.value && latestRun.value) {
    await catalog.confirmPptxPreview(latestRun.value)
    reviewMessage.value = '这一版已经导出为上课副本。原课件没有被覆盖。'
    await workbench.refresh()
    return
  }
  await catalog.executePptx(selectedPlan.value)
  reviewMessage.value = '已创建异步 WPS 任务。关闭或刷新页面不会重复启动。'
  await workbench.refresh()
}

async function activateVersion(version: TrustedPptxVersion): Promise<void> {
  activatingId.value = version.id
  try {
    const result = await teachingPrepWorkbenchApi.activatePptxVersion(
      version.id,
      version.current_revision,
    )
    reviewMessage.value = result.changed
      ? `已把第 ${version.version_number} 版恢复为当前可信 PPTX；上课包没有随之切换。`
      : '这个版本已经是当前可信 PPTX。'
    await workbench.refresh()
  } finally {
    activatingId.value = null
  }
}

async function createPackage(version: TrustedPptxVersion): Promise<void> {
  await catalog.createUpClassPackage(version.id)
  reviewMessage.value = '上课包已从所选可信 PPTX 单独生成。'
}

async function discardPackageStaging(item: UpClassPackage): Promise<void> {
  if (!window.confirm('丢弃后该暂存任务将被清理，不影响已生成的副本。确定丢弃吗？')) return
  try {
    await catalog.discardUpClassPackageStaging(item)
    reviewMessage.value = '暂存的上课包构建已丢弃。'
  } catch {
    reviewMessage.value = catalog.errorMessage || '暂存构建没有丢弃成功，请刷新后重试。'
  }
}

function showModifiedPreview(version: TrustedPptxVersion): void {
  previewVersionId.value = version.id
  previewVersionSlide.value = 1
}

function modifiedPreviewUrl(version: TrustedPptxVersion, slide: number): string {
  return `${version.preview_url}?slide=${slide}`
}

async function runPrimary(): Promise<void> {
  const version = currentVersion.value
  if (!version) return
  await createPackage(version)
}

defineExpose({
  primaryLabel: computed(() => (currentVersion.value ? '生成上课包' : '请先生成 PPTX 副本')),
  primaryDisabled: computed(() => !currentVersion.value),
  runPrimary,
})
</script>

<template>
  <div class="tp-step-canvas">
    <section class="tp-panel" aria-label="创建新 PPTX 副本">
      <div class="tp-panel__head">
        <h2>PPTX 副本执行</h2>
        <StatusBadge
          :tone="latestRun ? (['running', 'verifying', 'publishing'].includes(latestRun.status) ? 'info' : latestRun.status === 'failed' ? 'danger' : 'success') : 'neutral'"
          :label="latestRun?.status ?? '未开始'"
        />
      </div>
      <div class="tp-panel__body">
        <ol class="tp-execution-phases">
          <li v-for="item in phases" :key="item.id" :class="`is-${item.state}`">
            <span /><div><strong>{{ item.label }}</strong><small>{{ item.state }}</small></div>
          </li>
        </ol>
        <p v-if="latestRun?.error_code" class="tp-error-text">失败代码：{{ latestRun.error_code }}。未发布不可信文件，原课件保持不变。</p>
        <div v-if="!pptxExecutionAvailable" class="tp-banner tp-banner--warn" role="alert">
          <div>
            <strong>这台电脑当前不能生成 PPTX 副本</strong>
            <p>尚未检测到可用的 WPS 执行能力。对照页仍可查看；但不能假装已经导出成品。</p>
          </div>
        </div>
        <div class="tp-inline-actions">
          <AppButton variant="primary" :disabled="!canExecute" @click="executePlan">
            {{ previewReady ? '确认导出这一版副本' : '创建新 PPTX 副本' }}
          </AppButton>
          <AppButton
            v-if="latestRun && ['running', 'publishing'].includes(latestRun.status)"
            variant="secondary"
            @click="catalog.cancelPptxExecution(latestRun)"
          >
            取消后续阶段
          </AppButton>
          <AppButton
            v-if="latestRun?.recovery_actions.includes('retry')"
            variant="secondary"
            @click="catalog.recoverPptxExecution(latestRun)"
          >
            从安全检查点恢复
          </AppButton>
          <AppButton
            v-if="latestRun?.staging_retained"
            variant="ghost"
            class="tp-danger-text"
            @click="catalog.discardPptxStaging(latestRun)"
          >
            清理暂存副本
          </AppButton>
        </div>
      </div>
    </section>

    <section class="tp-panel" aria-label="PPTX 副本版本">
      <div class="tp-panel__head">
        <h2>PPT 副本版本</h2>
        <span class="tp-panel__hint">AI 只改副本，原始课件永不覆盖</span>
      </div>
      <table v-if="pptxVersions.length" class="tp-grid">
        <thead>
          <tr><th class="tp-grid__main">版本</th><th>生成时间</th><th>状态</th><th class="tp-grid__actions">操作</th></tr>
        </thead>
        <tbody>
          <tr v-for="version in pptxVersions" :key="version.id">
            <td>
              <div class="tp-cell-main">第 {{ version.version_number }} 版</div>
              <div class="tp-cell-sub">{{ version.output_filename }} · {{ version.slide_count }} 页</div>
            </td>
            <td>{{ version.created_at }}</td>
            <td>
              <StatusBadge
                :tone="version.is_current ? 'success' : version.file_verified ? 'neutral' : 'danger'"
                :label="version.is_current ? '当前使用' : version.file_verified ? '历史' : '文件已缺失'"
              />
            </td>
            <td>
              <div class="tp-row-actions">
                <AppButton variant="ghost" :disabled="!version.file_verified" @click="showModifiedPreview(version)">预览</AppButton>
                <a :href="version.download_url" class="tp-link-button">下载</a>
                <AppButton
                  variant="ghost"
                  :disabled="version.is_current || !version.file_verified || activatingId === version.id"
                  @click="activateVersion(version)"
                >
                  恢复为当前
                </AppButton>
                <AppButton variant="ghost" :disabled="!version.file_verified" @click="createPackage(version)">生成上课包</AppButton>
              </div>
            </td>
          </tr>
        </tbody>
      </table>
      <div v-else class="tp-panel__body">
        <p class="tp-muted">还没有上课副本。请先在对照页确认导出。</p>
      </div>

      <section v-if="previewVersion" class="tp-modified-ppt-preview" aria-label="修改后 PPT 逐页预览">
        <header>
          <div>
            <h3>{{ previewVersion.output_filename }}</h3>
          </div>
          <strong>第 {{ previewVersionSlide }} / {{ previewVersion.slide_count }} 页</strong>
        </header>
        <div class="tp-slide-stage">
          <img :src="modifiedPreviewUrl(previewVersion, previewVersionSlide)" :alt="`修改后 PPT 第 ${previewVersionSlide} 页`">
        </div>
        <div class="tp-modified-ppt-preview__controls">
          <AppButton variant="secondary" :disabled="previewVersionSlide <= 1" @click="previewVersionSlide -= 1">上一页</AppButton>
          <div role="list" aria-label="修改后 PPT 页码">
            <button
              v-for="slide in previewVersion.slide_count"
              :key="slide"
              type="button"
              :class="{ 'is-active': slide === previewVersionSlide }"
              @click="previewVersionSlide = slide"
            >{{ slide }}</button>
          </div>
          <AppButton variant="secondary" :disabled="previewVersionSlide >= previewVersion.slide_count" @click="previewVersionSlide += 1">下一页</AppButton>
        </div>
      </section>
    </section>

    <section class="tp-panel" aria-label="上课包">
      <div class="tp-panel__head">
        <h2>上课包</h2>
        <span class="tp-panel__hint">把当前副本 + 确认资料打包，拷到教室电脑即可上课；与 PPTX 分离管理</span>
      </div>
      <div class="tp-panel__body">
        <div class="tp-package-list">
          <details v-for="item in catalog.upClassPackages" :key="item.id" :open="item.is_current">
            <summary>
              <strong>上课包 {{ item.version_number }}</strong>
              <StatusBadge
                :tone="item.status === 'complete' ? 'success' : item.status === 'interrupted' ? 'warning' : 'info'"
                :label="`${item.status}${item.is_current ? ' · 当前使用' : ''}`"
              />
            </summary>
            <p>关联 PPTX：{{ item.pptx_version_id }}</p>
            <ul v-if="item.manifest">
              <li v-for="(value, key) in item.manifest" :key="key"><strong>{{ key }}</strong>：{{ value }}</li>
            </ul>
            <div class="tp-inline-actions">
              <a v-if="item.download_url" :href="item.download_url" class="tp-link-button">下载上课包</a>
              <AppButton variant="ghost" :disabled="item.is_current || item.status !== 'complete'" @click="catalog.activateUpClassPackage(item)">恢复对应上课包</AppButton>
              <AppButton v-if="item.status === 'interrupted'" variant="secondary" @click="catalog.recoverUpClassPackage(item)">恢复构建</AppButton>
              <AppButton
                v-if="item.status === 'interrupted'"
                variant="ghost"
                class="tp-danger-text"
                data-testid="discard-package-staging"
                @click="discardPackageStaging(item)"
              >
                丢弃暂存
              </AppButton>
            </div>
          </details>
          <p v-if="!catalog.upClassPackages.length" class="tp-muted">可信 PPTX 发布后，可单独生成上课包。</p>
        </div>
        <p class="tp-inline-message" role="status">{{ reviewMessage }}</p>
      </div>
    </section>
  </div>
</template>
