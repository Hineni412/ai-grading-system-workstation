export function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

export function isNullableString(value: unknown): value is string | null {
  return typeof value === 'string' || value === null
}

export function assertNoPathLikeKeys(value: unknown): void {
  if (Array.isArray(value)) {
    value.forEach(assertNoPathLikeKeys)
    return
  }
  if (!isRecord(value)) return
  for (const [key, child] of Object.entries(value)) {
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
      && sensitive
    ) throw new Error('Path-like response key')
    assertNoPathLikeKeys(child)
  }
}
