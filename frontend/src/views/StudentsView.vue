<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import AppButton from '../components/design-system/AppButton.vue'
import StatusBadge from '../components/design-system/StatusBadge.vue'
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
const changingHomeroom = ref(false)

const homeroomSaved = computed(() => Boolean(homeroom.value?.homeroom_class))
const showHomeroomEditor = computed(() => !homeroomSaved.value || changingHomeroom.value)

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
    changingHomeroom.value = false
    homeroomMessage.value = homeroomDraft.value
      ? `已把“${homeroomDraft.value}”设为我的班主任班级。新建班主任对话会立即使用它。`
      : '已取消班主任班级设置；学生和历史记录没有删除。'
    if (homeroom.value.homeroom_class) {
      await roster.load({ page: 1, class_name: homeroom.value.homeroom_class })
    }
  } catch {
    homeroomMessage.value = '班主任班级没有保存。学生名单可能刚刚变化，请刷新后再选。'
  } finally {
    homeroomBusy.value = false
  }
}

function startChangeHomeroom(): void {
  homeroomDraft.value = homeroom.value?.homeroom_class ?? ''
  changingHomeroom.value = true
}

function cancelChangeHomeroom(): void {
  homeroomDraft.value = homeroom.value?.homeroom_class ?? ''
  changingHomeroom.value = false
}

onMounted(async () => {
  await loadHomeroom()
  await roster.load({
    page: 1,
    page_size: 50,
    class_name: homeroom.value?.homeroom_class ?? '',
  })
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
    </header>

    <section
      class="homeroom-setting"
      :class="{ 'is-set': homeroomSaved && !showHomeroomEditor, 'is-missing': homeroom && !homeroomSaved }"
      aria-labelledby="homeroom-setting-title"
    >
      <template v-if="!showHomeroomEditor">
        <div>
          <p class="student-eyebrow">我的班主任班级</p>
          <strong id="homeroom-setting-title">当前班主任班级：{{ homeroom?.homeroom_class }}</strong>
          <span>班主任工作台的新对话和学生选择都会默认使用这个班。</span>
        </div>
        <StatusBadge tone="success" label="已设置" />
        <AppButton variant="ghost" @click="startChangeHomeroom">更换班级</AppButton>
      </template>
      <template v-else>
        <div>
          <strong id="homeroom-setting-title">
            {{ homeroomSaved ? '更换班主任班级' : '尚未设置班主任班级' }}
          </strong>
          <span>
            {{ homeroomSaved
              ? '保存后，班主任工作台的新对话会改用新的默认班级。'
              : '先选择班级并保存，班主任工作台才会按这个班显示学生。' }}
          </span>
        </div>
        <select v-model="homeroomDraft" :disabled="homeroomBusy || !homeroom">
          <option value="">尚未设置</option>
          <option v-for="classLabel in homeroom?.classes ?? []" :key="classLabel" :value="classLabel">
            {{ classLabel }}
          </option>
        </select>
        <div class="homeroom-setting__actions">
          <AppButton variant="primary" :disabled="homeroomBusy || !homeroom" @click="saveHomeroom">
            {{ homeroomBusy ? '正在保存…' : '保存班主任班级' }}
          </AppButton>
          <AppButton v-if="homeroomSaved" variant="ghost" :disabled="homeroomBusy" @click="cancelChangeHomeroom">
            取消
          </AppButton>
        </div>
      </template>
      <p v-if="homeroomMessage" role="status">{{ homeroomMessage }}</p>
    </section>

    <p v-if="roster.errorMessage" class="student-feedback is-error" role="alert">
      {{ roster.errorMessage }}
    </p>
    <p v-if="roster.noticeMessage" class="student-feedback is-success" role="status">
      {{ roster.noticeMessage }}
    </p>

    <div
      class="student-roster__workspace"
      :class="{
        'has-inspector': roster.selectedStudent,
        'is-importing': Boolean(roster.preview),
      }"
    >
      <StudentRosterTable />
      <StudentInspector v-if="roster.selectedStudent && !roster.preview" />
    </div>
  </section>
</template>

<style scoped>
.homeroom-setting {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto auto;
  align-items: center;
  gap: 12px;
  margin: 0 0 12px;
  padding: 10px 14px;
  border: 1px solid var(--border);
  border-radius: var(--radius);
  background: var(--card);
}

.homeroom-setting.is-missing {
  border-color: var(--color-warning);
  background: var(--color-warning-subtle);
}

.homeroom-setting > div {
  display: grid;
  gap: 2px;
}

.homeroom-setting span,
.homeroom-setting p {
  color: var(--muted-foreground);
}

.homeroom-setting select {
  min-height: 36px;
  min-width: 180px;
  padding: 0 12px;
  border: 1px solid var(--input);
  border-radius: 6px;
  background: var(--card);
  color: var(--foreground);
  font: inherit;
}

.homeroom-setting__actions {
  display: flex;
  gap: 8px;
}

.homeroom-setting p[role="status"] {
  grid-column: 1 / -1;
  margin: 0;
}

@media (max-width: 800px) {
  .homeroom-setting {
    grid-template-columns: 1fr;
  }
}
</style>
