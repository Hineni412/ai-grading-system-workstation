import { apiClient } from './client'
import { isRecord } from './validation'

export type PaperMark = 'keep' | 'fix'
export type Quadrant = 'good' | 'free' | 'high' | 'suspect'
export type Prediction = 'harder' | 'easier' | 'none' | 'fit'
export interface PaperError { reason: string; category: string | null; count: number }
export interface CoverageType {
  key: string; name: string; group_count: number; credit: number; difficulty: number | null
  hard: boolean; shown: boolean; skipped: boolean
}
export interface CoverageSection { section: string; floor: number; ratio: number; types: CoverageType[] }
export interface IndependencePair { a: string; b: string; q3: number }
export interface IndependenceSameType extends IndependencePair { types: string[] }
export interface PaperChain { parent: string; from: string; to: string; zero_from: number; zero_both: number; others_rate: number }
export interface OrderInversion { earlier: string; later: string; type: string; earlier_rate: number; later_rate: number }
export interface PaperReliability { alpha: number; units: number; students: number }
interface DimensionBase { name: string; weight: number; score: number | null }
export type PaperDimension =
  | DimensionBase & { key: 'coverage'; detail: { sections: CoverageSection[]; missing: string[] } }
  | DimensionBase & { key: 'time_fit'; detail: { mid_students: number; unfinished: number; share: number; last_item: string; last_item_blank_mid: number; order_inversions: OrderInversion[] } }
  | DimensionBase & { key: 'distribution'; detail: { mean: number; sd: number; ceiling_share: number; floor_share: number; bins: number[]; parts: { mean: number; spread: number; extremes: number } } }
  | DimensionBase & { key: 'basic_hits'; detail: { basic_items: string[]; hit_items: string[]; too_easy: string[]; weak: string[] } }
  | DimensionBase & { key: 'discrimination'; detail: { low: string[] } }
  | DimensionBase & { key: 'independence'; detail: { pairs: IndependenceSameType[]; linked_other: IndependencePair[]; overlap_points: number; share: number; mean_q3: number | null; chains: PaperChain[] } }
export interface PaperPoint {
  id: string; max: number; rate: number; class_rates: Record<string, number | null>; r_rest: number | null
  predicted_difficulty: number | null; quadrant: Quadrant; pred_flag: Prediction
}
export interface PaperItem extends PaperPoint {
  parent_id: string; type: string; stem: string; answer: string; predicted_source: string
  lost_students: number; errors: PaperError[]; typical_error: PaperError | null
  distractors: { answer: string; counts: Record<string, number> | null; note: string | null } | null
}
export interface PaperGroup extends PaperPoint {
  parts: string[]; top_error: { part_id: string; reason: string; count: number } | null; flagged: boolean
}
export interface PaperReview {
  status: 'ready' | 'no_data'; session_id: number; title: string; graded_at: string | null
  full_score: number; students: number; classes: Record<string, number>; small_sample: boolean
  total: number | null; dimensions: PaperDimension[]; items: PaperItem[]; groups: PaperGroup[]
  coverage_types: Record<string, { covered_by: string[]; secondary_by: string[]; typical: { role: string; stem: string; question_id: number | null }[] }>
  unlinked_sections: number; review_lists: Record<PaperMark, { id: string; reason: string }[]>
  reliability: PaperReliability | null
}

function object(v: unknown): Record<string, unknown> {
  if (!isRecord(v)) throw new Error('试卷评价数据格式不正确')
  return v
}
function str(v: unknown): string { if (typeof v !== 'string') throw new Error('缺少文字字段'); return v }
function num(v: unknown): number { if (typeof v !== 'number' || !Number.isFinite(v)) throw new Error('数值字段不正确'); return v }
function bool(v: unknown): boolean { if (typeof v !== 'boolean') throw new Error('状态字段不正确'); return v }
function nullable(v: unknown): number | null { return v === null ? null : num(v) }
function nullableText(v: unknown): string | null { return v === null ? null : str(v) }
function list<T>(v: unknown, decode: (value: unknown) => T): T[] {
  if (!Array.isArray(v)) throw new Error('列表字段不正确')
  return v.map(decode)
}
function dictionary<T>(v: unknown, decode: (value: unknown) => T): Record<string, T> {
  return Object.fromEntries(Object.entries(object(v)).map(([key, value]) => [key, decode(value)]))
}
function paperError(v: unknown): PaperError { const d = object(v); return { reason: str(d.reason), category: nullableText(d.category), count: num(d.count) } }
function point(v: unknown): PaperPoint {
  const d = object(v)
  const quadrant = d.quadrant, pred_flag = d.pred_flag
  if (quadrant !== 'good' && quadrant !== 'free' && quadrant !== 'high' && quadrant !== 'suspect') throw new Error('类别不正确')
  if (pred_flag !== 'harder' && pred_flag !== 'easier' && pred_flag !== 'none' && pred_flag !== 'fit') throw new Error('预估类别不正确')
  return { id: str(d.id), max: num(d.max), rate: num(d.rate), class_rates: dictionary(d.class_rates, nullable), r_rest: nullable(d.r_rest),
    predicted_difficulty: nullable(d.predicted_difficulty), quadrant, pred_flag }
}
function dimension(v: unknown): PaperDimension {
  const d = object(v), dt = object(d.detail)
  const base = { name: str(d.name), weight: num(d.weight), score: nullable(d.score) }
  switch (d.key) {
    case 'coverage': return { ...base, key: 'coverage', detail: { missing: list(dt.missing, str), sections: list(dt.sections, value => {
      const s = object(value)
      return { section: str(s.section), floor: num(s.floor), ratio: num(s.ratio), types: list(s.types, value => {
        const t = object(value)
        return { key: str(t.key), name: str(t.name), group_count: num(t.group_count), credit: num(t.credit), difficulty: nullable(t.difficulty), hard: bool(t.hard), shown: bool(t.shown), skipped: bool(t.skipped) }
      }) }
    }) } }
    case 'time_fit': return { ...base, key: 'time_fit', detail: { mid_students: num(dt.mid_students), unfinished: num(dt.unfinished), share: num(dt.share), last_item: str(dt.last_item), last_item_blank_mid: num(dt.last_item_blank_mid), order_inversions: list(dt.order_inversions, v => {
      const e = object(v)
      return { earlier: str(e.earlier), later: str(e.later), type: str(e.type), earlier_rate: num(e.earlier_rate), later_rate: num(e.later_rate) }
    }) } }
    case 'independence': return { ...base, key: 'independence', detail: {
      pairs: list(dt.pairs, v => { const p = object(v); return { a: str(p.a), b: str(p.b), types: list(p.types, str), q3: num(p.q3) } }),
      linked_other: list(dt.linked_other, v => { const p = object(v); return { a: str(p.a), b: str(p.b), q3: num(p.q3) } }),
      overlap_points: num(dt.overlap_points), share: num(dt.share), mean_q3: nullable(dt.mean_q3),
      chains: list(dt.chains, v => { const c = object(v); return { parent: str(c.parent), from: str(c.from), to: str(c.to), zero_from: num(c.zero_from), zero_both: num(c.zero_both), others_rate: num(c.others_rate) } }) } }
    case 'distribution': { const p = object(dt.parts); return { ...base, key: 'distribution', detail: { mean: num(dt.mean), sd: num(dt.sd), ceiling_share: num(dt.ceiling_share), floor_share: num(dt.floor_share), bins: list(dt.bins, num), parts: { mean: num(p.mean), spread: num(p.spread), extremes: num(p.extremes) } } } }
    case 'basic_hits': return { ...base, key: 'basic_hits', detail: { basic_items: list(dt.basic_items, str), hit_items: list(dt.hit_items, str), too_easy: list(dt.too_easy, str), weak: list(dt.weak, str) } }
    case 'discrimination': return { ...base, key: 'discrimination', detail: { low: list(dt.low, str) } }
    default: throw new Error('评价维度不正确')
  }
}
function decodePaperReview(v: unknown): PaperReview {
  const d = object(v), lists = object(d.review_lists)
  const status = d.status
  if (status !== 'ready' && status !== 'no_data') throw new Error('评价状态不正确')
  const reviewEntry = (v: unknown) => { const d = object(v); return { id: str(d.id), reason: str(d.reason) } }
  return { status, session_id: num(d.session_id), title: str(d.title), graded_at: nullableText(d.graded_at), full_score: num(d.full_score),
    students: num(d.students), classes: dictionary(d.classes, num), small_sample: bool(d.small_sample), total: nullable(d.total),
    dimensions: list(d.dimensions, dimension), items: list(d.items, v => {
      const d = object(v)
      let distractors: PaperItem['distractors'] = null
      if (d.distractors !== null) { const r = object(d.distractors); distractors = { answer: str(r.answer), counts: r.counts === null ? null : dictionary(r.counts, num), note: nullableText(r.note) } }
      return { ...point(d), parent_id: str(d.parent_id), type: str(d.type), stem: str(d.stem), answer: str(d.answer), predicted_source: str(d.predicted_source),
        lost_students: num(d.lost_students), errors: list(d.errors, paperError), typical_error: d.typical_error === null ? null : paperError(d.typical_error), distractors }
    }), groups: list(d.groups, v => {
      const d = object(v), e = d.top_error === null ? null : object(d.top_error)
      return { ...point(d), parts: list(d.parts, str), flagged: bool(d.flagged), top_error: e ? { part_id: str(e.part_id), reason: str(e.reason), count: num(e.count) } : null }
    }), coverage_types: dictionary(d.coverage_types, v => {
      const t = object(v)
      return { covered_by: list(t.covered_by, str), secondary_by: list(t.secondary_by, str), typical: list(t.typical, v => { const r = object(v); return { role: str(r.role), stem: str(r.stem), question_id: r.question_id == null ? null : num(r.question_id) } }) }
    }), unlinked_sections: num(d.unlinked_sections), review_lists: { fix: list(lists.fix, reviewEntry), keep: list(lists.keep, reviewEntry) },
    reliability: d.reliability === null ? null : (() => { const r = object(d.reliability); return { alpha: num(r.alpha), units: num(r.units), students: num(r.students) } })() }
}
export const paperReviewApi = {
  get: (sessionId: number, signal?: AbortSignal) => apiClient.request(`/api/sessions/${sessionId}/paper-review`, { decode: decodePaperReview, signal, timeoutMs: 120000 }),
}
