export type HistoryScrollAnchor = {
  id?: string
  top: number
  scrollTop: number
  height: number
}

export function captureHistoryScroll(el: HTMLElement): HistoryScrollAnchor {
  const top = el.getBoundingClientRect().top
  const first = Array.from(el.querySelectorAll<HTMLElement>('[data-history-id]'))
    .find(node => node.getBoundingClientRect().bottom > top)
  return {
    id: first?.dataset.historyId,
    top: first ? first.getBoundingClientRect().top - top : 0,
    scrollTop: el.scrollTop,
    height: el.scrollHeight,
  }
}

export function restoreHistoryScroll(el: HTMLElement, anchor: HistoryScrollAnchor): void {
  const node = Array.from(el.querySelectorAll<HTMLElement>('[data-history-id]'))
    .find(node => node.dataset.historyId === anchor.id)
  if (node) {
    el.scrollTop += node.getBoundingClientRect().top - el.getBoundingClientRect().top - anchor.top
  } else {
    el.scrollTop = anchor.scrollTop + el.scrollHeight - anchor.height
  }
}
