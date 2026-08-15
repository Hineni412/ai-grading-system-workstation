const TOKEN_SPLIT = /[，,、;；\s]+/
const RANGE_TOKEN = /^(\d+)\s*[-~—–－]\s*(\d+)$/

export interface AnimationPageParse {
  pages: number[]
  error: string
}

export function parseAnimationPageText(
  raw: string,
  allowedPages: readonly number[],
  maxSelected: number,
): AnimationPageParse {
  const trimmed = raw.trim()
  if (!trimmed) return { pages: [], error: '' }

  const allowed = new Set(allowedPages)
  const pages: number[] = []
  const seen = new Set<number>()

  for (const token of trimmed.split(TOKEN_SPLIT).filter(Boolean)) {
    const values: number[] = []
    const range = token.match(RANGE_TOKEN)
    if (range) {
      const start = Number(range[1])
      const end = Number(range[2])
      if (
        !Number.isInteger(start)
        || !Number.isInteger(end)
        || start < 1
        || end < start
      ) {
        return { pages: [], error: `页码「${token}」无效` }
      }
      for (let page = start; page <= end; page += 1) values.push(page)
    } else if (/^\d+$/.test(token)) {
      values.push(Number(token))
    } else {
      return { pages: [], error: `页码「${token}」无效，请输入数字，例如 3,5 或 2-4` }
    }

    for (const page of values) {
      if (!allowed.has(page)) {
        return { pages: [], error: `第 ${page} 页不在这份主课件范围内` }
      }
      if (!seen.has(page)) {
        seen.add(page)
        pages.push(page)
      }
    }
    if (pages.length > maxSelected) {
      return {
        pages: pages.slice(0, maxSelected),
        error: `每次最多 ${maxSelected} 页`,
      }
    }
  }

  return { pages, error: '' }
}
