import { describe, expect, it } from 'vitest'

import type { QuestionBankSection, QuestionSelectionItem } from '../../api/workbench'
import {
  buildPreviewRequest,
  inferLessonKind,
  matchSectionForLesson,
  questionBankVolumeId,
  sectionShortName,
  selectionPayloadForFreeze,
} from './questionSelection'

function section(id: string, name: string, chapter = '第四章 一次函数'): QuestionBankSection {
  return {
    section_id: id,
    section_name: name,
    chapter_id: 'c04',
    chapter_name: `八年级上册｜${chapter}`,
    question_count: 12,
  }
}

describe('questionSelection helpers', () => {
  it('maps the prep curriculum to the bundled question bank volume', () => {
    expect(questionBankVolumeId({ grade_level: 8, volume: 'first' })).toBe('bnu24-math-g8-upper')
    expect(questionBankVolumeId({ grade_level: 7, volume: 'second' })).toBe('bnu24-math-g7-lower')
    expect(questionBankVolumeId({ grade_level: 9, volume: 'second' })).toBeNull()
    expect(questionBankVolumeId({ grade_level: 8, volume: 'whole_year' })).toBeNull()
    expect(questionBankVolumeId({ grade_level: 6, volume: 'first' })).toBeNull()
    expect(questionBankVolumeId(null)).toBeNull()
  })

  it('strips the volume/chapter prefix and the numbering from a section name', () => {
    expect(sectionShortName(section('s1', '八年级上册｜第四章 一次函数｜3 一次函数的应用')))
      .toBe('一次函数的应用')
    expect(sectionShortName(section('s2', '1 幂的乘除'))).toBe('幂的乘除')
  })

  it('matches the lesson title to the unique section', () => {
    const sections = [
      section('s1', '八年级上册｜第四章 一次函数｜1 函数'),
      section('s2', '八年级上册｜第四章 一次函数｜3 一次函数的应用'),
    ]
    expect(matchSectionForLesson('第 6 课时 · 一次函数的应用', sections)?.section_id).toBe('s2')
  })

  it('prefers the longest matching section name', () => {
    const sections = [
      section('s1', '八年级上册｜第四章 一次函数｜1 函数'),
      section('s2', '八年级上册｜第四章 一次函数｜2 一次函数'),
    ]
    expect(matchSectionForLesson('第 6 课时 · 一次函数', sections)?.section_id).toBe('s2')
  })

  it('returns null when no section matches or the longest match is tied', () => {
    const tied = [
      section('s1', '八年级上册｜第一章 有理数｜1 复习与回顾', '第一章 有理数'),
      section('s2', '八年级上册｜第二章 实数｜1 复习与回顾', '第二章 实数'),
    ]
    expect(matchSectionForLesson('第 3 课时 · 复习与回顾', tied)).toBeNull()
    expect(matchSectionForLesson('第 6 课时 · 勾股定理', tied)).toBeNull()
    expect(matchSectionForLesson('', tied)).toBeNull()
  })

  it('builds the preview request with deduplicated exclusions', () => {
    expect(buildPreviewRequest('bnu24-math-g8-upper', ['s2', 's2'], [7, 7, 9])).toEqual({
      volume_id: 'bnu24-math-g8-upper',
      section_ids: ['s2'],
      difficulty_max: 5,
      stem_max_chars: 220,
      limit: 8,
      max_per_method: 2,
      exclude_question_ids: [7, 9],
    })
    expect(buildPreviewRequest('bnu24-math-g8-upper', ['s2'], [], 'review')).toMatchObject({
      difficulty_max: 7,
      limit: 12,
    })
  })

  it('infers the lesson kind from the lesson title', () => {
    expect(inferLessonKind('第1课时 认识勾股定理')).toBe('new_lesson')
    expect(inferLessonKind('第一章 小结与复习')).toBe('review')
    expect(inferLessonKind('期中复习课')).toBe('review')
  })

  it('carries the final picked questions into the freeze payload', () => {
    const items = [
      {
        question_id: 101,
        question_type: '解答题',
        stem: '题干',
        difficulty: 4,
        frequency_score: 0.873,
        frequency: { midterm: 0.5, final: 0.3, zhongkao: 0.073 },
        method: '函数建模',
        knowledge_points: [],
        has_images: false,
        selection_reason: { frequency: '综合考频 0.873', difficulty: '难度 4', method: '函数建模' },
      },
      {
        question_id: 102,
        question_type: '填空题',
        stem: '题干 2',
        difficulty: 2,
        frequency_score: 0.5,
        frequency: { midterm: 0.5, final: 0, zhongkao: 0 },
        method: '',
        knowledge_points: [],
        has_images: false,
        selection_reason: { frequency: '综合考频 0.500', difficulty: '难度 2', method: '未标注方法' },
      },
    ] satisfies QuestionSelectionItem[]
    const preview = {
      request: {
        volume_id: 'bnu24-math-g8-upper',
        section_ids: ['s2'],
        difficulty_max: 5,
        stem_max_chars: 220,
        limit: 12,
        max_per_method: 2,
        exclude_question_ids: [],
      },
    }
    expect(selectionPayloadForFreeze(preview, items)).toEqual({
      volume_id: 'bnu24-math-g8-upper',
      section_ids: ['s2'],
      difficulty_max: 5,
      stem_max_chars: 220,
      limit: 12,
      max_per_method: 2,
      items: [
        { question_id: 101, method: '函数建模', difficulty: 4, frequency_score: 0.873 },
        { question_id: 102, method: null, difficulty: 2, frequency_score: 0.5 },
      ],
    })
  })
})
