/**
 * 等价答案去重：比较前把全角标点/运算符映射为半角并去掉空白，
 * 与标准答案或更早的等价答案规范化相同的条目被丢弃，保留首个原始写法。
 */
const FULL_WIDTH_MAP: Record<string, string> = {
  '：': ':',
  '，': ',',
  '；': ';',
  '＝': '=',
  '（': '(',
  '）': ')',
  '＋': '+',
  '－': '-',
  '。': '.',
}

export function normalizeAnswerText(text: string): string {
  return [...text.trim().replace(/\s+/g, '')]
    .map((char) => FULL_WIDTH_MAP[char] ?? char)
    .join('')
}

export function dedupeAcceptedAnswers(
  acceptedAnswers: readonly string[],
  standardAnswer: string,
): string[] {
  const standardKey = normalizeAnswerText(standardAnswer)
  const seen = new Set<string>()
  const deduped: string[] = []
  for (const answer of acceptedAnswers) {
    const key = normalizeAnswerText(answer)
    if (key === '' || key === standardKey || seen.has(key)) continue
    seen.add(key)
    deduped.push(answer)
  }
  return deduped
}
