<script setup lang="ts">
import { computed, defineAsyncComponent, ref } from 'vue'

import { intakeApi, type IntakeHandoffSummary } from '../api/intake'
import { projectionR1Api, studentR1Api, type DirectorySubject } from '../api/r1'
import { createOrdinaryWorkModule } from '../ordinary/createOrdinaryWorkModule'
import ClassTeacherSurfaceTabs from '../shell/ClassTeacherSurfaceTabs.vue'
import { useClassTeacherRouteState, type ClassTeacherSurface } from '../shell/useClassTeacherRouteState'

const AffairsSurface = defineAsyncComponent(() => import('../affairs/AffairsSurface.vue'))
const ConversationDesk = defineAsyncComponent(() => import('../intake/ConversationDesk.vue'))
const HandoffWorkspace = defineAsyncComponent(() => import('../intake/HandoffWorkspace.vue'))
const CalendarSurface = defineAsyncComponent(() => import('../ordinary/CalendarSurface.vue'))
const StudentSurface = defineAsyncComponent(() => import('../students/StudentSurface.vue'))
const StudentOverviewPanel = defineAsyncComponent(() => import('../students/StudentOverviewPanel.vue'))

const { state: routeState, navigate } = useClassTeacherRouteState()
const ordinaryWork = createOrdinaryWorkModule()
const focusedHandoff = computed(() => routeState.value.handoffId)
const overlaySubject = ref<DirectorySubject | null>(null)
const overlayConversationId = ref<string | null>(null)
const profileError = ref('')

function asDirectorySubject(value: Record<string, unknown>): DirectorySubject {
  return {
    subject_id: String(value.subject_id ?? ''),
    display_name: String(value.display_name ?? ''),
    source_student_id: String(value.source_student_id ?? ''),
    class_label: value.class_label == null ? null : String(value.class_label),
    support_record_count: Number(value.support_record_count ?? 0),
    support_plan_count: Number(value.support_plan_count ?? 0),
    confirmed_entry_count: Number(value.confirmed_entry_count ?? 0),
    projection_state: String(value.projection_state ?? 'none'),
    attention_pending_count: Number(value.attention_pending_count ?? 0),
    last_confirmed_at: value.last_confirmed_at == null ? null : String(value.last_confirmed_at),
    ...(value.profile_state == null ? {} : { profile_state: String(value.profile_state) as DirectorySubject['profile_state'] }),
  }
}

async function selectSurface(surface: ClassTeacherSurface): Promise<void> {
  await navigate({
    surface,
    handoffId: null,
    turnId: surface === 'home' ? routeState.value.turnId : null,
    workItemId: surface === 'home' ? routeState.value.workItemId : null,
  })
}

async function openRestricted(projectionId: string, projectionType: string | null): Promise<void> {
  profileError.value = ''
  try {
    const target = await projectionR1Api.resolve(projectionId) as unknown as {
      surface?: ClassTeacherSurface
      panel?: 'directory' | 'support' | 'academic'
      subject_id?: string | null
      gone?: boolean
    }
    if (target.gone) {
      profileError.value = '这条提醒对应的内容已经不存在，提醒已自动关闭。'
      await navigate({ surface: projectionType === 'sensitive_affair' ? 'affairs' : 'students', panel: target.panel ?? 'directory', subjectId: null })
      return
    }
    await navigate({
      surface: target.surface ?? (projectionType === 'sensitive_affair' ? 'affairs' : 'students'),
      panel: target.panel ?? 'directory',
      subjectId: target.subject_id ?? null,
    })
  } catch {
    await navigate({ surface: projectionType === 'sensitive_affair' ? 'affairs' : 'students' })
  }
}

function rememberConversation(conversationId: string): void {
  void navigate({ surface: 'home', conversationId, handoffId: null })
}

function openHandoff(handoff: { handoff_id: string; turn_id: string; work_item_id: string }): void {
  overlaySubject.value = null
  overlayConversationId.value = null
  void navigate({
    handoffId: handoff.handoff_id,
    turnId: handoff.turn_id,
    workItemId: handoff.work_item_id,
  })
}

async function openStudentProfile(handoff: IntakeHandoffSummary): Promise<void> {
  profileError.value = ''
  try {
    const draft = await intakeApi.handoff(handoff.handoff_id)
    const subjectId = draft.subject_refs[0]?.id
    if (!subjectId) {
      profileError.value = '请先在对话里确认是哪名学生，再打开个人档案。'
      return
    }
    overlaySubject.value = asDirectorySubject(await studentR1Api.header(subjectId))
    overlayConversationId.value = draft.conversation_id
  } catch {
    overlaySubject.value = null
    overlayConversationId.value = null
    profileError.value = '学生档案暂时无法打开。可以返回对话后重试。'
  }
}

function closeStudentProfile(): void {
  overlaySubject.value = null
  overlayConversationId.value = null
}

function openStudentWorkspace(panel: 'support' | 'academic'): void {
  const subjectId = overlaySubject.value?.subject_id ?? null
  closeStudentProfile()
  void navigate({ surface: 'students', panel, subjectId })
}

function returnToConversation(conversationId: string, workItemId: string): void {
  void navigate({ surface: 'home', conversationId, workItemId, handoffId: null })
}

function handoffCompleted(conversationId: string): void {
  void navigate({ surface: 'home', conversationId, handoffId: null })
}

function openDomain(domain: string): void {
  const destinations = {
    student_growth: { surface: 'students' as const, panel: 'academic' as const },
    student_support: { surface: 'students' as const, panel: 'support' as const },
    conflict_safety: { surface: 'affairs' as const },
    class_operations: { surface: 'calendar' as const },
    activities_culture: { surface: 'calendar' as const },
    school_coordination: { surface: 'affairs' as const },
  }
  const destination = destinations[domain as keyof typeof destinations]
  if (destination) void navigate({ ...destination, handoffId: null })
}
</script>

<template>
  <main class="class-teacher-r7">
    <HandoffWorkspace
      v-if="focusedHandoff"
      :handoff-id="focusedHandoff"
      @back="returnToConversation"
      @completed="handoffCompleted"
    />
    <template v-else>
      <ClassTeacherSurfaceTabs :active="routeState.surface" @select="selectSurface" />
      <p v-if="profileError" class="profile-open-error" role="alert">{{ profileError }}</p>
      <ConversationDesk
        v-if="routeState.surface === 'home'"
        :conversation-id="routeState.conversationId"
        :focus-work-item-id="routeState.workItemId"
        @conversation-changed="rememberConversation"
        @open-handoff="openHandoff"
        @open-student-profile="openStudentProfile"
        @open-calendar="selectSurface('calendar')"
        @open-restricted="openRestricted"
        @open-domain="openDomain"
      />
      <CalendarSurface
        v-else-if="routeState.surface === 'calendar'"
        :module="ordinaryWork"
        @open-restricted="openRestricted"
      />
      <AffairsSurface v-else-if="routeState.surface === 'affairs'" />
      <StudentSurface
        v-else
        :panel="routeState.panel"
        :subject-id="routeState.subjectId"
        @navigate="(panel, subjectId) => navigate({ surface: 'students', panel, subjectId })"
      />
      <StudentOverviewPanel
        v-if="overlaySubject && overlayConversationId"
        :subject="overlaySubject"
        :conversation-id="overlayConversationId"
        @close="closeStudentProfile"
        @open="openStudentWorkspace"
      />
    </template>
  </main>
</template>

<style scoped>
.class-teacher-r7{min-height:100%;padding:16px clamp(12px,2.2vw,30px) 32px;background:var(--background)}
.class-teacher-r7 :deep(.surface-tabs){margin:-16px calc(clamp(12px,2.2vw,30px) * -1) 16px}
.profile-open-error{margin:0 0 12px;padding:10px 16px;border:1px solid var(--border);background:var(--color-danger-subtle);color:var(--destructive)}
@media(max-width:640px){.class-teacher-r7{padding-inline:10px}}
</style>
