export function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

export function isNullableString(value: unknown): value is string | null {
  return typeof value === 'string' || value === null
}

export function hasExactKeys(
  value: Record<string, unknown>,
  keys: readonly string[],
): boolean {
  const actual = Object.keys(value)
  return actual.length === keys.length && keys.every(
    (key) => Object.prototype.hasOwnProperty.call(value, key),
  )
}

export function isInteger(value: unknown, minimum = 0): value is number {
  return Number.isSafeInteger(value) && Number(value) >= minimum
}

export function isPositiveInteger(value: unknown): value is number {
  return isInteger(value, 1)
}

export function isNonnegativeInteger(value: unknown): value is number {
  return isInteger(value, 0)
}

export function isFiniteNumber(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value)
}

export function isStringArray(value: unknown): value is string[] {
  return Array.isArray(value) && value.every((item) => typeof item === 'string')
}

export function isRevision(value: unknown): value is string {
  return typeof value === 'string' && /^[0-9a-f]{64}$/.test(value)
}

export function normalizedQuestionIds(values: readonly number[]): number[] {
  const result: number[] = []
  const seen = new Set<number>()
  for (const value of values) {
    if (!isPositiveInteger(value)) throw new Error('Invalid question ids')
    if (!seen.has(value)) {
      seen.add(value)
      result.push(value)
    }
  }
  if (result.length === 0 || result.length > 500) {
    throw new Error('Invalid question ids')
  }
  return result
}

export function assertNoPathLikeKeys(value: unknown): void {
  const checkedKeys = new Set<string>()
  function checkKey(key: string): void {
    const normalized = key
      .replace(/([a-z0-9])([A-Z])/g, '$1_$2')
      .replace(/([A-Z]+)([A-Z][a-z])/g, '$1_$2')
      .toLowerCase()
    const tokens = normalized.split(/[^a-z0-9]+/).filter(Boolean)
    const sensitive = tokens.some((token) => (
      token === 'path' || token === 'root' || token === 'dir'
      || token === 'directory' || token === 'file'
    ))
    if (
      key !== 'safe_filename'
      && key !== 'file_status'
      && key !== 'file_count'
      && key !== 'directory_evidence'
      && sensitive
    ) throw new Error('Path-like response key')
    checkedKeys.add(key)
  }
  const pending: unknown[] = [value]
  while (pending.length) {
    const item = pending.pop()
    if (Array.isArray(item)) {
      for (const child of item) {
        if (child !== null && typeof child === 'object') pending.push(child)
      }
    } else if (isRecord(item)) {
      for (const key of Object.keys(item)) {
        if (!checkedKeys.has(key)) checkKey(key)
        const child = item[key]
        if (child !== null && typeof child === 'object') pending.push(child)
      }
    }
  }
}
