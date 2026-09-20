import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import Audit from './Audit'

vi.mock('../api', () => ({ getAudit: vi.fn() }))
import { getAudit } from '../api'

const ENTRY_OK = {
  timestamp: '2026-09-14T07:00:00+00:00', agent: '商品分析员', provider: 'deepseek',
  model: 'deepseek-v4-flash', action: 'execute', duration_ms: 812, tokens: 1200,
  cost_usd: 0.00017, status: 'ok', session_id: 'abcdef1234567890',
}

const ENTRY_FAIL = {
  ...ENTRY_OK, agent: '提示词生成员', status: 'failed',
  error: 'DeepSeek API error: 401 @ https://api.deepseek.com/v1 — invalid api key',
}

const AUDIT = { total: 2, entries: [ENTRY_OK, ENTRY_FAIL], stats: {} }

function renderAudit(initialPath = '/audit') {
  return render(
    <MemoryRouter initialEntries={[initialPath]}>
      <Audit />
    </MemoryRouter>,
  )
}

beforeEach(() => {
  vi.clearAllMocks()
  getAudit.mockResolvedValue(AUDIT)
})

describe('审计页错误可见性（B3-25）', () => {
  it('表格新增「错误」列：失败行显示上游原文，成功行显示占位', async () => {
    renderAudit()

    expect(await screen.findByText('错误')).toBeInTheDocument()
    expect(screen.getByText(/401 @ https:\/\/api\.deepseek\.com\/v1/)).toBeInTheDocument()
    // 全文放 title，便于悬停查看
    expect(screen.getByText(/401 @ https:\/\/api\.deepseek\.com\/v1/)).toHaveAttribute(
      'title', ENTRY_FAIL.error,
    )
  })

  it('「只看失败」过滤掉成功条目', async () => {
    const user = userEvent.setup()
    renderAudit()
    await screen.findByText('错误')

    await user.click(screen.getByLabelText(/只看失败/))

    expect(screen.queryByText('商品分析员')).not.toBeInTheDocument()
    expect(screen.getByText('提示词生成员')).toBeInTheDocument()
  })

  it('深链 ?session=<id> 预置过滤条件并提示', async () => {
    renderAudit('/audit?session=abcdef1234567890')

    await waitFor(() => expect(getAudit).toHaveBeenCalledWith(
      expect.objectContaining({ sessionId: 'abcdef1234567890' }),
    ))
    expect(screen.getByText(/仅显示会话/)).toBeInTheDocument()
    expect(screen.getByDisplayValue('abcdef1234567890')).toBeInTheDocument()
  })

  it('无深链时不请求任何会话过滤（回归）', async () => {
    renderAudit()

    await waitFor(() => expect(getAudit).toHaveBeenCalledWith(
      expect.objectContaining({ sessionId: '' }),
    ))
    expect(screen.queryByText(/仅显示会话/)).not.toBeInTheDocument()
  })
})
