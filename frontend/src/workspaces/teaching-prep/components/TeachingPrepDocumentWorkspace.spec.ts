import { createApp, defineComponent, h, nextTick, ref } from 'vue'
import { afterEach, describe, expect, it } from 'vitest'
import { vi } from 'vitest'

import TeachingPrepDocumentWorkspace from './TeachingPrepDocumentWorkspace.vue'

afterEach(() => { document.body.innerHTML = '' })

describe('TeachingPrepDocumentWorkspace narrow-screen panes', () => {
  it('uses the source preview review tabs to control the three real document panes', async () => {
    const pane = ref<'source' | 'preview' | 'review'>('source')
    const host = document.createElement('div')
    document.body.appendChild(host)
    const app = createApp(defineComponent({
      setup: () => () => h(TeachingPrepDocumentWorkspace, {
        title: '原页',
        previewUrl: '/preview/1',
        activePane: pane.value,
        previewZoom: 125,
        fitWidth: false,
        'onUpdate:activePane': value => { pane.value = value },
      }, {
        rail: () => h('span', { id: 'source-content' }, '来源内容'),
        inspector: () => h('span', { id: 'review-content' }, '审阅内容'),
      }),
    }))
    app.mount(host)
    await nextTick()

    const panes = () => ({
      source: host.querySelector('.tp-document-workspace__rail'),
      preview: host.querySelector('.tp-document-workspace__canvas'),
      review: host.querySelector('.tp-document-workspace__inspector'),
    })
    expect(panes().source?.classList.contains('is-mobile-active')).toBe(true)
    expect(panes().preview?.classList.contains('is-mobile-active')).toBe(false)

    const reviewTab = [...host.querySelectorAll<HTMLButtonElement>('.tp-document-workspace__mobile-tabs button')]
      .find(item => item.textContent === '审阅')
    reviewTab?.click()
    await nextTick()

    expect(pane.value).toBe('review')
    expect(panes().review?.classList.contains('is-mobile-active')).toBe(true)
    expect(panes().source?.classList.contains('is-mobile-active')).toBe(false)
    expect(host.querySelector('#source-content')).not.toBeNull()
    expect(host.querySelector('#review-content')).not.toBeNull()
    expect(host.querySelector<HTMLImageElement>('.tp-document-workspace__preview')?.style.inlineSize).toBe('125%')
    app.unmount()
  })

  it('emits one preview-loaded event per URL after the real image response succeeds', async () => {
    const previewUrl = ref('/preview/structural-1')
    const loaded = vi.fn()
    const host = document.createElement('div')
    document.body.appendChild(host)
    const app = createApp(defineComponent({
      setup: () => () => h(TeachingPrepDocumentWorkspace, {
        title: '合成课件',
        previewUrl: previewUrl.value,
        onPreviewLoaded: loaded,
      }),
    }))
    app.mount(host)
    await nextTick()

    const firstImage = host.querySelector<HTMLImageElement>('.tp-document-workspace__preview')!
    firstImage.dispatchEvent(new Event('load'))
    firstImage.dispatchEvent(new Event('load'))
    await nextTick()
    expect(loaded).toHaveBeenCalledOnce()
    expect(loaded).toHaveBeenCalledWith('/preview/structural-1')

    previewUrl.value = '/preview/structural-2'
    await nextTick()
    host.querySelector<HTMLImageElement>('.tp-document-workspace__preview')!
      .dispatchEvent(new Event('load'))
    await nextTick()
    expect(loaded).toHaveBeenCalledTimes(2)
    expect(loaded).toHaveBeenLastCalledWith('/preview/structural-2')
    app.unmount()
  })
})
