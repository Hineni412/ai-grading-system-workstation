import type { LocationQuery } from 'vue-router'

import type { ReviewScope } from '../../stores/review-queue'

export function stringQuery(value: unknown): string | null {
  return typeof value === 'string' && value.length > 0 ? value : null
}

export function positiveIntegerQuery(value: unknown): number | null {
  if (typeof value !== 'string' || !/^\d+$/.test(value)) return null
  const parsed = Number(value)
  return Number.isSafeInteger(parsed) && parsed > 0 ? parsed : null
}

export function scopeQuery(value: unknown): ReviewScope {
  return value === 'teacher_pending'
    || value === 'ungraded'
    || value === 'ai_review'
    || value === 'teacher_final'
    || value === 'all'
    ? value
    : 'all'
}

export function entryQuery(query: LocationQuery): string | null {
  const entry = stringQuery(query.entry)
  return entry === 'manual' || entry === 'intervention' || entry === 'results'
    ? entry
    : null
}
