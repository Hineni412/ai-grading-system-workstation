import { createApp, nextTick } from 'vue';
import { createPinia, setActivePinia } from 'pinia';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import {
  questionBankApi,
  type QuestionSkillIndex,
  type SkillCandidateList,
  type SkillCandidateSuggestion,
  type SkillCandidateSummary,
} from '../api/question-bank';
import type { JobResponse } from '../api/jobs';
import { ApiError } from '../api/errors';

import QuestionBankStandardSummary from '../components/question-bank/QuestionBankStandardSummary.vue';
import SkillCandidateRunDialog from '../components/question-bank/SkillCandidateRunDialog.vue';
import SkillCandidateReview from '../components/question-bank/SkillCandidateReview.vue';

const VOLUME = 'vol-test';
const counts = { total: 5, ready: 3, pending_review: 2, approved_new: 0, dismissed: 1, unlocated: 1 };

const standardFixture = {
  curriculum_volume_id: VOLUME,
  graph_release_id: 'kgr_1',
  active_release: { release_id: 'r1', label: '2026 春季标准', taxonomy_revision: 3, activated_at: '2026-02-01 08:00:00', reason: '开学启用' },
  versions: [
    { release_id: 'r1', label: '2026 春季标准', taxonomy_revision: 3, status: 'active', activated_at: '2026-02-01 08:00:00', reason: '开学启用' },
    { release_id: 'r0', label: '2025 秋季标准', taxonomy_revision: 2, status: 'retired', activated_at: '2025-09-01 08:00:00', reason: '' },
  ],
  skill_count: 42,
  question_count: 100,
  unlinked_question_count: 7,
  no_usable_evidence_count: 0,
  gap_point_count: 5,
  unlocated_gap_point_count: 1,
  model_calls: 0,
};

const candidateSummary = (overrides: Partial<SkillCandidateSummary> = {}): SkillCandidateSummary => ({
  curriculum_volume_id: VOLUME,
  graph_release_id: 'kgr_1',
  counts,
  pending_suggestion_count: 2,
  approved_unpublished_skill_count: 0,
  active_run: null,
  revision: 4,
  model_calls: 0,
  ...overrides,
});

const previewFixture = {
  curriculum_volume_id: VOLUME,
  graph_release_id: 'kgr_1',
  fingerprint: 'f'.repeat(64),
  counts,
  max_per_run: 600,
  planned_requests: 2,
  batches: [
    { chapter_key: 'ch1', chapter_label: '第一章', gap_count: 2 },
    { chapter_key: 'ch2', chapter_label: '第二章', gap_count: 1 },
  ],
  items: [
    { gap_key: 'ch1|1|v1|p1', question_id: 1, question_number: '1', paper_title: '期中卷', chapter_key: 'ch1', section_key: 'sec1', target: '写出算式' },
  ],
  model_calls: 0,
};

const runFixture = {
  run_id: 'a'.repeat(32),
  curriculum_volume_id: VOLUME,
  graph_release_id: 'kgr_1',
  status: 'completed',
  stale: false,
  retryable: false,
  batches: [{ batch_id: 'b1', chapter_key: 'ch1', gap_keys: ['ch1|1|v1|p1'], status: 'completed', attempts: 1, error: null, uncovered_count: 0 }],
  progress: { total: 1, processed: 1, completed: 1, failed: 0, pending: 0, cancelled: 0 },
};

const jobFixture: JobResponse = {
  id: 9,
  job_type: 'skill_candidate',
  payload: { run_id: 'a'.repeat(32), operation: 'process' },
  result: {},
  status: 'succeeded',
  progress: 1,
  stage: '',
  detail: '',
  error: null,
  cancel_requested: false,
  created_at: '2026-02-01T00:00:00Z',
  started_at: null,
  updated_at: '2026-02-01T00:00:00Z',
  finished_at: null,
};

const gapRef = (key: string, questionId: number) => ({
  gap_key: key,
  question_id: questionId,
  evidence_version_id: `v${questionId}`,
  point_id: `p${questionId}`,
  part_id: `part${questionId}`,
  target: `判定点${questionId}`,
  section_key: 'sec1',
  question_number: String(questionId),
  paper_title: '期中卷',
  paper_id: 1,
});

const suggestionFixture = (overrides: Partial<SkillCandidateSuggestion> = {}): SkillCandidateSuggestion => ({
  suggestion_id: 'sug-1',
  run_id: 'a'.repeat(32),
  curriculum_volume_id: VOLUME,
  graph_release_id: 'kgr_1',
  chapter_key: 'ch1',
  decision: 'new_skill',
  skill_key: '',
  new_skill: { section_key: 'sec1', name: '列式计算', include: '会写算式', exclude: '只写答案', examples: ['例一'] },
  reason: '这些判定点都指向同一能力。',
  gap_refs: [gapRef('g1', 1), gapRef('g2', 2)],
  status: 'pending',
  stale: false,
  created_at: '2026-02-02T00:00:00Z',
  reviewed_at: null,
  result: null,
  ...overrides,
});

const candidateList = (suggestions: SkillCandidateSuggestion[] = [], overrides: Partial<SkillCandidateList> = {}): SkillCandidateList => ({
  revision: 4,
  graph_release_id: 'kgr_1',
  summary: candidateSummary(),
  suggestions,
  approved_skills: [],
  active_run: null,
  ...overrides,
});

const indexFixture: QuestionSkillIndex = {
  graph_release_id: 'kgr_1',
  curriculum_volume_id: VOLUME,
  model_calls: 0,
  question_count: 0,
  unlinked: { no_usable_evidence: 0, no_skill_link: 0 },
  chapters: [{
    id: 'ch1', label: '第一章', question_count: 0, cross_section_skills: [],
    sections: [{
      id: 'sec1', label: '1.1 小节', question_count: 0, topics: [],
      skills: [{
        stable_key: 'sk_sec1_101', display_name: '写算式', full_name: '写算式', question_count: 0,
        type_counts: { '选择题': 0, '多选题': 0, '填空题': 0, '解答题': 0 },
        difficulty: null, criteria_needs_review_count: 0, cross_section: false,
        definition: { observable_evidence: '', include_scope: '', exclude_scope: '' },
      }],
    }],
  }],
};

async function flush() {
  await nextTick();
  await new Promise((resolve) => setTimeout(resolve, 0));
  await nextTick();
}

beforeEach(() => {
  document.body.innerHTML = '';
  setActivePinia(createPinia());
});

afterEach(() => {
  document.body.innerHTML = '';
  localStorage.clear();
  vi.restoreAllMocks();
});

describe('学期标准块', () => {
  it('renders the release, version history, counts and hint without any POST', async () => {
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input);
      const payload = url.includes('/standard-summary') ? standardFixture : candidateSummary();
      return new Response(JSON.stringify(payload), { status: 200, headers: { 'content-type': 'application/json' } });
    });
    const host = document.createElement('div');
    document.body.append(host);
    const app = createApp(QuestionBankStandardSummary, { volumeId: VOLUME });
    app.mount(host);
    await flush();

    expect(document.body.textContent).toContain('当前技能标准 2026 春季标准 · 2026-02-01 启用');
    expect(document.body.textContent).toContain('开学启用');
    expect(document.body.textContent).toContain('历次版本（2）');
    expect(document.body.textContent).toContain('本学期技能 42');
    expect(document.body.textContent).toContain('未挂技能题 7');
    expect(document.body.textContent).toContain('技能缺口判定点 5');
    expect(document.body.textContent).toContain('待整理 3');
    expect(document.body.textContent).toContain('有 3 个技能缺口判定点尚未整理');
    expect(document.body.textContent).toContain('查看这些信息不调用模型。');
    expect(fetchSpy.mock.calls.every((call) => String(call[1]?.method ?? 'GET') !== 'POST')).toBe(true);
    app.unmount();
  });

  it('shows the pending-release hint when approved skills wait for publication', async () => {
    vi.spyOn(questionBankApi, 'standardSummary').mockResolvedValue(standardFixture);
    vi.spyOn(questionBankApi, 'skillCandidateSummary').mockResolvedValue(
      candidateSummary({ approved_unpublished_skill_count: 2, counts: { ...counts, ready: 0 } }) as never,
    );
    const host = document.createElement('div');
    document.body.append(host);
    const app = createApp(QuestionBankStandardSummary, { volumeId: VOLUME });
    app.mount(host);
    await flush();
    expect(document.body.textContent).toContain('有 2 项已批准的新技能等待发布');
    expect(document.body.textContent).not.toContain('尚未整理');
    app.unmount();
  });
});

describe('整理确认窗口', () => {
  it('shows planned requests and cost, posts the confirmed body, and recovers ambiguous submits', async () => {
    const ambiguous = new ApiError({ kind: 'network', status: null, code: 'network_error', message: 'offline', details: {}, requestId: 'r', retryable: true });
    vi.spyOn(questionBankApi, 'skillGapPreview').mockResolvedValue(previewFixture);
    vi.spyOn(questionBankApi, 'skillCandidateRun').mockResolvedValue(runFixture);
    vi.spyOn(questionBankApi, 'skillCandidates').mockResolvedValue(candidateList());
    const startSpy = vi.spyOn(questionBankApi, 'startSkillCandidateRun')
      .mockRejectedValueOnce(ambiguous)
      .mockResolvedValue({ job: jobFixture, run: runFixture });

    const host = document.createElement('div');
    document.body.append(host);
    const app = createApp(SkillCandidateRunDialog, { open: true, volumeId: VOLUME, activeRunId: null });
    app.mount(host);
    await flush();

    expect(document.body.textContent).toContain('本次将整理 3 个判定点，按章分成 2 次 AI 请求');
    expect(document.body.textContent).toContain('会产生费用');
    expect(document.body.textContent).toContain('第一章 · 2 个判定点');

    const confirm = [...document.body.querySelectorAll<HTMLButtonElement>('button')].find((button) => button.textContent?.includes('确认并开始整理'));
    confirm?.click();
    await flush();

    expect(startSpy).toHaveBeenCalledTimes(1);
    const saved = JSON.parse(localStorage.getItem(`question-bank-skill-candidates:${VOLUME}`) ?? '{}') as { fingerprint: string; token: string };
    expect(saved.fingerprint).toBe('f'.repeat(64));
    expect(saved.token).toMatch(/^[0-9a-f]{32}$/);
    expect(startSpy.mock.calls[0]).toEqual([VOLUME, 'f'.repeat(64), saved.token]);
    expect(document.body.textContent).toContain('重新提交');

    const resubmit = [...document.body.querySelectorAll<HTMLButtonElement>('button')].find((button) => button.textContent?.includes('重新提交'));
    resubmit?.click();
    await flush();
    expect(startSpy).toHaveBeenCalledTimes(2);
    expect(startSpy.mock.calls[1]).toEqual(startSpy.mock.calls[0]);
    expect(localStorage.getItem(`question-bank-skill-candidates:${VOLUME}`)).toBeNull();
    app.unmount();
  });
});

describe('审核面板', () => {
  async function mountReview(list: ReturnType<typeof candidateList>) {
    vi.spyOn(questionBankApi, 'skillCandidates').mockResolvedValue(list);
    const host = document.createElement('div');
    document.body.append(host);
    const app = createApp(SkillCandidateReview, { open: true, volumeId: VOLUME, index: indexFixture });
    app.mount(host);
    await flush();
    return app;
  }

  it('sends edits.kind new_skill with expected_revision when the teacher renames', async () => {
    const suggestion = suggestionFixture();
    const reviewSpy = vi.spyOn(questionBankApi, 'reviewSkillCandidate')
      .mockResolvedValue({ suggestion: suggestionFixture({ status: 'accepted' }), result: { approved_skill_id: 'sk_sec1_106' } });
    const app = await mountReview(candidateList([suggestion]));

    const nameInput = document.body.querySelector<HTMLInputElement>('.sc-card__fields input');
    expect(nameInput?.value).toBe('列式计算');
    nameInput!.value = '分步列式';
    nameInput!.dispatchEvent(new Event('input', { bubbles: true }));
    await nextTick();
    const accept = [...document.body.querySelectorAll<HTMLButtonElement>('button')].find((button) => button.textContent === '采纳');
    accept?.click();
    await flush();

    expect(reviewSpy).toHaveBeenCalledTimes(1);
    const body = reviewSpy.mock.calls[0]?.[1] as Record<string, unknown>;
    expect(body.decision).toBe('accept');
    expect(body.expected_revision).toBe(4);
    expect(String(body.request_token)).toMatch(/^[0-9a-f]{32}$/);
    expect((body.edits as Record<string, unknown>).kind).toBe('new_skill');
    expect((body.edits as Record<string, unknown>).name).toBe('分步列式');
    expect(body.gap_keys).toBeUndefined();
    app.unmount();
  });

  it('sends gap_keys when one point is unchecked', async () => {
    const suggestion = suggestionFixture();
    const reviewSpy = vi.spyOn(questionBankApi, 'reviewSkillCandidate')
      .mockResolvedValue({ suggestion: suggestionFixture({ status: 'accepted' }), result: { approved_skill_id: 'sk_sec1_106' } });
    const app = await mountReview(candidateList([suggestion]));

    const first = document.body.querySelector<HTMLInputElement>('.sc-card__gaps input[type="checkbox"]');
    first!.checked = false;
    first!.dispatchEvent(new Event('change', { bubbles: true }));
    await nextTick();
    [...document.body.querySelectorAll<HTMLButtonElement>('button')].find((button) => button.textContent === '采纳')?.click();
    await flush();

    const body = reviewSpy.mock.calls[0]?.[1] as Record<string, unknown>;
    expect(body.gap_keys).toEqual(['g2']);
    app.unmount();
  });

  it('disables accept on stale suggestions except keep_section', async () => {
    const stale = suggestionFixture({ decision: 'link_existing', skill_key: 'sk_sec1_101', stale: true });
    vi.spyOn(questionBankApi, 'reviewSkillCandidate')
      .mockResolvedValue({ suggestion: suggestionFixture({ status: 'accepted' }), result: { dismissed: ['g1'] } });
    const app = await mountReview(candidateList([stale]));

    expect(document.body.textContent).toContain('标准已更新');
    const accept = [...document.body.querySelectorAll<HTMLButtonElement>('button')].find((button) => button.textContent === '采纳');
    expect(accept?.disabled).toBe(true);
    const keepRadio = [...document.body.querySelectorAll<HTMLInputElement>('input[type="radio"]')].find((input) => input.value === 'keep_section');
    expect(keepRadio?.disabled).toBe(false);
    keepRadio!.checked = true;
    keepRadio!.dispatchEvent(new Event('change', { bubbles: true }));
    await nextTick();
    expect(accept?.disabled).toBe(false);
    app.unmount();
  });

  it('reloads and shows the conflict message on a 409', async () => {
    const suggestion = suggestionFixture();
    vi.spyOn(questionBankApi, 'reviewSkillCandidate')
      .mockRejectedValue(new ApiError({ kind: 'conflict', status: 409, code: 'skill_candidate_revision_conflict', message: 'revision', details: {}, requestId: 'r', retryable: false }));
    const listSpy = vi.spyOn(questionBankApi, 'skillCandidates').mockResolvedValue(candidateList([suggestion]));
    const host = document.createElement('div');
    document.body.append(host);
    const app = createApp(SkillCandidateReview, { open: true, volumeId: VOLUME, index: indexFixture });
    app.mount(host);
    await flush();

    [...document.body.querySelectorAll<HTMLButtonElement>('button')].find((button) => button.textContent === '驳回')?.click();
    await flush();
    expect(listSpy).toHaveBeenCalledTimes(2);
    expect(document.body.textContent).toContain('内容已更新，请重新查看后再操作。');
    app.unmount();
  });
});
