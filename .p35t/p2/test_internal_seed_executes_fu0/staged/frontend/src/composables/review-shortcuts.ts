export type ReviewShortcutCommand =
  | 'focus-search'
  | 'fit-width'
  | 'zoom-in'
  | 'zoom-out'

type ReviewShortcutHandler = (command: ReviewShortcutCommand) => void

export class ReviewShortcutBus {
  private readonly handlers = new Set<ReviewShortcutHandler>()

  subscribe(handler: ReviewShortcutHandler): () => void {
    this.handlers.add(handler)
    return () => {
      this.handlers.delete(handler)
    }
  }

  dispatch(command: ReviewShortcutCommand): void {
    for (const handler of [...this.handlers]) handler(command)
  }
}

export const reviewShortcutBus = new ReviewShortcutBus()
