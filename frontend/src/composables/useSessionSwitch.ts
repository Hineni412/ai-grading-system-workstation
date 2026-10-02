import { useConfigWorkspaceStore } from '../stores/config-workspace'
import { useSessionStore } from '../stores/session'

/* 顶栏「当前考试」与命令面板共用的受守卫切换：
   有待核对提交或未保存修改时阻止/确认；成功才切换两侧状态，考试配置页按需加载工作区。 */
export function useSessionSwitch() {
  const sessionStore = useSessionStore()
  const configStore = useConfigWorkspaceStore()

  function switchSession(nextSessionId: number | null): boolean {
    const changesSession = nextSessionId !== configStore.sessionId
    if (changesSession && configStore.hasPendingSubmission) {
      window.alert('上一次上传或生成的结果仍在核对。为避免重复处理，请先回到考试配置完成核对，再切换考试。')
      return false
    }
    if (changesSession && configStore.hasDirtyEditor) {
      const discard = window.confirm('当前评分依据有未保存修改。切换考试会丢弃这些修改，是否继续？')
      if (!discard) return false
      configStore.discardEditorDraft()
    }
    if (!configStore.selectSession(nextSessionId)) return false
    sessionStore.selectSession(nextSessionId)
    return true
  }

  return { switchSession }
}
