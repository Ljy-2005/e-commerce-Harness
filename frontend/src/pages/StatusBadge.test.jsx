import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { StatusBadge } from './Dashboard'

// Dashboard 顶层 import api（vi.mock 防副作用）
vi.mock('../api', () => ({
  getHealth: vi.fn(),
  getAdminStatus: vi.fn(),
  getSessions: vi.fn(),
}))

describe('StatusBadge 状态映射', () => {
  const cases = [
    ['created', '已创建'],
    ['running', '运行中'],
    ['completed', '已完成'],
    ['waiting_human', '待人工审查'],
    ['failed', '失败'],
  ]
  for (const [status, label] of cases) {
    it(`${status} → ${label}`, () => {
      render(<StatusBadge status={status} />)
      expect(screen.getByText(label)).toBeInTheDocument()
    })
  }

  it('未知状态原样显示', () => {
    render(<StatusBadge status="weird" />)
    expect(screen.getByText('weird')).toBeInTheDocument()
  })
})
