import { inject, provide, type InjectionKey } from 'vue'

import type { VaultSessionModule } from './createVaultSessionModule'

const vaultSessionKey: InjectionKey<VaultSessionModule> = Symbol('class-teacher-vault-session')

export function provideVaultSession(session: VaultSessionModule): void {
  provide(vaultSessionKey, session)
}

export function useVaultSession(): VaultSessionModule {
  const session = inject(vaultSessionKey)
  if (!session) throw new Error('class-teacher vault session is not provided')
  return session
}
