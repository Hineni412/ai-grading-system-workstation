/**
 * Shared polling for display-refresh reads.
 *
 * The affected GET/POST endpoints answer with the previously saved result
 * and an `X-Data-Refreshing: 1` header while the server recomputes in the
 * background. The caller shows that result immediately, sets a refreshing
 * flag, and re-requests the same scope until a fresh response arrives or
 * the attempt budget is exhausted.
 */

export const DISPLAY_REFRESH_INTERVAL_MS = 3_000
export const DISPLAY_REFRESH_MAX_TRIES = 60

export interface DisplayRefreshHandlers<T> {
  /** Apply the payload for the current scope (stale or fresh). */
  apply?: (result: T) => void
  /**
   * Report the refresh state after each settled response: `refreshing` true
   * while the server is still recomputing, `exhausted` once the attempt
   * budget is spent so the caller can show the "check back later" hint.
   */
  report?: (refreshing: boolean, exhausted: boolean) => void
  /**
   * Return true when this request chain is obsolete (scope changed, the
   * controller was aborted, or the view was left). Obsolete results are
   * never applied and never change the refresh flags.
   */
  aborted?: () => boolean
}

export function displayRefreshDelay(milliseconds: number): Promise<void> {
  return new Promise(resolve => setTimeout(resolve, milliseconds))
}

export async function requestWithRefresh<T>(
  issue: (onRefreshing: (refreshing: boolean) => void) => Promise<T>,
  handlers: DisplayRefreshHandlers<T> = {},
  delay: (milliseconds: number) => Promise<void> = displayRefreshDelay,
): Promise<T> {
  const { apply, report, aborted } = handlers
  for (let attempt = 0; ; attempt += 1) {
    let marked = false
    const result = await issue(refreshing => { marked = refreshing })
    if (aborted?.()) return result
    apply?.(result)
    if (!marked) {
      report?.(false, false)
      return result
    }
    const exhausted = attempt + 1 >= DISPLAY_REFRESH_MAX_TRIES
    report?.(true, exhausted)
    if (exhausted) return result
    await delay(Math.min(DISPLAY_REFRESH_INTERVAL_MS, 50 * 2 ** attempt))
    if (aborted?.()) return result
  }
}
