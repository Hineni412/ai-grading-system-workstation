export function formatScore(value: number | null | undefined, digits = 1): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return '—'
  if (Number.isInteger(value)) return String(value)
  return value.toFixed(digits).replace(/\.?0+$/, '')
}

export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1_048_576) return `${(bytes / 1024).toFixed(1)} KB`
  if (bytes < 1_073_741_824) return `${(bytes / 1_048_576).toFixed(1)} MB`
  return `${(bytes / 1_073_741_824).toFixed(1)} GB`
}

export function formatPercent(value: number | null | undefined): string {
  if (value === null || value === undefined) return '—'
  const percent = value <= 1 ? value * 100 : value
  return `${Math.round(percent)}%`
}

export function formatTime(value: string | null | undefined): string {
  if (!value) return ''
  return value.replace('T', ' ').replace('Z', '').slice(0, 19)
}
