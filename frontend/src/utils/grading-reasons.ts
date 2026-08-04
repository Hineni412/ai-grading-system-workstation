const REASON_LABELS: Record<string, string> = {
  objective_api_disabled: '客观题识别未启用，已转教师复核',
  objective_api_not_configured: '客观题识别模型配置不完整，已转教师复核',
  objective_paper_model_failed: '客观题识别请求失败，已转教师复核',
  objective_paper_region_failed: '无法读取选填题作答区域',
  objective_region_not_found: '未找到此题的有效作答区域',
  missing_question_result: 'AI 未返回此题的识别结果',
  duplicate_question_result: 'AI 返回了重复的识别结果',
  paper_key_mismatch: '识别结果与当前答卷不一致',
  low_confidence: '作答辨识度较低，需要教师复核',
  needs_review: 'AI 建议教师复核',
  objective_needs_review: '客观题识别结果需要教师复核',
  objective_score_uncertain: '答案识别存在不确定性',
  prompt_injection_or_score_bait: '作答区出现与答题无关的批改指令',
  discarded_answer_only: '只识别到已经涂抹或作废的答案',
  assignment_changed: '批改期间答卷匹配发生变化',
  missing_detail_question_ids: 'AI 未完整返回所有小问的评分结果',
  duplicate_detail_question_id: 'AI 重复返回了同一小问',
  unexpected_detail_question_id: 'AI 返回了不属于本题的小问',
  no_numeric_value: '未能可靠识别填写的数值，需要教师确认',
}

export function translateGradingReason(
  value: string | null | undefined,
  fallback = '自动处理未完成，请教师复核',
): string {
  const text = value?.trim() ?? ''
  if (!text) return fallback
  const direct = REASON_LABELS[text.toLocaleLowerCase()]
  if (direct) return direct
  const embeddedCode = Object.entries(REASON_LABELS).find(([code]) => (
    text.toLocaleLowerCase().includes(code)
  ))
  if (embeddedCode) return embeddedCode[1]
  if (/[\u3400-\u9fff]/u.test(text)) return text
  return fallback
}
