import { readonly, ref } from 'vue'

/**
 * 拆题结果列表与生成面板之间的轻量定位通道：
 * 生成面板的「定位 Qx」请求在这里落点，拆题列表监听后选中并滚动到该行。
 */
const focusedQuestionId = ref('')
const focusRequestToken = ref(0)

export function useConfigQuestionFocus() {
  return {
    focusedQuestionId: readonly(focusedQuestionId),
    focusRequestToken: readonly(focusRequestToken),
    requestQuestionFocus(questionId: string) {
      focusedQuestionId.value = questionId
      focusRequestToken.value += 1
    },
  }
}
