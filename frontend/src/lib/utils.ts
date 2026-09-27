import { clsx, type ClassValue } from 'clsx'
import { twMerge } from 'tailwind-merge'

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}

// 小数难度归入最近的整数档（半上取整）：6.4→6、6.5→7、7.5→8、10.0→10。
// 只用于“属于哪一级”的分类/配色；范围比较一律直接用原始小数。
export function difficultyLevel(value: unknown): number | null {
  const parsed = Number(value)
  if (!Number.isFinite(parsed) || parsed < 1 || parsed > 10) return null
  return Math.floor(parsed + 0.5)
}
