import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import ChatPanel, { msgPreview, senderIcon, PromptPlanView, PromptReviewView } from './ChatPanel'

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
  // 用户反馈（2026-09-20）："agent 的对话里面显示的会很直白，会给出代码原文，
  // 实际上我们只要文字的内容显示" —— 兜底**不再是 JSON**，而是人话/字段清单摘要
  it('结构化消息自带 message → 显示那句话（不再被结构顶掉）', () => {
    const content = {
      quality_report: { count: 10, white_bg_ok: true },
      set_plan_coverage: { expected: 10, produced: 8 },
      message: '图一（真实商品图）+ 生成图的双条件出图完成，共 10 张；本地体检发现 2 个问题',
    }
    expect(msgPreview(content)).toContain('双条件出图完成')
    expect(msgPreview(content)).not.toContain('{')
  })
  it('没有 message 的结构化消息 → 字段清单摘要（不是 JSON）', () => {
    const preview = msgPreview({ quality_report: { count: 10 }, set_plan_summary: '拼多多 共 10 张' })
    expect(preview).toContain('本地体检')
    expect(preview).toContain('套图编排摘要')
    expect(preview).not.toContain('{')
  })
  it('未知字段兜底也不再吐 JSON', () => {
    expect(msgPreview({ a: 1 })).not.toContain('{"a":1}')
    expect(msgPreview({ a: 1 })).toContain('a')
  })
  // 用户反馈：要能"指定每一张的提示词"，而不是看一坨 JSON
  it('prompt_plan → 📝 本套逐张提示词', () => {
    expect(msgPreview({ prompt_plan: [{ number: 1 }, { number: 2 }] }))
      .toBe('📝 本套逐张提示词（2 张）')
  })
  it('prompt_lint → 体检结论（硬伤/通过）', () => {
    expect(msgPreview({ prompt_lint: { errors: ['缺槽位'], warnings: [] } }))
      .toBe('🧪 提示词体检：❌ 1 项硬伤')
    expect(msgPreview({ prompt_lint: { errors: [], warnings: [] } }))
      .toBe('🧪 提示词体检：✅ 通过')
  })
  it('prompt_review → 审美审核摘要', () => {
    expect(msgPreview({ prompt_review: { message: '🎨 提示词审核（6 张）：审美分 平均 78.0' } }))
      .toContain('审美分')
  })
  it('独立的审美审核消息 → 显示改写张数', () => {
    expect(msgPreview({ revised_slots: ['main_white'], message: '🎨 提示词审核（6 张）：…' }))
      .toContain('已改写 1 张')
  })
  // 风格词库：本次采用哪些风格词条/原型（artifacts.prompts.style_refs 的群聊回放）
  it('style_refs → 直接用档案回放里的 message', () => {
    expect(msgPreview({ style_refs: { enabled: true, entries: [{ id: 's1', name: '冷调实验室风' }],
      slots: {}, anchors: [], notes: [], message: '🎨 采用风格档案：第1张=冷调实验室风' } }))
      .toBe('🎨 采用风格档案：第1张=冷调实验室风')
  })
  it('style_refs 没有 message → 兜底文案（不显示一坨 JSON）', () => {
    expect(msgPreview({ style_refs: { enabled: true, entries: [], slots: {} } }))
      .toBe('🎨 采用风格档案')
  })
})

describe('PromptPlanView / PromptReviewView', () => {
  it('逐张渲染"第N张 + 想要 + 画面描述"', () => {
    render(<PromptPlanView plan={[{ number: 3, slot_id: 'main_ingredients', role: '成分配方图',
      usage: 'detail', kind: 'info', intent: '让买家看清配方', prompt: '上半部留白 40%',
      aesthetic_score: 62 }]} />)
    expect(screen.getByText(/第3张/)).toBeInTheDocument()
    expect(screen.getByText(/想要：让买家看清配方/)).toBeInTheDocument()
    expect(screen.getByText(/审美 62/)).toBeInTheDocument()
    expect(screen.getByText('查看画面描述')).toBeInTheDocument()
  })

  it('体检与审美审核结论逐条展示', () => {
    render(<PromptReviewView
      lint={{ errors: ['缺少平台槽位：main_scene'], warnings: [],
              findings: [{ rule: 'missing_slot', level: 'error', message: '缺少平台槽位：main_scene' }] }}
      review={{ verdict: 'revised', threshold: 85, scores: { main_white: 60 },
                revised_slots: ['main_white'], refine_rejected: [] }} />)
    expect(screen.getByText(/1 项硬伤/)).toBeInTheDocument()
    expect(screen.getByText(/缺少平台槽位/)).toBeInTheDocument()
    expect(screen.getByText(/main_white 60/)).toBeInTheDocument()
    expect(screen.getByText(/已改写：main_white/)).toBeInTheDocument()
  })

  it('没有审核产物时不渲染', () => {
    const { container } = render(<PromptReviewView lint={null} review={null} />)
    expect(container).toBeEmptyDOMElement()
  })
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

  it('展开后正文给人话，原始字段默认折叠在「技术详情」里', async () => {
    const user = userEvent.setup()
    useWebSocket.mockReturnValue(wsState({
      messages: [{ id: 'm1', turn: 0, role: 'agent', sender: '商品分析员',
        content: { category: '保健品', note: 'hidden' } }],
    }))
    render(<ChatPanel sessionId="s1" />)
    expect(screen.getByText(/品类: 保健品/)).toBeInTheDocument()
    await user.click(screen.getByText(/展开详情/))
    const bubble = document.querySelector('.chat-bubble')
    // 正文给的是人话
    expect(bubble.textContent).toContain('品类: 保健品')
    // 原始字段被关在「技术详情」里，**默认不展开**（需要时才打开）
    const tech = Array.from(bubble.querySelectorAll('details summary'))
      .find(el => el.textContent.includes('技术详情'))
      .closest('details')
    expect(tech.open).toBe(false)
    expect(tech.querySelector('pre').textContent).toContain('"note": "hidden"')
    // 点开才看得到原文（信息没被藏掉）
    await user.click(screen.getByText('技术详情（原始字段）'))
    expect(tech.open).toBe(true)
  })

  it('展开的正文里不出现签名地址与图片数据', async () => {
    const user = userEvent.setup()
    const signed = 'https://ark-example.tos-cn-beijing.volces.com/img.jpeg?X-Tos-Signature=deadbeef&X-Tos-Credential=AKLTabcdef'
    const blob = 'A'.repeat(400)
    useWebSocket.mockReturnValue(wsState({
      messages: [{ id: 'm1', turn: 0, role: 'agent', sender: '生图员',
        content: { images: [{ slot_id: 'main_white', image_url: signed, base64_data: blob,
          saved_path: 'default/s1/pinduoduo_main_white_1.jpg' }] } }],
    }))
    render(<ChatPanel sessionId="s1" />)
    await user.click(screen.getByText(/展开详情/))
    const text = document.body.textContent
    expect(text).not.toContain('X-Tos-Signature')
    expect(text).not.toContain('deadbeef')
    expect(text).not.toContain(blob)
    // 落盘路径只留文件名（不暴露目录结构）
    expect(text).toContain('pinduoduo_main_white_1.jpg')
    expect(text).not.toContain('default/s1/pinduoduo_main_white_1.jpg')
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
