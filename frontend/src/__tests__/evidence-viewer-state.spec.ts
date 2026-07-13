import { describe, expect, it } from 'vitest'
import { useEvidenceViewer } from '../composables/use-evidence-viewer'

describe('P2-06 evidence viewer state', () => {
  it('starts on the crop in fit-width mode and resets between records', () => {
    const viewer = useEvidenceViewer()
    viewer.setCanvasSize({ width: 800, height: 600 })
    viewer.acceptImage(viewer.beginImageLoad(), { width: 1600, height: 1200 })
    viewer.zoomBy(0.5)
    viewer.panBy({ x: 120, y: -80 })
    viewer.rotateBy(90)
    viewer.resetForRecord('detail-2')
    expect(viewer.source.value).toBe('crop')
    expect(viewer.mode.value).toBe('fit-width')
    expect(viewer.rotation.value).toBe(0)
    expect(viewer.pan.value).toEqual({ x: 0, y: 0 })
  })

  it('fits rotated image width and keeps zoom between ten and four hundred percent', () => {
    const viewer = useEvidenceViewer()
    viewer.setCanvasSize({ width: 820, height: 600 })
    viewer.acceptImage(viewer.beginImageLoad(), { width: 1600, height: 1000 })
    expect(viewer.scale.value).toBeCloseTo(0.48)
    viewer.rotateBy(90)
    expect(viewer.scale.value).toBeCloseTo(0.772)
    viewer.setActualSize()
    viewer.zoomBy(20)
    expect(viewer.scale.value).toBe(4)
    viewer.zoomBy(-20)
    expect(viewer.scale.value).toBe(0.1)
  })

  it('zooms around the pointer and recenters an image smaller than the canvas', () => {
    const viewer = useEvidenceViewer()
    viewer.setCanvasSize({ width: 800, height: 600 })
    viewer.acceptImage(viewer.beginImageLoad(), { width: 800, height: 600 })
    viewer.setActualSize()
    viewer.zoomBy(0.5, { x: 200, y: 100 })
    expect(viewer.pan.value.x).toBeLessThan(0)
    expect(viewer.pan.value.y).toBeLessThan(0)
    viewer.setActualSize()
    expect(viewer.pan.value).toEqual({ x: 0, y: 0 })
  })

  it('bounds drag and keyboard pan while keeping a visible edge', () => {
    const viewer = useEvidenceViewer()
    viewer.setCanvasSize({ width: 800, height: 600 })
    viewer.acceptImage(viewer.beginImageLoad(), { width: 2400, height: 1800 })
    viewer.setActualSize()
    viewer.panBy({ x: 5000, y: -5000 })
    expect(viewer.pan.value).toEqual({ x: 848, y: -648 })
  })

  it('centers each axis until the rendered image exceeds the canvas', () => {
    const viewer = useEvidenceViewer()
    viewer.setCanvasSize({ width: 800, height: 600 })
    viewer.acceptImage(viewer.beginImageLoad(), { width: 1600, height: 1200 })
    viewer.panBy({ x: 500, y: -500 })
    expect(viewer.pan.value).toEqual({ x: 0, y: 0 })

    viewer.setActualSize()
    viewer.panBy({ x: 5000, y: -5000 })
    expect(viewer.pan.value).toEqual({ x: 448, y: -348 })
  })

  it('ignores stale load events and gives retry a new image key', () => {
    const viewer = useEvidenceViewer()
    const first = viewer.beginImageLoad()
    const second = viewer.beginImageLoad()
    expect(viewer.acceptImage(first, { width: 10, height: 10 })).toBe(false)
    expect(viewer.failImage(first)).toBe(false)
    expect(viewer.acceptImage(second, { width: 100, height: 200 })).toBe(true)
    const key = viewer.imageKey.value
    viewer.retry()
    expect(viewer.imageKey.value).toBe(key + 1)
    expect(viewer.loadState.value).toBe('loading')
  })
})
