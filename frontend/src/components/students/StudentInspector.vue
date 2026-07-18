<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'

import { useStudentRosterStore } from '../../stores/students'

const roster = useStudentRosterStore()
const form = reactive({
  student_code: '',
  name: '',
  class_name: '',
})
const deleteConfirmation = ref('')

watch(
  () => roster.selectedStudent,
  (student) => {
    form.student_code = student?.student_code ?? ''
    form.name = student?.name ?? ''
    form.class_name = student?.class_name ?? ''
    deleteConfirmation.value = ''
  },
  { immediate: true },
)

const canDelete = computed(() => (
  roster.deletionState === 'ready'
  && deleteConfirmation.value === roster.selectedStudent?.student_code
))

function save(): void {
  void roster.saveStudent({
    student_code: form.student_code,
    name: form.name,
    class_name: form.class_name.trim() || null,
  })
}

function close(): void {
  roster.selectStudent(null)
}
</script>

<template>
  <aside class="student-inspector" aria-labelledby="student-inspector-title">
    <template v-if="roster.selectedStudent">
      <div class="student-inspector__heading">
        <div>
          <p class="student-eyebrow">学生资料</p>
          <h2 id="student-inspector-title">{{ roster.selectedStudent.name }}</h2>
        </div>
        <button type="button" class="student-link" aria-label="关闭学生资料" @click="close">
          关闭
        </button>
      </div>

      <form class="student-inspector__form" @submit.prevent="save">
        <label>
          <span>学号</span>
          <input v-model.trim="form.student_code" name="student-code" required>
        </label>
        <label>
          <span>姓名</span>
          <input v-model.trim="form.name" name="student-name" required>
        </label>
        <label>
          <span>班级</span>
          <input v-model.trim="form.class_name" name="student-class">
        </label>
        <button
          type="submit"
          class="student-button student-button--primary"
          data-action="save-student"
          :disabled="!form.student_code || !form.name"
        >
          保存学生信息
        </button>
      </form>

      <section class="student-danger" aria-labelledby="student-danger-title">
        <h3 id="student-danger-title">删除学生</h3>
        <p>删除会同时清理该学生的成绩、明细和考勤关联。系统会先创建备份。</p>
        <button
          v-if="roster.deletionState === 'idle' || roster.deletionState === 'error'"
          type="button"
          class="student-button student-button--secondary"
          data-action="review-deletion"
          @click="roster.loadDeletionImpact()"
        >
          查看删除影响
        </button>
        <span v-else-if="roster.deletionState === 'loading'" role="status">
          正在核对关联记录…
        </span>

        <div v-if="roster.deletionImpact" class="student-danger__impact">
          <strong>
            将删除 {{ roster.deletionImpact.counts.deleted_results }} 份成绩记录、
            {{ roster.deletionImpact.counts.deleted_details }} 条评分明细
          </strong>
          <span>
            同时删除 {{ roster.deletionImpact.counts.deleted_annotations }} 条批注、
            {{ roster.deletionImpact.counts.deleted_attendance }} 条考勤，并解除
            {{ roster.deletionImpact.counts.unlinked_papers }} 份答卷的学生关联。
          </span>
          <label>
            <span>输入学号 {{ roster.selectedStudent.student_code }} 确认</span>
            <input
              v-model="deleteConfirmation"
              name="delete-confirmation"
              autocomplete="off"
            >
          </label>
          <button
            type="button"
            class="student-button student-button--danger"
            data-action="delete-student"
            :disabled="!canDelete"
            @click="roster.deleteSelected()"
          >
            创建备份并永久删除
          </button>
        </div>
      </section>
    </template>
    <div v-else class="student-inspector__empty">
      <strong>选择一名学生</strong>
      <span>可在这里修改学号、姓名和班级，或核对删除影响。</span>
    </div>
  </aside>
</template>
