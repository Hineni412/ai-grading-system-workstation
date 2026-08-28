// PROTOTYPE — throwaway, ?variant= 切换，mock 数据，验收后删除
import { reactive } from 'vue'

export type StepKind = 'safety' | 'record' | 'branch' | 'communicate' | 'followup'
export type StepStatus = 'in_progress' | 'pending' | 'locked' | 'completed' | 'pruned'

export interface PrototypeStep {
  id: string
  title: string
  kind: StepKind
  status: StepStatus
  hint?: string
  aiUpdated: boolean
  aiNote: string
  record: string
  recordDraft: string
  chosenOption?: string
}

export interface BranchOption {
  key: string
  label: string
  targetStepId: string | null
}

export interface SupplementItem {
  id: string
  text: string
  done: boolean
  keywords: string[]
}

export interface StudentDraft {
  name: string
  classLabel: string
  line: string
  summary: string
  confirmed: boolean
}

export const kindLabel: Record<StepKind, string> = {
  safety: '安全必做',
  record: '记录',
  branch: '判断分支',
  communicate: '沟通',
  followup: '跟进',
}

export const statusLabel: Record<StepStatus, string> = {
  in_progress: '进行中',
  pending: '待处理',
  locked: '未解锁',
  completed: '已完成 ✓',
  pruned: '已剪枝',
}

export function createSopPrototypeState() {
  const state = reactive({
    steps: [
      { id: '1', title: '确认现场已停止冲突且学生当前安全', kind: 'safety', status: 'in_progress', aiUpdated: false, aiNote: '', record: '', recordDraft: '' },
      { id: '2', title: '分别记录各方表述', kind: 'record', status: 'pending', aiUpdated: false, aiNote: '', record: '', recordDraft: '' },
      { id: '3', title: '区分一致事实、直接观察和争议内容', kind: 'record', status: 'pending', aiUpdated: false, aiNote: '', record: '', recordDraft: '' },
      { id: '4', title: '分流判断', kind: 'branch', status: 'locked', aiUpdated: false, aiNote: '', record: '', recordDraft: '', chosenOption: '' },
      { id: '5a', title: '组织双方面对面调解沟通', kind: 'communicate', status: 'locked', aiUpdated: false, aiNote: '', record: '', recordDraft: '' },
      { id: '5b', title: '按欺凌处置流程上报并做好保护性隔离', kind: 'safety', status: 'locked', aiUpdated: false, aiNote: '', record: '', recordDraft: '' },
      { id: '6', title: '与双方家长分别沟通', kind: 'communicate', status: 'locked', hint: '可参考沟通模板', aiUpdated: false, aiNote: '', record: '', recordDraft: '' },
      { id: '7', title: '一周后复查两人情绪与关系', kind: 'followup', status: 'locked', aiUpdated: false, aiNote: '', record: '', recordDraft: '' },
    ] as PrototypeStep[],
    branchOptions: [
      { key: 'mediate', label: '普通矛盾调解', targetStepId: '5a' },
      { key: 'report', label: '疑似欺凌按流程上报', targetStepId: '5b' },
      { key: 'parents-first', label: '需先与家长沟通', targetStepId: null },
    ] as BranchOption[],
    supplements: [
      { id: 's1', text: '冲突具体起因', done: false, keywords: ['起因', '因为', '抢', '座位', '电脑'] },
      { id: 's2', text: '双方是否有受伤或情绪异常', done: false, keywords: ['受伤', '伤', '情绪', '哭', '分开', '无人'] },
      { id: 's3', text: '目击者陈述内容', done: false, keywords: ['目击', '看到', '旁观', '同学'] },
    ] as SupplementItem[],
    students: [
      { name: '张立璞', classLabel: '七年级（3）班', line: '因信息课与同学发生冲突，配合度良好，待进一步沟通', summary: '张立璞近期课堂表现正常；本次冲突中为主动一方还是被动一方尚待核实，建议先分别谈话再作判断。', confirmed: false },
      { name: '钱肖白', classLabel: '七年级（3）班', line: '因信息课与同学发生冲突，情绪状态待观察', summary: '钱肖白在冲突后情绪偏低落，需观察一至两天；过往无类似冲突记录，暂不预设责任方。', confirmed: false },
    ] as StudentDraft[],
    expandedStudent: '',
    aiNotice: '',
    toast: '',
    updating: false,
    discarded: false,
  })

  let toastToken = 0

  function stepBy(id: string): PrototypeStep {
    return state.steps.find((step) => step.id === id) as PrototypeStep
  }

  function showToast(text: string): void {
    state.toast = text
    const token = ++toastToken
    window.setTimeout(() => {
      if (token === toastToken) state.toast = ''
    }, 2600)
  }

  function prereqsDone(step: PrototypeStep): boolean {
    if (step.id === '2') return stepBy('1').status === 'completed'
    if (step.id === '3') return stepBy('2').status === 'completed'
    if (step.id === '4') return stepBy('3').status === 'completed'
    if (step.id === '5a' || step.id === '5b') return stepBy('4').status === 'completed'
    if (step.id === '6') {
      const s4 = stepBy('4')
      if (s4.status !== 'completed' || !s4.chosenOption) return false
      if (s4.chosenOption === 'parents-first') return true
      const target = s4.chosenOption === 'mediate' ? '5a' : '5b'
      return stepBy(target).status === 'completed'
    }
    if (step.id === '7') return stepBy('6').status === 'completed'
    return true
  }

  function canComplete(step: PrototypeStep): boolean {
    return (step.status === 'in_progress' || step.status === 'pending') && prereqsDone(step)
  }

  function setPending(id: string): void {
    const step = stepBy(id)
    if (step.status === 'locked') step.status = 'pending'
  }

  function completeStep(id: string): void {
    const step = stepBy(id)
    if (!canComplete(step)) return
    step.status = 'completed'
    if (id === '3') setPending('4')
    if (id === '5a' || id === '5b') setPending('6')
    if (id === '6') setPending('7')
  }

  function canChooseBranch(): boolean {
    const s4 = stepBy('4')
    return s4.status === 'pending' && prereqsDone(s4)
  }

  function chooseBranch(optionKey: string): void {
    if (!canChooseBranch()) return
    const option = state.branchOptions.find((item) => item.key === optionKey)
    if (!option) return
    const s4 = stepBy('4')
    s4.status = 'completed'
    s4.chosenOption = optionKey
    if (option.targetStepId === '5a') {
      setPending('5a')
      stepBy('5b').status = 'pruned'
    } else if (option.targetStepId === '5b') {
      setPending('5b')
      stepBy('5a').status = 'pruned'
    } else {
      stepBy('5a').status = 'pruned'
      stepBy('5b').status = 'pruned'
      setPending('6')
    }
  }

  function saveRecord(id: string): void {
    const step = stepBy(id)
    step.record = step.recordDraft.trim()
  }

  function requestAiUpdate(): void {
    if (state.updating || state.discarded) return
    state.updating = true
    window.setTimeout(() => {
      for (const step of state.steps) {
        if (step.status === 'completed' || step.status === 'pruned') continue
        step.aiUpdated = true
        step.aiNote = '更新版：AI 已结合已完成步骤与新补充信息，调整了本步骤的执行重点。'
      }
      state.updating = false
      showToast('AI 已根据已完成步骤更新后续安排')
    }, 1500)
  }

  function discardSop(): void {
    if (window.confirm('确定弃用这份 SOP？弃用后它将从工作区移除。（原型仅演示）')) {
      state.discarded = true
    }
  }

  function sendAiInput(text: string): void {
    const input = text.trim()
    if (!input) return
    let matched = state.supplements.filter((item) => !item.done && item.keywords.some((word) => input.includes(word)))
    if (!matched.length) {
      const first = state.supplements.find((item) => !item.done)
      matched = first ? [first] : []
    }
    for (const item of matched) item.done = true
    state.aiNotice = matched.length
      ? `AI 已归位：「${input}」已整理进流程，并标记 ${matched.length} 条待补充为已补充。`
      : `AI 已归位：「${input}」已整理进流程记录。`
    showToast('AI 已归位')
  }

  function toggleStudent(name: string): void {
    state.expandedStudent = state.expandedStudent === name ? '' : name
  }

  function confirmStudentWrite(name: string): void {
    const student = state.students.find((item) => item.name === name)
    if (student) student.confirmed = true
  }

  return {
    state,
    stepBy,
    prereqsDone,
    canComplete,
    completeStep,
    canChooseBranch,
    chooseBranch,
    saveRecord,
    requestAiUpdate,
    discardSop,
    sendAiInput,
    toggleStudent,
    confirmStudentWrite,
    showToast,
  }
}

export type SopPrototype = ReturnType<typeof createSopPrototypeState>
