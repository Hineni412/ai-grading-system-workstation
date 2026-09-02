import type { PptxLocalOutput } from '../../api/workbench'

export interface AuditBadge {
  key: 'superscript' | 'pageBudget' | 'questionPages'
  label: string
  tone: 'success' | 'warning' | 'danger'
  detail: string
}

/**
 * 把本机执行产物里的机械审计报告映射成对照页的徽标视图。
 * 上下标残留与题页核对失败是 danger（内容可能有误）；
 * 页数超红线是 warning（可以上课，但建议删页）。
 */
export function auditBadges(output: PptxLocalOutput): AuditBadge[] {
  const audit = output.audit
  const superscript = audit.superscript_subscript
  const budget = audit.page_budget
  const questionPages = audit.question_pages
  return [
    {
      key: 'superscript',
      label: superscript.finding_count > 0
        ? `上下标疑似错误 ${superscript.finding_count} 处`
        : '上下标扫描通过',
      tone: superscript.finding_count > 0 ? 'danger' : 'success',
      detail: superscript.finding_count > 0
        ? `涉及第 ${[...new Set(superscript.findings.map(item => item.page))].join('、')} 页，导出后请人工复核。`
        : `已扫描 ${superscript.scanned_runs} 段文字，未发现整句上下标残留。`,
    },
    {
      key: 'pageBudget',
      label: budget.over_limit
        ? `页数超红线：${budget.final_page_count}/${budget.limit} 页`
        : `页数 ${budget.final_page_count}/${budget.limit} 页`,
      tone: budget.over_limit ? 'warning' : 'success',
      detail: budget.suggestion,
    },
    {
      key: 'questionPages',
      label: questionPages.problem_count > 0
        ? `题页页码核对 ${questionPages.problem_count} 处冲突`
        : `题页页码核对通过（${questionPages.checked} 题）`,
      tone: questionPages.problem_count > 0 ? 'danger' : 'success',
      detail: questionPages.problem_count > 0
        ? questionPages.problems.join('；')
        : questionPages.checked > 0
          ? `插入题页落在第 ${Object.keys(questionPages.pages).join('、')} 页，与题号一一对应。`
          : '本课没有插入题页。',
    },
  ]
}

/** 学案下载文件名：优先服务端回执，其次产物里的学案文件名。 */
export function downloadFilename(
  contentDisposition: string | null,
  fallback: string,
): string {
  if (!contentDisposition) return fallback
  const star = /filename\*=UTF-8''([^;]+)/i.exec(contentDisposition)
  if (star?.[1]) {
    try {
      return decodeURIComponent(star[1])
    } catch {
      return fallback
    }
  }
  const plain = /filename="([^"]+)"/i.exec(contentDisposition)
  return plain?.[1] ?? fallback
}
