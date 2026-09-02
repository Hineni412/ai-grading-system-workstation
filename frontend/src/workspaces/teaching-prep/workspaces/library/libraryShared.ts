import type {
  SemesterMaterialRole,
} from '../../api/catalog'

export const MATERIAL_ROLES: Array<{ value: SemesterMaterialRole; label: string }> = [
  { value: 'textbook', label: '教材' },
  { value: 'reference_ppt', label: '参考课件 / 备选 PPT' },
  { value: 'exercise_workbook', label: '普通教辅' },
  { value: 'homework_workbook', label: '日常作业教辅' },
  { value: 'answer_book', label: '答案册' },
  { value: 'supplement', label: '补充资料' },
]

export function roleLabel(role: SemesterMaterialRole): string {
  return MATERIAL_ROLES.find(item => item.value === role)?.label ?? role
}

export function suggestedRole(name: string): SemesterMaterialRole {
  const normalized = name.toLowerCase()
  if (normalized.endsWith('.pptx')) return 'reference_ppt'
  if (/作业|练习册|同步练/.test(name)) return 'homework_workbook'
  if (/教材|教科书/.test(name)) return 'textbook'
  if (/答案|解析/.test(name)) return 'answer_book'
  return 'supplement'
}

export function fileSizeLabel(size: number): string {
  if (size >= 1024 * 1024) return `${(size / 1024 / 1024).toFixed(1)} MB`
  return `${Math.max(1, Math.round(size / 1024))} KB`
}

/* ===== 资料库（方案 C）选中态与角色分组 ===== */

/** 资料库页左栏选中态：导入 / 章文件夹 / 课时树 / 单份资料（按版本 id）。 */
export type LibrarySelection =
  | { kind: 'import' }
  | { kind: 'chapter'; folderKey: string }
  | { kind: 'tree' }
  | { kind: 'material'; materialId: string }

export const BOOK_ROLES: SemesterMaterialRole[] = [
  'textbook',
  'exercise_workbook',
  'homework_workbook',
  'answer_book',
]

/** 教材/教辅类角色：在资料柜中归入「书」，选中后打开资料详情面板。 */
export function isBookRole(role: SemesterMaterialRole): boolean {
  return BOOK_ROLES.includes(role)
}
