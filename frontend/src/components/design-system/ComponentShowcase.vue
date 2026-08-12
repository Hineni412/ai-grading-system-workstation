<script setup lang="ts">
import { ref } from 'vue'

import { Input } from '@/components/ui/input'
import AppButton from './AppButton.vue'
import AppField from './AppField.vue'
import FeedbackBanner from './FeedbackBanner.vue'
import StatePanel from './StatePanel.vue'
import StatusBadge, { type StatusTone } from './StatusBadge.vue'

const examName = ref('2025—2026 学年度第二学期七年级数学期末质量监测与学情诊断测试')
const teacherScore = ref('13.75')
const disabledStudent = ref('阿布都热合曼·麦麦提艾力同学')
const tones: Array<{ tone: StatusTone; label: string }> = [
  { tone: 'neutral', label: '未开始' },
  { tone: 'info', label: '处理中' },
  { tone: 'success', label: '教师已确认' },
  { tone: 'warning', label: '待人工复核' },
  { tone: 'danger', label: '人机冲突' },
  { tone: 'ai', label: 'AI 初评：建议 9.5 分' },
  {
    tone: 'teacher',
    label: '教师决定：二次函数图像与性质—顶点式、对称轴及最值综合判断',
  },
]
</script>

<template>
  <main class="showcase" data-testid="design-system-showcase">
    <header class="showcase__header">
      <p class="showcase__eyebrow">AI 阅卷系统 · P2-02</p>
      <h1>设计系统展示</h1>
      <p>
        面向教师长时间使用的安静工作台基线。这里集中检查控件、状态、长中文和失败恢复，不代表业务页面已经迁移。
      </p>
    </header>

    <section class="showcase-section" aria-labelledby="section-token">
      <h2 id="section-token">基础 Token</h2>
      <p class="showcase-section__intro">颜色承担明确职责；普通层级依靠背景与边框，不依赖阴影。</p>
      <div class="token-strip">
        <div class="token-sample" data-token="accent"><span />强调与主要操作</div>
        <div class="token-sample" data-token="teacher"><span />教师最终决定</div>
        <div class="token-sample" data-token="ai"><span />AI 建议</div>
        <div class="token-sample" data-token="warning"><span />需要人工关注</div>
        <div class="token-sample" data-token="danger"><span />失败或冲突</div>
        <div class="token-sample" data-token="surface"><span />工作表面</div>
      </div>
    </section>

    <section class="showcase-section" aria-labelledby="section-button">
      <h2 id="section-button">按钮</h2>
      <p class="showcase-section__intro">操作区只保留一个主要动作，按钮文案直接说明结果。</p>
      <div class="control-row">
        <AppButton variant="primary">保存并继续复核</AppButton>
        <AppButton variant="secondary">暂存教师修改</AppButton>
        <AppButton variant="ghost">查看评分规则</AppButton>
        <AppButton variant="danger">删除未提交草稿</AppButton>
      </div>
      <div class="control-row" aria-label="按钮过程状态">
        <AppButton variant="primary" :loading="true">正在保存草稿</AppButton>
        <AppButton variant="secondary" disabled>等待评分依据</AppButton>
      </div>
    </section>

    <section class="showcase-section" aria-labelledby="section-input">
      <h2 id="section-input">输入</h2>
      <p class="showcase-section__intro">标签常驻，说明与错误紧邻字段，长内容允许换行而不挤压操作。</p>
      <div class="field-grid">
        <AppField id="exam-name" label="考试名称" hint="名称会显示在阅卷任务和报告中。" required>
          <template #default="{ inputId, ariaDescribedby, ariaInvalid, ariaRequired }">
            <Input
              :id="inputId"
              v-model="examName"
              :aria-describedby="ariaDescribedby"
              :aria-invalid="ariaInvalid"
              :aria-required="ariaRequired"
            />
          </template>
        </AppField>
        <AppField
          id="teacher-score"
          label="教师最终分"
          hint="本题满分 12 分，支持一位小数。"
          error="13.75 超过本题满分 12 分；AI 初评分 9.5 分尚未被覆盖。"
          required
        >
          <template #default="{ inputId, ariaDescribedby, ariaInvalid, ariaRequired }">
            <Input
              :id="inputId"
              v-model="teacherScore"
              inputmode="decimal"
              :aria-describedby="ariaDescribedby"
              :aria-invalid="ariaInvalid"
              :aria-required="ariaRequired"
            />
          </template>
        </AppField>
        <AppField
          id="student-name"
          data-testid="disabled-field"
          label="当前学生"
          hint="学生身份来自已导入名单，当前步骤不能修改。"
        >
          <template #default="{ inputId, ariaDescribedby, ariaInvalid, ariaRequired }">
            <Input
              :id="inputId"
              v-model="disabledStudent"
              disabled
              :aria-describedby="ariaDescribedby"
              :aria-invalid="ariaInvalid"
              :aria-required="ariaRequired"
            />
          </template>
        </AppField>
      </div>
    </section>

    <section class="showcase-section" aria-labelledby="section-badge">
      <h2 id="section-badge">状态徽章</h2>
      <p class="showcase-section__intro">每个状态都带有文字；教师决定和 AI 建议使用不同语义色。</p>
      <div class="badge-row">
        <StatusBadge v-for="item in tones" :key="item.tone" v-bind="item" />
      </div>
    </section>

    <section class="showcase-section" aria-labelledby="section-state">
      <h2 id="section-state">空、加载与错误</h2>
      <p class="showcase-section__intro">状态解释原因、影响和下一步，不让页面突然闪空。</p>
      <div class="state-grid">
        <StatePanel
          kind="empty"
          title="暂无待复核答卷"
          description="当前筛选条件下没有记录。可以清除“仅看人机冲突”，或返回批改进度查看任务状态。"
        />
        <StatePanel
          kind="loading"
          title="正在读取待复核记录"
          description="已保留当前筛选条件和列表位置。"
        />
        <StatePanel
          kind="error"
          title="复核记录加载失败"
          description="当前列表没有更新，不能确认下一份答卷。"
          detail="教师已填写的最终分与备注仍保存在本地草稿中；重新加载不会覆盖草稿。"
          retry-label="重新加载记录"
        />
      </div>
    </section>

    <section class="showcase-section" aria-labelledby="section-feedback">
      <h2 id="section-feedback">操作反馈</h2>
      <p class="showcase-section__intro">反馈说明结果与影响范围，并在失败时提供明确恢复动作。</p>
      <div class="feedback-list">
        <FeedbackBanner tone="info" title="评分依据已更新" description="下一份答卷将使用版本 4。" />
        <FeedbackBanner
          tone="success"
          title="教师最终分已保存"
          description="本次修改已进入审计记录，可以继续复核下一份。"
        />
        <FeedbackBanner
          tone="warning"
          title="存在人机评分差异"
          description="教师最终分比 AI 初评高 3.5 分，请在确认前核对评分细则第 2 项。"
        />
        <FeedbackBanner
          tone="error"
          title="保存失败"
          description="本次修改尚未写入数据库，但输入内容仍保留在当前页面。请检查本机服务后重新保存。"
          action-label="重新保存"
          dismissible
        />
      </div>
    </section>
  </main>
</template>

<style scoped>
.showcase {
  width: min(100%, var(--content-max-width));
  margin-inline: auto;
  padding: var(--space-8) var(--space-6) var(--space-9);
}

.showcase__header {
  max-width: 760px;
  padding-bottom: var(--space-7);
}

.showcase__eyebrow {
  margin: 0 0 var(--space-2);
  color: var(--color-accent);
  font-size: var(--font-size-dense);
  font-weight: var(--font-weight-semibold);
  letter-spacing: 0.04em;
}

.showcase__header h1 {
  margin: 0;
  font-size: var(--font-size-h1);
  font-weight: var(--font-weight-semibold);
  line-height: var(--line-height-tight);
}

.showcase__header > p:last-child {
  margin: var(--space-3) 0 0;
  color: var(--color-text-secondary);
  line-height: var(--line-height-relaxed);
}

.showcase-section {
  position: relative;
  min-width: 0;
  padding-block: var(--space-7);
  border-top: var(--border-width) solid var(--color-border-default);
}

.showcase-section h2 {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  margin: 0;
  font-size: var(--font-size-h2);
  font-weight: var(--font-weight-semibold);
}

.showcase-section h2::before {
  width: var(--space-1);
  height: var(--space-5);
  border-radius: var(--radius-tag);
  background: var(--color-accent);
  content: '';
}

.showcase-section__intro {
  margin: var(--space-2) 0 var(--space-5) var(--space-4);
  color: var(--color-text-secondary);
}

.token-strip,
.control-row,
.badge-row {
  display: flex;
  min-width: 0;
  flex-wrap: wrap;
  gap: var(--space-3);
}

.control-row + .control-row {
  margin-top: var(--space-3);
}

.token-sample {
  display: inline-flex;
  max-width: 100%;
  align-items: center;
  gap: var(--space-2);
  color: var(--color-text-secondary);
  font-size: var(--font-size-dense);
}

.token-sample span {
  width: var(--space-6);
  height: var(--space-6);
  flex: 0 0 auto;
  border: var(--border-width) solid var(--color-border-default);
  border-radius: var(--radius-control);
}

.token-sample[data-token='accent'] span {
  background: var(--color-accent);
}

.token-sample[data-token='teacher'] span {
  background: var(--color-teacher);
}

.token-sample[data-token='ai'] span {
  background: var(--color-ai);
}

.token-sample[data-token='warning'] span {
  background: var(--color-warning-subtle);
}

.token-sample[data-token='danger'] span {
  background: var(--color-danger-subtle);
}

.token-sample[data-token='surface'] span {
  background: var(--color-bg-surface);
}

.field-grid,
.state-grid {
  display: grid;
  min-width: 0;
  grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
  gap: var(--space-6);
}

.feedback-list {
  display: grid;
  gap: var(--space-3);
}

@media (max-width: 768px) {
  .showcase {
    padding: var(--space-6) var(--space-4) var(--space-8);
  }

  .showcase-section {
    padding-block: var(--space-6);
  }

  .showcase-section__intro {
    margin-left: 0;
  }
}

@media (max-width: 480px) {
  .showcase {
    padding-inline: var(--space-3);
  }

  .field-grid,
  .state-grid {
    grid-template-columns: minmax(0, 1fr);
  }
}
</style>
