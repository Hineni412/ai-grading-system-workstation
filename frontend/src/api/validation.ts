export function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

export function isNullableString(value: unknown): value is string | null {
  return typeof value === 'string' || value === null
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
