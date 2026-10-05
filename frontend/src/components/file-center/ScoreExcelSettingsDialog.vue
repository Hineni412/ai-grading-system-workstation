<script setup lang="ts">
import AppButton from '../design-system/AppButton.vue'
import StatePanel from '../design-system/StatePanel.vue'
import type { ResultsCenterStudent } from '../../api/results-center'

defineProps<{
  eligibleStudents: ResultsCenterStudent[]
  filteredManualStudents: ResultsCenterStudent[]
  hiddenCount: number
  visibleCount: number
  submitting: boolean
}>()
const emit = defineEmits<{
  close: []
  submit: []
}>()

const hideBottomEnabled = defineModel<boolean>('hideBottomEnabled', { required: true })
const hideBottomN = defineModel<number>('hideBottomN', { required: true })
const manualHideEnabled = defineModel<boolean>('manualHideEnabled', { required: true })
const manualHiddenStudentIds = defineModel<number[]>('manualHiddenStudentIds', { required: true })
const manualStudentSearch = defineModel<string>('manualStudentSearch', { required: true })
</script>

<template>
  <div
    class="excel-settings-backdrop"
    data-testid="excel-settings-backdrop"
    @click.self="emit('close')"
  >
    <form
      class="excel-settings-dialog"
      role="dialog"
      aria-modal="true"
      aria-labelledby="excel-settings-title"
      data-testid="excel-settings-dialog"
      @submit.prevent="emit('submit')"
    >
      <div class="excel-settings-dialog__heading">
        <div>
          <h3 id="excel-settings-title">精简打印姓名，不改变成绩统计</h3>
        </div>
        <AppButton variant="ghost"
          @click="emit('close')"
        >
          关闭
        </AppButton>
      </div>

      <p class="excel-settings-dialog__explanation">
        均分、得分率、失分人数和排名始终统计全部正常参考且已完整批改的学生。下面的选择只影响每道题打印哪些失分学生姓名。
      </p>

      <div class="excel-settings-preview" aria-label="导出设置预览">
        <div>
          <span>正式统计</span>
          <strong data-testid="excel-preview-statistical">
            {{ eligibleStudents.length }} 人
          </strong>
        </div>
        <div>
          <span>最多隐藏姓名</span>
          <strong data-testid="excel-preview-hidden">
            {{ hiddenCount }} 人
          </strong>
        </div>
        <div>
          <span>仍可显示姓名</span>
          <strong data-testid="excel-preview-visible">
            {{ visibleCount }} 人
          </strong>
        </div>
      </div>

      <fieldset class="excel-settings-group">
        <legend>自动精简</legend>
        <label class="excel-settings-checkline">
          <input
            v-model="hideBottomEnabled"
            type="checkbox"
            data-testid="excel-hide-bottom-enabled"
          >
          <span>每班隐藏总分最后</span>
          <input class="app-input"
            v-model.number="hideBottomN"
            type="number"
            min="0"
            max="100"
            step="1"
            data-testid="excel-hide-bottom-n"
            :disabled="!hideBottomEnabled"
            aria-label="每班隐藏最后人数"
          >
          <span>名</span>
        </label>
        <small>班级人数不超过填写人数时不会自动隐藏全班；同分时按学号和姓名稳定选取准确人数。</small>
      </fieldset>

      <fieldset class="excel-settings-group">
        <legend>手动补充</legend>
        <label class="excel-settings-checkline">
          <input
            v-model="manualHideEnabled"
            type="checkbox"
            data-testid="excel-manual-enabled"
          >
          <span>另外手动隐藏指定学生姓名</span>
        </label>
        <template v-if="manualHideEnabled">
          <label class="excel-settings-search">
            <span>查找学生</span>
            <input class="app-input"
              v-model="manualStudentSearch"
              type="search"
              placeholder="输入姓名、学号或班级"
              data-testid="excel-student-search"
            >
          </label>
          <div
            v-if="filteredManualStudents.length"
            class="excel-settings-students"
            data-testid="excel-student-options"
          >
            <label
              v-for="student in filteredManualStudents"
              :key="student.student_id"
            >
              <input
                v-model="manualHiddenStudentIds"
                type="checkbox"
                :value="student.student_id"
                :data-testid="`excel-student-${student.student_id}`"
              >
              <span>
                <strong>{{ student.student_name }}</strong>
                {{ student.student_code || '无学号' }} · {{ student.class_name || '未分班' }} · {{ student.current_score }} 分
              </span>
            </label>
          </div>
          <StatePanel v-else kind="empty" compact :title="eligibleStudents.length ? '没有匹配的完整成绩学生。' : '完整成绩读取完成后，可在这里手动选择学生。'" />
        </template>
      </fieldset>

      <div class="excel-settings-dialog__actions">
        <span>这些设置只用于本次导出的 Excel，不会修改成绩中心数据。</span>
        <div>
          <AppButton variant="secondary"
            @click="emit('close')"
          >
            取消
          </AppButton>
          <AppButton variant="primary" type="submit"
            data-testid="submit-score-excel"
            :disabled="submitting"
          >
            生成成绩表
          </AppButton>
        </div>
      </div>
    </form>
  </div>
</template>
