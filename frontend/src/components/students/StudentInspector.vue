<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'
import { Sheet, SheetContent, SheetHeader, SheetTitle } from '../ui/sheet'
import AppButton from '../design-system/AppButton.vue'
import { useStudentRosterStore } from '../../stores/students'
const roster = useStudentRosterStore()
const form = reactive({ student_code: '', name: '', class_name: '' })
const deleteConfirmation = ref('')
const saving = ref(false)
watch(() => roster.selectedStudent?.id, () => {
  const student = roster.selectedStudent
  form.student_code = student?.student_code ?? ''; form.name = student?.name ?? ''; form.class_name = student?.class_name ?? ''
  deleteConfirmation.value = ''
}, { immediate: true })
const canDelete = computed(() => roster.deletionState === 'ready' && deleteConfirmation.value === roster.selectedStudent?.student_code)
const busy = computed(() => saving.value || roster.deletionState === 'deleting')
function close() { if (!busy.value) roster.selectStudent(null) }
async function save() {
  if (busy.value) return
  saving.value = true
  try {
    if (await roster.saveStudent({ ...form, class_name: form.class_name.trim() || null })) {
      roster.selectStudent(null)
      await roster.load()
    }
  } finally { saving.value = false }
}
</script>
<template>
  <Sheet :open="Boolean(roster.selectedStudent)" @update:open="value => { if (!value) close() }">
    <SheetContent class="settings-drawer" :aria-describedby="undefined" @interact-outside="event => { if (busy) event.preventDefault() }" @escape-key-down="event => { if (busy) event.preventDefault() }">
      <SheetHeader class="settings-drawer__header"><SheetTitle>编辑学生</SheetTitle></SheetHeader>
      <form v-if="roster.selectedStudent" class="settings-drawer__form" @submit.prevent="save">
        <div class="settings-drawer__body">
          <label class="settings-field"><span>学号</span><input v-model.trim="form.student_code" class="app-input" name="student-code" required :disabled="busy"></label>
          <label class="settings-field"><span>姓名</span><input v-model.trim="form.name" class="app-input" name="student-name" required :disabled="busy"></label>
          <label class="settings-field"><span>班级</span><input v-model.trim="form.class_name" class="app-input" name="student-class" placeholder="选填" :disabled="busy"></label>
          <section class="student-danger" aria-label="删除学生">
            <h3>删除学生</h3>
            <AppButton v-if="roster.deletionState === 'idle' || roster.deletionState === 'error'" variant="ghost" class="settings-danger-ghost" data-action="review-deletion" @click="roster.loadDeletionImpact()">删除这名学生…</AppButton>
            <p v-else-if="roster.deletionState === 'loading'" role="status">正在核对关联记录…</p>
            <template v-if="roster.deletionImpact">
              <p>将删除 {{ roster.deletionImpact.counts.deleted_results }} 份成绩、{{ roster.deletionImpact.counts.deleted_details }} 条评分明细、{{ roster.deletionImpact.counts.deleted_annotations }} 条批注记录和 {{ roster.deletionImpact.counts.deleted_attendance }} 条考勤记录，并解除 {{ roster.deletionImpact.counts.unlinked_papers }} 份答卷与该生的关联。删除前会自动备份。</p>
              <label class="settings-field"><span>输入学号 {{ roster.selectedStudent.student_code }} 确认</span><input v-model="deleteConfirmation" class="app-input" name="delete-confirmation" autocomplete="off" :disabled="busy"></label>
              <AppButton variant="danger" data-action="delete-student" :disabled="!canDelete || busy" @click="roster.deleteSelected()">{{ roster.deletionState === 'deleting' ? '正在备份并删除…' : '备份并删除' }}</AppButton>
            </template>
          </section>
          <p v-if="roster.errorMessage" role="alert" class="settings-feedback is-error">{{ roster.errorMessage }}</p>
        </div>
        <footer class="settings-drawer__footer"><AppButton variant="secondary" :disabled="busy" @click="close">取消</AppButton><AppButton variant="primary" type="submit" data-action="save-student" :disabled="!form.student_code || !form.name || busy">{{ saving ? '正在保存…' : '保存' }}</AppButton></footer>
      </form>
    </SheetContent>
  </Sheet>
</template>
