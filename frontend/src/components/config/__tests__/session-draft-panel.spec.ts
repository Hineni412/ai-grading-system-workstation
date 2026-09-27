import { createApp, nextTick } from 'vue';
import { createPinia, setActivePinia } from 'pinia';
import { beforeEach, describe, expect, it } from 'vitest';

import type { CurriculumVolume } from '../../../api/question-bank';
import type { SessionSummary } from '../../../api/sessions';
import { useCurriculumScopeStore } from '../../../stores/curriculum-scope';
import { useSessionStore } from '../../../stores/session';
import SessionDraftPanel from '../SessionDraftPanel.vue'

function volume(id: string, order: number, label: string): CurriculumVolume {
  return {
    id,
    order,
    label,
    grade: '七年级',
    semester: '上册',
    textbook_version: '北师大版',
    source: { provider: '组卷网' },
    statistics: { raw_nodes: 1, excluded_nodes: 0, retained_nodes: 1 },
    chapters: [],
  }
}

function session(
  id: number,
  name: string,
  curriculumVolumeId: string | null = null,
): SessionSummary {
  return {
    id,
    name,
    status: 'draft',
    curriculum_volume_id: curriculumVolumeId,
    is_deleted: false,
    deleted_at: null,
    created_at: null,
    updated_at: null,
  }
}

async function settle(): Promise<void> {
  await Promise.resolve()
  await nextTick()
  await Promise.resolve()
  await nextTick()
}

async function mountPanel() {
  const host = document.createElement('div')
  document.body.append(host)
  const app = createApp(SessionDraftPanel)
  app.mount(host)
  await settle()
  return { host, unmount: () => app.unmount() }
}

function volumeSelect(host: HTMLElement): HTMLSelectElement {
  return host.querySelector<HTMLSelectElement>('#session-draft-curriculum')!
}

beforeEach(() => {
  document.body.innerHTML = ''
  localStorage.clear()
  setActivePinia(createPinia())
  const scope = useCurriculumScopeStore()
  scope.volumes = [
    volume('g7-upper', 1, '七年级数学上册'),
    volume('g7-lower', 2, '七年级数学下册'),
    volume('g8-upper', 3, '八年级数学上册'),
  ]
  scope.loadState = 'ready'
})

describe('SessionDraftPanel curriculum volume', () => {

  it('keeps the semester saved on the current session when the global selection changes', async () => {
    const sessionStore = useSessionStore()
    sessionStore.sessions = [session(7, '七年级月考', 'g7-upper')]
    sessionStore.selectSession(7)
    const scope = useCurriculumScopeStore()
    scope.selectedVolumeId = 'g7-upper'
    const mounted = await mountPanel()

    expect(volumeSelect(mounted.host).value).toBe('g7-upper')
    scope.selectedVolumeId = 'g8-upper'
    await settle()
    expect(volumeSelect(mounted.host).value).toBe('g7-upper')
    mounted.unmount()
  })

})
