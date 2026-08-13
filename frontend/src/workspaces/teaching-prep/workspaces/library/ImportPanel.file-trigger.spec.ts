import { createPinia, setActivePinia } from 'pinia'
import { createApp, defineComponent, h, nextTick } from 'vue'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import { useTeachingPrepCatalogStore } from '../../stores/catalog'
import { useMaterialImportQueue } from './importQueue'
import ImportPanel from './ImportPanel.vue'

beforeEach(() => {
  setActivePinia(createPinia())
})

afterEach(() => {
  document.body.innerHTML = ''
})

describe('ImportPanel file triggers', () => {
  it('keeps both native inputs while showing the upgraded trigger visuals', async () => {
    useTeachingPrepCatalogStore()
    const Wrapper = defineComponent({
      setup() {
        const queue = useMaterialImportQueue()
        return () => h(ImportPanel, { queue })
      },
    })
    const host = document.createElement('div')
    document.body.appendChild(host)
    const app = createApp(Wrapper)
    app.mount(host)
    await nextTick()

    const triggers = [...host.querySelectorAll<HTMLElement>('.tp-file-button')]
    expect(triggers).toHaveLength(2)

    const [folderTrigger, filesTrigger] = triggers as [HTMLElement, HTMLElement]
    expect(folderTrigger.querySelector('.tp-file-button__icon svg')).not.toBeNull()
    expect(folderTrigger.textContent).toContain('导入课件文件夹')
    const folderInput = folderTrigger.querySelector<HTMLInputElement>('input[type="file"]')!
    expect(folderInput.hasAttribute('webkitdirectory')).toBe(true)
    expect(folderInput.hasAttribute('multiple')).toBe(true)

    expect(filesTrigger.classList.contains('tp-file-button--primary')).toBe(true)
    expect(filesTrigger.querySelector('.tp-file-button__icon svg')).not.toBeNull()
    expect(filesTrigger.textContent).toContain('选择多份资料')
    const filesInput = filesTrigger.querySelector<HTMLInputElement>('input[type="file"]')!
    expect(filesInput.hasAttribute('multiple')).toBe(true)
    expect(filesInput.getAttribute('accept')).toBe('.pdf,.pptx,.png,.jpg,.jpeg,.webp')

    app.unmount()
  })
})
