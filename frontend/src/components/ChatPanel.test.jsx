import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import ChatPanel, { msgPreview, senderIcon } from './ChatPanel'

// Mock WS 钩子与 api 模块（不发起真实网络）
vi.mock('../useWebSocket', () => ({ useWebSocket: vi.fn() }))
vi.mock('../api', () => ({ interjectSession: vi.fn() }))
import { useWebSocket } from '../useWebSocket'
import { interjectSession } from '../api'

function wsState(overrides = {}) {
  return {
    messages: [],
    connected: true,
    error: '',
    send: vi.fn(() => true),
    ...overrides,
  }
}

beforeEach(() => {
  vi.clearAllMocks()
  useWebSocket.mockReturnValue(wsState())
})

describe('msgPreview 分支', () => {
  it('空内容返回空串', () => expect(msgPreview(null)).toBe(''))
  it('error → ❌ 前缀', () => expect(msgPreview({ error: 'boom' })).toBe('❌ boom'))
  it('category → 品类', () => expect(msgPreview({ category: '保健品' })).toBe('品类: 保健品'))
  it('overall_score → 评分+判定', () => expect(msgPreview({ overall_score: 82, verdict: 'pass' })).toBe('评分: 82/100 | pass'))
  it('passed → 合规通过/不通过', () => {
    expect(msgPreview({ passed: true })).toBe('合规: 通过')
    expect(msgPreview({ passed: false })).toBe('合规: 不通过')
  })
  it('decision → 决策', () => expect(msgPreview({ decision: 'approve' })).toBe('决策: approve'))
  it('feedback 截断 80 字符', () => expect(msgPreview({ feedback: 'x'.repeat(100) })).toHaveLength(84))
  it('hitl → 需要人工审查', () => expect(msgPreview({ hitl: true })).toBe('⏸ 需要人工审查'))
  it('context_action → 上下文', () => expect(msgPreview({ context_action: 'compressed' })).toBe('上下文: compressed'))
  it('memory_recall → 🧠', () => expect(msgPreview({ memory_recall: '经验' })).toBe('🧠 经验'))
  it('winner → 🏆 最优', () => expect(msgPreview({ winner: 'v1', winner_score: 90 })).toBe('🏆 最优: v1 (90/100)'))
  it('interjection → 📣 用户插话', () => expect(msgPreview({ interjection: '换个风格' })).toBe('📣 用户插话: 换个风格'))
  it('agent_name → 邀请', () => expect(msgPreview({ agent_name: '审查员', task_brief: '审查图片' })).toBe('邀请: 审查员 — 审查图片'))
  it('action done → ✅ 任务完成', () => expect(msgPreview({ action: 'done' })).toBe('✅ 任务完成'))
  it('全部 Mock 占位图 → 明确提示', () => {
    const content = { images: [{ model_used: 'mock/svg-placeholder' }, { image_url: 'data:image/svg+xml;base64,x' }] }
    expect(msgPreview(content)).toContain('占位图')
  })
  it('真实生图不触发占位提示', () => {
    const content = { images: [{ model_used: 'seedream/seedream-5.0', image_url: 'data:image/jpeg;base64,x' }] }
    expect(msgPreview(content)).not.toContain('占位图')
  })
  it('其他内容 JSON 截断 120', () => expect(msgPreview({ a: 1 })).toBe('{"a":1}'))
})

describe('senderIcon', () => {
  it('已知角色返回对应图标，未知角色兜底 🤖', () => {
    expect(senderIcon('商品分析员')).toBe('🔍')
    expect(senderIcon('外星人')).toBe('🤖')
  })
})

describe('ChatPanel 渲染状态', () => {
  it('WS 错误 → 显示连接失败卡片', () => {
    useWebSocket.mockReturnValue(wsState({ error: '连接中断' }))
    render(<ChatPanel sessionId="s1" />)
    expect(screen.getByText(/群聊连接失败/)).toBeInTheDocument()
  })

  it('未连接且无消息 → 显示连接中', () => {
    useWebSocket.mockReturnValue(wsState({ connected: false }))
    render(<ChatPanel sessionId="s1" />)
    expect(screen.getByText('正在连接群聊...')).toBeInTheDocument()
  })

  it('已连接无消息 → 空状态', () => {
    render(<ChatPanel sessionId="s1" />)
    expect(screen.getByText('等待群聊开始...')).toBeInTheDocument()
  })

  it('按轮分组并渲染轮次分割线', () => {
    useWebSocket.mockReturnValue(wsState({
      messages: [
        { id: 'm1', turn: 0, role: 'coordinator', sender: '中心决策者', content: { agent_name: '商品分析员' } },
        { id: 'm2', turn: 0, role: 'agent', sender: '商品分析员', content: { category: '保健品' } },
        { id: 'm3', turn: 1, role: 'system', sender: '系统', content: { action: 'done' } },
      ],
    }))
    render(<ChatPanel sessionId="s1" />)
    expect(screen.getByText('— 第 0 轮 —')).toBeInTheDocument()
    expect(screen.getByText('— 第 1 轮 —')).toBeInTheDocument()
    expect(screen.getByText(/品类: 保健品/)).toBeInTheDocument()
  })

  it('邀请/完成徽章渲染', () => {
    useWebSocket.mockReturnValue(wsState({
      messages: [
        { id: 'm1', turn: 0, role: 'coordinator', sender: '中心决策者', action: 'invite', content: { agent_name: '审查员' } },
        { id: 'm2', turn: 0, role: 'system', sender: '系统', action: 'done', content: { action: 'done' } },
      ],
    }))
    render(<ChatPanel sessionId="s1" />)
    expect(screen.getAllByText('邀请').length).toBeGreaterThan(0)
    expect(screen.getByText('完成')).toBeInTheDocument()
  })

  it('消息默认折叠预览，点击展开完整 JSON', async () => {
    const user = userEvent.setup()
    useWebSocket.mockReturnValue(wsState({
      messages: [{ id: 'm1', turn: 0, role: 'agent', sender: '商品分析员', content: { category: '保健品', extra: 'hidden' } }],
    }))
    render(<ChatPanel sessionId="s1" />)
    expect(screen.getByText(/品类: 保健品/)).toBeInTheDocument()
    await user.click(screen.getByText('▼ 展开完整内容'))
    expect(screen.getByText(/"extra": "hidden"/)).toBeInTheDocument()
  })

  it('过滤器：点击 Agent 只显示 agent 消息', async () => {
    const user = userEvent.setup()
    useWebSocket.mockReturnValue(wsState({
      messages: [
        { id: 'm1', turn: 0, role: 'coordinator', sender: '中心决策者', content: { agent_name: 'x' } },
        { id: 'm2', turn: 0, role: 'agent', sender: '商品分析员', content: { category: '保健品' } },
      ],
    }))
    render(<ChatPanel sessionId="s1" />)
    await user.click(screen.getByRole('button', { name: 'Agent' }))
    expect(screen.queryByText(/邀请: x/)).not.toBeInTheDocument()
    expect(screen.getByText(/品类: 保健品/)).toBeInTheDocument()
  })

  it('typing 指示器：running 态且有消息时显示', () => {
    useWebSocket.mockReturnValue(wsState({ messages: [{ id: 'm1', turn: 0, role: 'agent', sender: '商品分析员', content: { category: 'x' } }] }))
    render(<ChatPanel sessionId="s1" status="running" />)
    expect(screen.getByText('正在输入…')).toBeInTheDocument()
  })

  it('typing 指示器：completed 态不显示', () => {
    useWebSocket.mockReturnValue(wsState({ messages: [{ id: 'm1', turn: 0, role: 'agent', sender: '商品分析员', content: { category: 'x' } }] }))
    render(<ChatPanel sessionId="s1" status="completed" />)
    expect(screen.queryByText('正在输入…')).not.toBeInTheDocument()
  })

  it('插话：WS 在线时 send 收到 {type:chat, content}', async () => {
    const user = userEvent.setup()
    const send = vi.fn(() => true)
    useWebSocket.mockReturnValue(wsState({ send }))
    render(<ChatPanel sessionId="s1" />)
    await user.type(screen.getByPlaceholderText(/插话指挥群聊/), '换个风格')
    await user.click(screen.getByRole('button', { name: '发送插话' }))
    expect(send).toHaveBeenCalledWith({ type: 'chat', content: '换个风格' })
    expect(interjectSession).not.toHaveBeenCalled()
  })

  it('插话：WS 不可用时回退 REST 并清空输入', async () => {
    const user = userEvent.setup()
    const send = vi.fn(() => false)
    interjectSession.mockResolvedValue({})
    useWebSocket.mockReturnValue(wsState({ send }))
    render(<ChatPanel sessionId="s1" />)
    await user.type(screen.getByPlaceholderText(/插话指挥群聊/), '加个场景')
    await user.click(screen.getByRole('button', { name: '发送插话' }))
    expect(interjectSession).toHaveBeenCalledWith('s1', '加个场景')
    expect(screen.getByPlaceholderText(/插话指挥群聊/)).toHaveValue('')
  })

  it('发送按钮：空输入禁用', () => {
    render(<ChatPanel sessionId="s1" />)
    expect(screen.getByRole('button', { name: '发送插话' })).toBeDisabled()
  })
})
