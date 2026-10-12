import { ref } from 'vue'

// 试卷篮抽屉的开合状态在组卷工作台与题库管理之间共享，仅保留在会话内。
export const assemblyBasketOpen = ref(false)

export function toggleAssemblyBasket(open?: boolean): void {
  assemblyBasketOpen.value = open === undefined ? !assemblyBasketOpen.value : open
}
