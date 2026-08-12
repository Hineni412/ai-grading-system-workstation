<script setup lang="ts">
import { onMounted, ref } from 'vue'

import AppButton from '../components/design-system/AppButton.vue'
import StudentImportDesk from '../components/students/StudentImportDesk.vue'
import StudentInspector from '../components/students/StudentInspector.vue'
import StudentRosterTable from '../components/students/StudentRosterTable.vue'
import { useStudentRosterStore } from '../stores/students'
import { intakeApi, type HomeroomPreference } from '../workspaces/class-teacher/api/intake'
import '../styles/students.css'

const roster = useStudentRosterStore()
const homeroom = ref<HomeroomPreference | null>(null)
const homeroomDraft = ref('')
const homeroomBusy = ref(false)
const homeroomMessage = ref('')

async function loadHomeroom(): Promise<void> {
  try {
    homeroom.value = await intakeApi.homeroom()
    homeroomDraft.value = homeroom.value.homeroom_class ?? ''
  } catch {
    homeroomMessage.value = '班主任班级暂时无法读取；学生名单的其他操作不受影响。'
  }
}

async function saveHomeroom(): Promise<void> {
  if (!homeroom.value || homeroomBusy.value) return
  homeroomBusy.value = true
  homeroomMessage.value = ''
  try {
    homeroom.value = await intakeApi.setHomeroom(homeroom.value, homeroomDraft.value || null)
    homeroomMessage.value = homeroomDraft.value
      ? `已把“${homeroomDraft.value}”设为我的班主任班级。新建班主任对话会立即使用它。`
      : '已取消班主任班级设置；学生和历史记录没有删除。'
  } catch {
    homeroomMessage.value = '班主任班级没有保存。学生名单可能刚刚变化，请刷新后再选。'
  } finally {
    homeroomBusy.value = false
  }
}

onMounted(() => {
  void roster.load({ page: 1, page_size: 50 })
  void loadHomeroom()
})
</script>

<template>
  <section class="student-roster">
    <header class="student-roster__hero">
      <div>
        <p class="student-eyebrow">Roster ledger</p>
        <h1 tabindex="-1">学生名单</h1>
        <p>先预览每一处变化，再把名单用于后续答卷匹配与阅卷。</p>
      </div>
      <span class="student-roster__registration">
        <i aria-hidden="true" />
        名单登记线
      </span>
    </header>

    <section class="homeroom-setting" aria-labelledby="homeroom-setting-title">
      <div>
        <strong id="homeroom-setting-title">我的班主任班级</strong>
        <span>这里是全局设置；保存后，班主任工作台的新对话和学生选择都会默认使用这个班。</span>
      </div>
      <select v-model="homeroomDraft" :disabled="homeroomBusy || !homeroom">
        <option value="">尚未设置</option>
        <option v-for="classLabel in homeroom?.classes ?? []" :key="classLabel" :value="classLabel">
          {{ classLabel }}
        </option>
      </select>
      <AppButton variant="primary" :disabled="homeroomBusy || !homeroom" @click="saveHomeroom">
        {{ homeroomBusy ? '正在保存…' : '保存班主任班级' }}
      </AppButton>
      <p v-if="homeroomMessage" role="status">{{ homeroomMessage }}</p>
    </section>

    <p v-if="roster.errorMessage" class="student-feedback is-error" role="alert">
      {{ roster.errorMessage }}
    </p>
    <p v-if="roster.noticeMessage" class="student-feedback is-success" role="status">
      {{ roster.noticeMessage }}
    </p>

    <StudentImportDesk />

    <div class="student-roster__workspace">
      <StudentRosterTable />
      <StudentInspector />
    </div>
  </section>
</template>

<style scoped>
.homeroom-setting{display:grid;grid-template-columns:minmax(260px,1fr) minmax(180px,280px) auto;align-items:end;gap:12px;margin:0 0 20px;padding:16px 20px;border:1px solid var(--border);border-radius:var(--radius);background:var(--card)}
.homeroom-setting>div{display:grid;gap:4px}.homeroom-setting span,.homeroom-setting p{color:var(--muted-foreground)}
.homeroom-setting select{min-height:36px;padding:0 12px;border:1px solid var(--input);border-radius:6px;background:var(--card);color:var(--foreground);font:inherit}
.homeroom-setting p{grid-column:1/-1;margin:0}
@media(max-width:800px){.homeroom-setting{grid-template-columns:1fr}.homeroom-setting p{grid-column:auto}}
</style>
