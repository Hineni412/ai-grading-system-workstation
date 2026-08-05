<script setup lang="ts">
import { computed } from 'vue'

import { projectionR1Api } from '../api/r1'
import AffairsSurface from '../affairs/AffairsSurface.vue'
import ConversationDesk from '../intake/ConversationDesk.vue'
import HandoffWorkspace from '../intake/HandoffWorkspace.vue'
import CalendarSurface from '../ordinary/CalendarSurface.vue'
import { createOrdinaryWorkModule } from '../ordinary/createOrdinaryWorkModule'
import ClassTeacherSurfaceTabs from '../shell/ClassTeacherSurfaceTabs.vue'
import { useClassTeacherRouteState, type ClassTeacherSurface } from '../shell/useClassTeacherRouteState'
import StudentSurface from '../students/StudentSurface.vue'

const { state: routeState, navigate } = useClassTeacherRouteState()
const ordinaryWork = createOrdinaryWorkModule()
const focusedHandoff = computed(() => routeState.value.handoffId)

async function selectSurface(surface: ClassTeacherSurface): Promise<void> {
  await navigate({
    surface,
    handoffId: null,
    turnId: surface === 'home' ? routeState.value.turnId : null,
    workItemId: surface === 'home' ? routeState.value.workItemId : null,
  })
}

async function openRestricted(projectionId: string, projectionType: string | null): Promise<void> {
  try {
    const target = await projectionR1Api.resolve('', projectionId) as unknown as {
      surface?: ClassTeacherSurface
      panel?: 'directory' | 'support' | 'academic'
    }
    await navigate({ surface: target.surface ?? (projectionType === 'sensitive_affair' ? 'affairs' : 'students'), panel: target.panel ?? 'directory' })
  } catch {
    await navigate({ surface: projectionType === 'sensitive_affair' ? 'affairs' : 'students' })
  }
}

function rememberConversation(conversationId: string): void {
  void navigate({ surface: 'home', conversationId, handoffId: null })
}

function openHandoff(handoff: { handoff_id: string; turn_id: string; work_item_id: string }): void {
  void navigate({
    handoffId: handoff.handoff_id,
    turnId: handoff.turn_id,
    workItemId: handoff.work_item_id,
  })
}

function returnToConversation(conversationId: string, workItemId: string): void {
  void navigate({ surface: 'home', conversationId, workItemId, handoffId: null })
}

function handoffCompleted(conversationId: string): void {
  void navigate({ surface: 'home', conversationId, handoffId: null })
}
</script>

<template>
  <main class="class-teacher-r7">
    <HandoffWorkspace
      v-if="focusedHandoff"
      :handoff-id="focusedHandoff"
      token=""
      @back="returnToConversation"
      @completed="handoffCompleted"
    />
    <template v-else>
      <ClassTeacherSurfaceTabs :active="routeState.surface" @select="selectSurface" />
      <ConversationDesk
        v-if="routeState.surface === 'home'"
        :conversation-id="routeState.conversationId"
        :focus-work-item-id="routeState.workItemId"
        @conversation-changed="rememberConversation"
        @open-handoff="openHandoff"
      />
      <CalendarSurface
        v-else-if="routeState.surface === 'calendar'"
        :module="ordinaryWork"
        @open-restricted="openRestricted"
      />
      <AffairsSurface v-else-if="routeState.surface === 'affairs'" token="" />
      <StudentSurface
        v-else
        token=""
        :panel="routeState.panel"
        :status="null"
        @navigate="(panel) => navigate({ surface: 'students', panel })"
      />
    </template>
  </main>
</template>

<style scoped>
.class-teacher-r7{min-height:100%;padding:18px clamp(12px,2.2vw,30px) 34px;background:linear-gradient(180deg,#edf2ef 0,#f6f7f5 220px)}
.class-teacher-r7 :deep(.surface-tabs){margin:-18px calc(clamp(12px,2.2vw,30px) * -1) 18px}
@media(max-width:640px){.class-teacher-r7{padding-inline:10px}}
</style>
