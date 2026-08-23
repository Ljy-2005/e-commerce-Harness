import '@testing-library/jest-dom/vitest'

// 每个测试后清理 DOM（@testing-library/react 自动 cleanup，这里兜底）
import { cleanup } from '@testing-library/react'
import { afterEach, vi } from 'vitest'

afterEach(() => {
  cleanup()
  localStorage.clear()
})

// jsdom 未实现 scrollIntoView（ChatPanel 滚动到底部用），打桩防 TypeError
if (!Element.prototype.scrollIntoView) {
  Element.prototype.scrollIntoView = vi.fn()
}
