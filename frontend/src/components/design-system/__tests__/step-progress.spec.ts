import { createApp, defineComponent, h, nextTick, ref } from 'vue'
import { describe, expect, it } from 'vitest'

import StepProgress, {
  type StepProgressStep,
} from '../StepProgress.vue'

const STEPS: StepProgressStep[] = [
  { id: 'draft', label: '考试草稿', status: 'done', available: true, hint: '草稿已就绪' },
  { id: 'source', label: '上传与拆题', status: 'done', available: true },
  { id: 'generation', label: '分析并入库', status: 'done', available: true },
  { id: 'editor', label: '本场赋分', status: 'in_progress', available: true },
  { id: 'template', label: '样卷题框', status: 'todo', available: false },
]

function mount(options: {
  current?: string
  steps?: StepProgressStep[]
} = {}) {
  const host = document.createElement('div')
  document.body.append(host)
  const selected = ref('')
  const app = createApp(defineComponent({
    setup: () => () => h(StepProgress, {
      steps: options.steps ?? STEPS,
      current: options.current ?? 'draft',
      onSelect: (id: string) => { selected.value = id },
    }),
  }))
  app.mount(host)
  return { host, selected, unmount: () => app.unmount() }
}

function items(host: HTMLElement): HTMLElement[] {
  return [...host.querySelectorAll<HTMLElement>('.step-progress__item')]
}

describe('StepProgress', () => {
  it('renders every step as a button', async () => {
    const { host, unmount } = mount()
    await nextTick()
    const buttons = host.querySelectorAll('button')
    expect(buttons).toHaveLength(STEPS.length)
    unmount()
  })

  it('keeps completion status independent from the step being viewed', async () => {
    // Viewing 考试草稿 must not mark later steps as undone.
    const { host, unmount } = mount({ current: 'draft' })
    await nextTick()
    const states = items(host).map((item: HTMLElement) => ({
      done: item.classList.contains('is-done'),
      inProgress: item.classList.contains('is-in_progress'),
      current: item.classList.contains('is-current'),
    }))
    expect(states).toEqual([
      { done: true, inProgress: false, current: true },
      { done: true, inProgress: false, current: false },
      { done: true, inProgress: false, current: false },
      { done: false, inProgress: true, current: false },
      { done: false, inProgress: false, current: false },
    ])
    unmount()
  })

  it('sets aria-current="step" only on the viewed step and exposes hints', async () => {
    const { host, unmount } = mount({ current: 'editor' })
    await nextTick()
    const current = host.querySelectorAll('[aria-current="step"]')
    expect(current).toHaveLength(1)
    expect(current.item(0).getAttribute('data-step-id')).toBe('editor')
    const draft = host.querySelector('[data-step-id="draft"]')
    expect(draft?.getAttribute('aria-description')).toBe('草稿已就绪')
    expect(draft?.getAttribute('title')).toBe('草稿已就绪')
    unmount()
  })

  it('disables unavailable steps and emits select for available ones', async () => {
    const { host, selected, unmount } = mount()
    await nextTick()
    const template = host.querySelector<HTMLButtonElement>('[data-step-id="template"]')
    const source = host.querySelector<HTMLButtonElement>('[data-step-id="source"]')
    expect(template?.disabled).toBe(true)
    expect(source?.disabled).toBe(false)
    source?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    await nextTick()
    expect(selected.value).toBe('source')
    unmount()
  })

  it('marks connectors done when the step on their left is done', async () => {
    const { host, unmount } = mount()
    await nextTick()
    const connectors = host.querySelectorAll('.step-progress__connector')
    expect(connectors).toHaveLength(STEPS.length - 1)
    const doneFlags = [...connectors].map((el) => el.classList.contains('is-done'))
    expect(doneFlags).toEqual([true, true, true, false])
    unmount()
  })
})
