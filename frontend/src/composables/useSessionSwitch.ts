import { useConfigWorkspaceStore } from '../stores/config-workspace'
import { useSessionStore } from '../stores/session'
import { useConfirm } from './useConfirm'

/* 顶栏「当前考试」与命令面板共用的受守卫切换：
   有待核对提交或未保存修改时阻止/确认；成功才切换两侧状态，考试配置页按需加载工作区。 */
export function useSessionSwitch() {
  const sessionStore = useSessionStore()
  const configStore = useConfigWorkspaceStore()
  const { confirm, alert } = useConfirm()
  let switching = false

  async function switchSession(nextSessionId: number | null): Promise<boolean> {
    if (switching) return false
    switching = true
    try {
      const changesSession = nextSessionId !== configStore.sessionId
      if (changesSession && configStore.hasPendingSubmission) {
        await alert({ title: '请先完成核对', message: '上一次上传或生成的结果仍在核对。为避免重复处理，请先回到考试配置完成核对，再切换考试。' })
        return false
      }
      if (changesSession && configStore.hasDirtyEditor) {
        const discard = await confirm({
          title: '切换考试？',
          message: '当前评分依据有未保存修改，切换考试会丢弃这些修改。',
          confirmLabel: '切换',
          danger: true,
        })
        if (!discard) return false
        configStore.discardEditorDraft()
      }
      if (!configStore.selectSession(nextSessionId)) return false
      sessionStore.selectSession(nextSessionId)
      return true
    } finally {
      switching = false
    }
  }

  return { switchSession }
}
