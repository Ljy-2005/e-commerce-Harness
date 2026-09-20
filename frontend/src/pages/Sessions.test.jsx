import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import Sessions, { capabilitySummary } from './Sessions'

vi.mock('../api', () => ({
  getSessions: vi.fn(),
  createSession: vi.fn(),
  deleteSession: vi.fn(),
  getSettings: vi.fn(),
  getPlatforms: vi.fn(),
}))
import { getSessions, getSettings, getPlatforms } from '../api'

const CAPS_MIXED = [
  { capability: 'text', provider: 'deepseek', model: 'deepseek-v4-flash', is_mock: false },
  { capability: 'vision', provider: 'deepseek', model: 'deepseek-v4-flash-vision-exp', is_mock: false },
  { capability: 'image', provider: 'mock', model: 'mock', is_mock: true },
]

function renderSessions() {
  return render(
    <MemoryRouter>
      <Sessions />
    </MemoryRouter>,
  )
}

const PLATFORMS = [
  { slug: 'taobao', label: '淘宝', aspect: '1:1', bg: '#FFFFFF', text_policy: 'composite',
    max_images: 5, slot_count: 5, detail_slot_count: 3, is_default: true,
    slot_roles: [
      { slot_id: 'main_white', label: '纯商品图', kind: 'photo', usage: 'main' },
      { slot_id: 'main_selling_point', label: '核心卖点图', kind: 'info', usage: 'main' },
      { slot_id: 'main_spec', label: '规格参数图', kind: 'info', usage: 'detail' },
    ] },
  { slug: 'pinduoduo', label: '拼多多', aspect: '1:1', bg: '#FFFFFF', text_policy: 'none',
    max_images: 10, slot_count: 6, detail_slot_count: 4, is_default: false,
    slot_roles: [
      { slot_id: 'main_white', label: '纯商品图', kind: 'photo', usage: 'main' },
      { slot_id: 'main_ingredients', label: '成分配方图', kind: 'info', usage: 'main' },
    ] },
]

beforeEach(() => {
  vi.clearAllMocks()
  getSessions.mockResolvedValue({ sessions: [] })
  getSettings.mockResolvedValue({ mock_mode: false, capabilities: CAPS_MIXED })
  getPlatforms.mockResolvedValue({ platforms: PLATFORMS })
})

describe('平台选择器（配置驱动）', () => {
  it('平台清单来自后端档案，包含拼多多及默认平台规范', async () => {
    renderSessions()
    expect(await screen.findByRole('option', { name: /拼多多/ })).toBeInTheDocument()
    expect(screen.getByRole('option', { name: /淘宝/ })).toBeInTheDocument()
    // 默认平台（淘宝）的规范提示：背景 / 文字策略 / 主图上限
    expect(screen.getByText(/主图上限 5 张/)).toBeInTheDocument()
    expect(screen.getByText(/文字由系统排版/)).toBeInTheDocument()
  })

  it('展示套图角色（含"图文"标记：信息图由系统本地排版）', async () => {
    renderSessions()
    expect(await screen.findByText(/套图角色：/)).toBeInTheDocument()
    expect(screen.getByText(/核心卖点图（图文）/)).toBeInTheDocument()
    expect(screen.getByText(/纯商品图/)).toBeInTheDocument()
  })

  it('切换平台后展示该平台规范（拼多多：白底图不得添加文字）', async () => {
    const user = (await import('@testing-library/user-event')).default.setup()
    renderSessions()
    const platformSelect = await screen.findByRole('combobox', { name: '目标平台' })
    await user.selectOptions(platformSelect, 'pinduoduo')
    expect(await screen.findByText(/白底图不得添加文字/)).toBeInTheDocument()
    expect(screen.getByText(/主图上限 10 张/)).toBeInTheDocument()
    expect(screen.getByText(/成分配方图（图文）/)).toBeInTheDocument()
  })

  it('选项里同时给出主图与图文张数', async () => {
    renderSessions()
    expect(await screen.findByRole('option', { name: /主图 5 \+ 图文 3/ })).toBeInTheDocument()
    expect(screen.getByRole('option', { name: /主图 6 \+ 图文 4/ })).toBeInTheDocument()
  })

  it('接口失败时回落默认淘宝，不影响建任务', async () => {
    getPlatforms.mockRejectedValue(new Error('boom'))
    renderSessions()
    expect(await screen.findByRole('option', { name: /淘宝/ })).toBeInTheDocument()
  })
})

describe('capabilitySummary（纯函数）', () => {
  it('真实能力显示 provider/model，回落能力标注 Mock（回落）', () => {
    const rows = capabilitySummary(CAPS_MIXED)
    expect(rows[0].text).toBe('文本：deepseek/deepseek-v4-flash')
    expect(rows[2].text).toBe('生图：Mock（回落）')
  })
})

describe('新建任务表单的能力警示（B3-26）', () => {
  it('生图回落 Mock 时给出醒目警示 + 设置页入口', async () => {
    renderSessions()

    expect(await screen.findByText(/生图能力当前回落/)).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /去设置/ })).toHaveAttribute('href', '/settings')
    // 能力条：文本真实、生图回落
    expect(screen.getByText('文本：deepseek/deepseek-v4-flash')).toBeInTheDocument()
    expect(screen.getByText('生图：Mock（回落）')).toBeInTheDocument()
  })

  it('配置了真实图像 Provider 时不再警示（回归）', async () => {
    getSettings.mockResolvedValue({
      mock_mode: false,
      capabilities: [
        ...CAPS_MIXED.slice(0, 2),
        { capability: 'image', provider: 'seedream', model: 'seedream-5.0', is_mock: false },
      ],
    })
    renderSessions()

    expect(await screen.findByText('生图：seedream/seedream-5.0')).toBeInTheDocument()
    expect(screen.queryByText(/生图能力当前回落/)).not.toBeInTheDocument()
  })

  it('Mock 模式整机提示（产出为模板数据）', async () => {
    getSettings.mockResolvedValue({
      mock_mode: true,
      capabilities: CAPS_MIXED.map(c => ({ ...c, provider: 'mock', is_mock: true })),
    })
    renderSessions()

    expect(await screen.findByText(/当前为 Mock 模式/)).toBeInTheDocument()
  })

  it('设置接口失败时不阻塞新建表单（回归）', async () => {
    getSettings.mockRejectedValue(new Error('boom'))
    renderSessions()

    expect(await screen.findByText('新建商品图任务')).toBeInTheDocument()
    expect(screen.queryByText(/生图能力当前回落/)).not.toBeInTheDocument()
  })
})
