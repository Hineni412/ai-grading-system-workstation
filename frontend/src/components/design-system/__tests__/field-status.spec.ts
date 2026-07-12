import { createApp, defineComponent, h, nextTick } from 'vue'
import { describe, expect, it } from 'vitest'

import AppField from '../AppField.vue'
import StatusBadge, { type StatusTone } from '../StatusBadge.vue'

interface FieldSlotProps {
  inputId: string
  ariaDescribedby?: string
  ariaInvalid?: string
  ariaRequired?: string
}

async function mount(component: ReturnType<typeof defineComponent>) {
  const host = document.createElement('div')
  createApp(component).mount(host)
  await nextTick()
  return host
}

describe('AppField', () => {
  it('links a permanent label, hint, and error to the slotted input', async () => {
    const host = await mount(
      defineComponent({
        setup() {
          return () =>
            h(
              AppField,
              {
                id: 'final-score',
                label: '教师最终分',
                hint: '请输入 0 到 12 分',
                error: '分数不能超过满分 12 分',
                required: true,
              },
              {
                default: ({
                  inputId,
                  ariaDescribedby,
                  ariaInvalid,
                  ariaRequired,
                }: FieldSlotProps) =>
                  h('input', {
                    id: inputId,
                    'aria-describedby': ariaDescribedby,
                    'aria-invalid': ariaInvalid,
                    'aria-required': ariaRequired,
                  }),
              },
            )
        },
      }),
    )

    expect(host.querySelector('label')?.getAttribute('for')).toBe('final-score')
    expect(host.querySelector('label')?.textContent).toContain('教师最终分')
    expect(host.querySelector('label')?.textContent).toContain('*')
    expect(host.querySelector('input')?.getAttribute('aria-describedby')).toBe(
      'final-score-hint final-score-error',
    )
    expect(host.querySelector('input')?.getAttribute('aria-invalid')).toBe('true')
    expect(host.querySelector('input')?.getAttribute('aria-required')).toBe('true')
    expect(host.querySelector('#final-score-error')?.textContent).toContain('不能超过')
  })

  it('omits invalid state when the field has no error', async () => {
    const host = await mount(
      defineComponent({
        setup() {
          return () =>
            h(AppField, { id: 'teacher-note', label: '教师备注' }, {
              default: ({ inputId, ariaInvalid, ariaRequired }: FieldSlotProps) =>
                h('textarea', {
                  id: inputId,
                  'aria-invalid': ariaInvalid,
                  'aria-required': ariaRequired,
                }),
            })
        },
      }),
    )

    expect(host.querySelector('textarea')?.hasAttribute('aria-invalid')).toBe(false)
    expect(host.querySelector('textarea')?.hasAttribute('aria-required')).toBe(false)
  })
})

describe('StatusBadge', () => {
  const tones: StatusTone[] = [
    'neutral',
    'info',
    'success',
    'warning',
    'danger',
    'ai',
    'teacher',
  ]

  it.each(tones)('renders text and accessible state for %s', async (tone) => {
    const label = `${tone} 状态`
    const host = await mount(
      defineComponent({
        setup: () => () => h(StatusBadge, { tone, label }),
      }),
    )

    const badge = host.querySelector('[data-testid="status-badge"]')
    expect(badge?.getAttribute('data-tone')).toBe(tone)
    expect(badge?.getAttribute('aria-label')).toBe(`状态：${label}`)
    expect(badge?.textContent).toBe(label)
  })
})
