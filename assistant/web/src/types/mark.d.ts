declare module 'mark.js' {
  export default class Mark {
    constructor(root: HTMLElement)
    mark(
      text: string | string[],
      options?: {
        className?: string
        separateWordSearch?: boolean
        accuracy?: string | { value: string; limiters?: string[] }
        acrossElements?: boolean
        caseSensitive?: boolean
      },
    ): void
    unmark(options?: Record<string, unknown>): void
  }
}
