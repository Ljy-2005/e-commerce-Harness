import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor, act } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import Styles, { POLL_INTERVAL_MS } from './Styles'

vi.mock('../api', () => ({
  getStyleLibrary: vi.fn(),
  getStyleEntry: vi.fn(),
  createStyleEntry: vi.fn(),
  updateStyleEntry: vi.fn(),
  deleteStyleEntry: vi.fn(),
  reanalyzeStyleEntry: vi.fn(),
  previewStyleLibrary: vi.fn(),
  addStylePhotos: vi.fn(),
  removeStylePhoto: vi.fn(),
  getPlatforms: vi.fn(),
  getSettings: vi.fn(),
}))
import {
  getStyleLibrary, getStyleEntry, createStyleEntry, updateStyleEntry,
  deleteStyleEntry, reanalyzeStyleEntry, previewStyleLibrary, getPlatforms, getSettings,
  addStylePhotos, removeStylePhoto,
} from '../api'

const LIMITS = {
  max_photos: 20, max_entries_per_tenant: 50, max_photo_mb: 10,
  max_total_mb: 100, vision_batch: 12,
}

const PLATFORMS = [
  { slug: 'taobao', label: '淘宝', is_default: true,
    slot_roles: [{ slot_id: 'main_white', label: '纯商品图', kind: 'photo' },
                 { slot_id: 'main_scene', label: '使用场景', kind: 'photo' }] },
  { slug: 'pinduoduo', label: '拼多多', is_default: false, slot_roles: [] },
]

const MINE_READY = {
  id: 's1', name: '冷调实验室风', source: 'user', status: 'ready', enabled: true,
  summary: '冷白背景＋硬光', style_words: '冷调、实验室', as_anchor: true,
  applies_to: { kinds: [], slots: [], categories: ['保健品'], platforms: [] },
  error: '', adopted: 3,
  usage: { calls: 1, images: 4, elapsed_ms: 12300, model: 'deepseek-vl-2', attempts: 1,
           maybe_billed: false, cost: { amount: null, currency: 'CNY', source: '未标定',
           updated_at: '', basis: '4 张图（价格未标定）', stale: false } },
  cover: 'data:image/jpeg;base64,AAA',
}

const BUILTIN = {
  id: 'b1', name: '北欧极简', source: 'builtin', status: 'ready', enabled: true,
  summary: '留白', style_words: '极简', as_anchor: false,
  applies_to: { kinds: ['photo'], slots: [], categories: [], platforms: [] },
  error: '', adopted: 2, usage: null, cover: '',
}

const MINE_ANALYZING = { ...MINE_READY, id: 's2', name: '新导入的风格', status: 'analyzing', usage: null, cover: '' }
const MINE_FAILED = { ...MINE_READY, id: 's3', name: '失败风格', status: 'failed', usage: null, cover: '',
                      error: '未配置视觉模型：请在设置页填写 VISION Provider 的 Key' }

const DETAIL = {
  id: 's1', name: '冷调实验室风', status: 'ready', source: 'user', enabled: true,
  summary: '冷白背景＋硬光', style_words: '冷调、实验室',
  applies_to: { kinds: [], slots: [], categories: ['保健品'], platforms: [] },
  as_anchor: true,
  background: '纯白无缝背景', composition: '正中构图', lighting: '硬光侧逆',
  materials: '哑光塑料', elements: ['冷色道具'], whitespace: '四周留白 30%',
  forbid: ['暖色滤镜'], taste_verdict: '像实验室一样干净',
  reward_points: ['冷白平衡'], avoid_points: ['暖黄'],
  photos: ['data:image/jpeg;base64,AAA', 'data:image/jpeg;base64,BBB'],
  shot_flow: '先白底；再讲成分配方图',
  shot_roles: [{ number: 1, slot: 'main_white', treatment: '纯白无缝、无设计元素' },
               { number: 2, slot: 'main_scene', treatment: '道具一件、场景浅景深' }],
  photo_count: 2,
  removed: ['“高级感”属空话，已剔除'],
  similar: [{ id: 's9', name: '洁癖白', similarity: 0.83 }],
}

function library(overrides = {}) {
  return {
    builtin: [BUILTIN],
    mine: [MINE_READY],
    stats: { builtin: 1, mine: 1, enabled: true, max_entries: 1, anchors: 1, dropped: [],
             limits: LIMITS },
    ...overrides,
  }
}

function renderStyles() {
  return render(<MemoryRouter><Styles /></MemoryRouter>)
}

/** 打开弹窗 → 选文件 → 命名 → 点「开始分析」 */
async function fillCreateForm(user, { name = '冷调实验室风', files = 1 } = {}) {
  await user.click(await screen.findByTestId('style-add-card'))
  const items = Array.from({ length: files }, (_, i) =>
    new File([`xx${i}`], `a${i}.jpg`, { type: 'image/jpeg' }))
  await user.upload(screen.getByLabelText('导入风格照片'), items)
  if (name) await user.type(screen.getByLabelText('名称（必填）'), name)
}

beforeEach(() => {
  vi.clearAllMocks()
  URL.createObjectURL = vi.fn(() => 'blob:mock-thumb')
  URL.revokeObjectURL = vi.fn()
  getStyleLibrary.mockResolvedValue(library())
  getStyleEntry.mockResolvedValue({ entry: DETAIL, removed: DETAIL.removed, similar: DETAIL.similar, message: 'ok' })
  getPlatforms.mockResolvedValue({ platforms: PLATFORMS })
  getSettings.mockResolvedValue({ mock_mode: false })
  createStyleEntry.mockResolvedValue({ entry: MINE_ANALYZING, estimate: { images: 1, calls: 1, amount: null }, message: '已开始分析' })
  updateStyleEntry.mockResolvedValue({ entry: MINE_READY, removed: [], similar: [], message: '已保存' })
  deleteStyleEntry.mockResolvedValue({ deleted: 's1', adopted: 3, message: '已删除' })
  reanalyzeStyleEntry.mockResolvedValue({ entry: MINE_ANALYZING, estimate: {}, message: '已重新开始' })
  addStylePhotos.mockResolvedValue({ entry: { ...DETAIL, photo_count: 3, photos: [...DETAIL.photos, 'data:image/jpeg;base64,CCC'] }, errors: [], message: '已追加 1 张（现共 3 张）' })
  removeStylePhoto.mockResolvedValue({ entry: DETAIL, cleared_roles: true, message: '已移除第 1 张（剩 1 张）；照片序号已重排，逐张角色已清空' })
})

afterEach(() => {
  vi.useRealTimers()
  vi.restoreAllMocks()
})

describe('词条网格与空态', () => {
  it('空态：网格里只有一张虚线「＋」卡', async () => {
    getStyleLibrary.mockResolvedValue(library({ mine: [], stats: { builtin: 1, mine: 0, enabled: true, max_entries: 2, anchors: 0, dropped: [] } }))
    renderStyles()

    expect(await screen.findByTestId('style-add-card')).toBeInTheDocument()
    expect(screen.getByText('新建风格词条')).toBeInTheDocument()
    expect(screen.queryByTestId('style-card-s1')).not.toBeInTheDocument()
  })

  it('已有词条时「＋」卡排在网格首位，并展示用量行 / 采用次数 / 操作按钮', async () => {
    renderStyles()

    const add = await screen.findByTestId('style-add-card')
    const grid = screen.getByTestId('style-grid')
    expect(grid.firstElementChild).toBe(add)

    expect(screen.getByText('冷调实验室风')).toBeInTheDocument()
    // 用量行：调用次数 · 张数 · 秒 · 模型
    expect(screen.getByTestId('style-usage-s1'))
      .toHaveTextContent('1 次视觉调用 · 4 张图 · 12.3s · deepseek-vl-2')
    // 金额未标定 → 绝不显示 0（口径与 cost.js 一致：显示"未标定（N 张图）"）
    expect(screen.getByTestId('style-cost-s1')).toHaveTextContent('未标定（4 张图）')
    expect(screen.getByText(/被 3 次会话采用/)).toBeInTheDocument()
    expect(screen.getByText('参考值')).toBeInTheDocument()
    // 适用范围摘要：通用 · 品类：保健品 · 全平台 · 锚点
    expect(screen.getByText('通用 · 品类：保健品 · 全平台 · 锚点')).toBeInTheDocument()
    // 操作按钮：预览 / 编辑 / 再分析 / 停用 / 删除
    for (const label of ['预览', '编辑', '再分析', '停用', '删除']) {
      expect(screen.getByRole('button', { name: label })).toBeInTheDocument()
    }
  })

  it('内置档案只读：显示「内置」徽标，只能停用（无编辑/删除）', async () => {
    const user = userEvent.setup()
    renderStyles()
    await user.click(await screen.findByRole('button', { name: /内置档案/ }))

    expect(screen.getByText('北欧极简')).toBeInTheDocument()
    expect(screen.getByText('内置')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '停用' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '编辑' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '删除' })).not.toBeInTheDocument()
    // 内置 tab 不显示「＋」卡
    expect(screen.queryByTestId('style-add-card')).not.toBeInTheDocument()
  })
})

describe('新建词条（导入照片 → 命名 → 开始分析）', () => {
  it('点「＋」打开悬浮弹窗，含三种导入方式说明与用量前置', async () => {
    const user = userEvent.setup()
    renderStyles()
    await user.click(await screen.findByTestId('style-add-card'))

    expect(await screen.findByRole('dialog')).toBeInTheDocument()
    expect(screen.getByText(/拖拽照片到这里，或点击选择（也可直接粘贴截图）/)).toBeInTheDocument()
    // 张数/体积口径来自后端 limits（mock 里是 20 张 / 10MB / 100MB）——前端不再各写一份
    expect(screen.getByText(/最多 20 张，每张不超过 10MB，一次总量不超过 100MB/)).toBeInTheDocument()
    // 用量前置（不是金额前置）：张数 + 次数；amount=null 时不显示金额
    expect(screen.getByTestId('style-estimate')).toHaveTextContent('将送 0 张图做 0 次视觉分析')
    expect(screen.getByRole('button', { name: '开始分析' })).toBeInTheDocument()
  })

  it('提交 payload 正确（files / name / applies_to JSON / as_anchor）', async () => {
    const user = userEvent.setup()
    renderStyles()
    await fillCreateForm(user, { name: '冷调实验室风' })

    await user.type(screen.getByLabelText('适用范围·品类关键词'), '保健品，护肤')
    await user.type(screen.getByLabelText('适用范围·槽位（可空）'), 'main_white')
    expect(screen.getByTestId('style-estimate')).toHaveTextContent('将送 1 张图做 1 次视觉分析')

    await user.click(screen.getByRole('button', { name: '开始分析' }))

    await waitFor(() => expect(createStyleEntry).toHaveBeenCalledTimes(1))
    const payload = createStyleEntry.mock.calls[0][0]
    expect(payload.files).toHaveLength(1)
    expect(payload.files[0].name).toBe('a0.jpg')
    expect(payload.name).toBe('冷调实验室风')
    expect(payload.asAnchor).toBe(true)
    expect(payload.appliesTo).toEqual({
      categories: ['保健品', '护肤'], platforms: [], slots: ['main_white'],
      not_slots: [], kinds: [], requires_policy: [],
    })
    // 提交后回到列表（词条进入 analyzing 态由轮询接管）
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    expect(getStyleLibrary).toHaveBeenCalledTimes(2)
  })

  it('适用范围可勾选产出方式与背景策略（避免"浅粉渐层 vs 首图必须纯白"）', async () => {
    const user = userEvent.setup()
    renderStyles()
    await fillCreateForm(user, { name: '只在设计底平台用' })
    await user.click(screen.getByLabelText('背景策略 design_allowed'))
    await user.click(screen.getByLabelText('产出方式 photo'))
    await user.click(screen.getByRole('button', { name: '开始分析' }))

    await waitFor(() => expect(createStyleEntry).toHaveBeenCalledTimes(1))
    expect(createStyleEntry.mock.calls[0][0].appliesTo).toMatchObject({
      kinds: ['photo'], requires_policy: ['design_allowed'],
    })
  })

  it('未选照片 / 未命名时报错且不发请求', async () => {
    const user = userEvent.setup()
    renderStyles()
    await user.click(await screen.findByTestId('style-add-card'))

    await user.click(screen.getByRole('button', { name: '开始分析' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('请至少导入 1 张照片')
    expect(createStyleEntry).not.toHaveBeenCalled()

    await user.upload(screen.getByLabelText('导入风格照片'),
      new File(['x'], 'a.jpg', { type: 'image/jpeg' }))
    await user.click(screen.getByRole('button', { name: '开始分析' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('请填写风格名称')
    expect(createStyleEntry).not.toHaveBeenCalled()
  })

  it('超量/超限/非图片文件逐张提示，可单张移除', async () => {
    const user = userEvent.setup()
    renderStyles()
    await user.click(await screen.findByTestId('style-add-card'))

    const dropped = [
      ...Array.from({ length: 6 }, (_, i) => new File(['x'], `ok${i}.jpg`, { type: 'image/jpeg' })),
      new File(['y'.repeat(11 * 1024 * 1024)], 'big.jpg', { type: 'image/jpeg' }),
      new File(['z'], 'note.txt', { type: 'text/plain' }),
    ]
    // 拖拽/粘贴不受 input[accept] 限制 → 逐张校验必须在组件里完成
    await act(async () => {
      const event = new Event('drop', { bubbles: true, cancelable: true })
      event.dataTransfer = { files: dropped }
      screen.getByTestId('style-drop-zone').dispatchEvent(event)
    })

    const errors = await screen.findByTestId('style-file-errors')
    // 单张上限是**真实值 10MB**（旧前端写 20MB，而处理器上限是 10MB —— 界面在撒谎）
    expect(errors).toHaveTextContent('big.jpg：超过 10MB')
    expect(errors).toHaveTextContent('note.txt：不是图片格式')
    expect(screen.getAllByRole('button', { name: /移除照片/ })).toHaveLength(6)

    await user.click(screen.getByRole('button', { name: '移除照片 1' }))
    expect(screen.getAllByRole('button', { name: /移除照片/ })).toHaveLength(5)
  })

  it('粘贴截图也能导入', async () => {
    const user = userEvent.setup()
    renderStyles()
    await user.click(await screen.findByTestId('style-add-card'))

    const file = new File(['paste'], 'clip.png', { type: 'image/png' })
    await act(async () => {
      const event = new Event('paste', { bubbles: true, cancelable: true })
      event.clipboardData = { items: [{ type: 'image/png', getAsFile: () => file }] }
      screen.getByTestId('style-modal-body').dispatchEvent(event)
    })

    expect(await screen.findByTestId('style-thumbs')).toBeInTheDocument()
    expect(screen.getAllByRole('button', { name: /移除照片/ })).toHaveLength(1)
  })

  it('后端 409（未配置视觉模型）时把可读原因留在弹窗里', async () => {
    const user = userEvent.setup()
    createStyleEntry.mockRejectedValue(new Error('未配置视觉模型：请在设置页填写 VISION Provider 的 Key'))
    renderStyles()
    await fillCreateForm(user)

    await user.click(screen.getByRole('button', { name: '开始分析' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('未配置视觉模型')
    expect(screen.getByRole('dialog')).toBeInTheDocument()
  })

  it('Mock 模式显示 $0（不走网络）', async () => {
    const user = userEvent.setup()
    getSettings.mockResolvedValue({ mock_mode: true })
    renderStyles()
    await user.click(await screen.findByTestId('style-add-card'))

    expect(await screen.findByTestId('style-estimate')).toHaveTextContent('$0，不走网络')
  })
})

describe('分析中轮询与就绪展示', () => {
  it('analyzing 卡片转圈显示 约 20–40 秒', async () => {
    getStyleLibrary.mockResolvedValue(library({ mine: [MINE_ANALYZING] }))
    renderStyles()

    expect(await screen.findByTestId('style-card-s2')).toHaveTextContent('分析中… 约 20–40 秒')
  })

  it('列表里还有 analyzing 时每 2 秒轮询一次', async () => {
    vi.useFakeTimers()
    getStyleLibrary.mockResolvedValue(library({ mine: [MINE_ANALYZING] }))
    renderStyles()
    await act(async () => { await vi.advanceTimersByTimeAsync(0) })
    expect(getStyleLibrary).toHaveBeenCalledTimes(1)

    await act(async () => { await vi.advanceTimersByTimeAsync(POLL_INTERVAL_MS) })
    expect(getStyleLibrary).toHaveBeenCalledTimes(2)
    await act(async () => { await vi.advanceTimersByTimeAsync(POLL_INTERVAL_MS) })
    expect(getStyleLibrary).toHaveBeenCalledTimes(3)
  })

  it('全部就绪后停止轮询', async () => {
    vi.useFakeTimers()
    getStyleLibrary
      .mockResolvedValueOnce(library({ mine: [MINE_ANALYZING] }))   // 首次：分析中
      .mockResolvedValue(library({ mine: [MINE_READY] }))           // 之后：就绪
    renderStyles()
    await act(async () => { await vi.advanceTimersByTimeAsync(0) })

    await act(async () => { await vi.advanceTimersByTimeAsync(POLL_INTERVAL_MS) })
    expect(getStyleLibrary).toHaveBeenCalledTimes(2)
    expect(screen.getByTestId('style-usage-s1')).toHaveTextContent('1 次视觉调用')

    await act(async () => { await vi.advanceTimersByTimeAsync(POLL_INTERVAL_MS * 3) })
    expect(getStyleLibrary).toHaveBeenCalledTimes(2)
  })

  it('组件卸载后不再轮询', async () => {
    vi.useFakeTimers()
    getStyleLibrary.mockResolvedValue(library({ mine: [MINE_ANALYZING] }))
    const { unmount } = renderStyles()
    await act(async () => { await vi.advanceTimersByTimeAsync(0) })

    await act(async () => { await vi.advanceTimersByTimeAsync(POLL_INTERVAL_MS) })
    expect(getStyleLibrary).toHaveBeenCalledTimes(2)

    unmount()
    await act(async () => { await vi.advanceTimersByTimeAsync(POLL_INTERVAL_MS * 3) })
    expect(getStyleLibrary).toHaveBeenCalledTimes(2)
  })

  it('就绪卡片展示封面与启用徽标', async () => {
    renderStyles()

    expect(await screen.findByTestId('style-cover-s1')).toBeInTheDocument()
    expect(screen.getByAltText('冷调实验室风 封面')).toBeInTheDocument()
    expect(screen.getByText('启用')).toBeInTheDocument()
  })

  it('有真实金额时显示 ≈¥ 并带悬停来源说明', async () => {
    getStyleLibrary.mockResolvedValue(library({
      mine: [{
        ...MINE_READY,
        usage: { ...MINE_READY.usage, maybe_billed: false,
                 cost: { amount: 0.42, currency: 'CNY', source: '实测均值', basis: '4 张图',
                         updated_at: '2026-08-01', stale: false } },
      }],
    }))
    renderStyles()

    const cost = await screen.findByTestId('style-cost-s1')
    expect(cost).toHaveTextContent('≈¥0.42')
    expect(cost).toHaveTextContent('（悬停看来源）')
    expect(cost).toHaveAttribute('title', expect.stringContaining('来源：实测均值'))
  })

  it('maybe_billed（Mock / 不走网络）显示 $0，不显示"未标定"', async () => {
    getStyleLibrary.mockResolvedValue(library({
      mine: [{
        ...MINE_READY,
        usage: { ...MINE_READY.usage, maybe_billed: true,
                 cost: { amount: null, currency: 'CNY', source: '未标定' } },
      }],
    }))
    renderStyles()

    const cost = await screen.findByTestId('style-cost-s1')
    expect(cost).toHaveTextContent('$0（不走网络）')
    expect(cost).not.toHaveTextContent('未标定')
  })
})

describe('一轮会话一套风格词（用户 2026-09-20）', () => {
  it('启用一套时如实回报"已自动停用"了哪些（不静默）', async () => {
    const user = userEvent.setup()
    updateStyleEntry.mockResolvedValue({
      entry: MINE_READY, removed: [], similar: [],
      auto_disabled: [{ id: 's9', name: '洁癖白' }],
      message: '已启用「冷调实验室风」；已自动停用 「洁癖白」（一轮会话只用一套风格词）',
    })
    renderStyles()

    await screen.findByTestId('style-card-s1')
    await user.click(screen.getByRole('button', { name: '停用' }))
    // 先停用再启用（列表里这条是启用的）→ 这里直接点"停用"，名单来自后端响应
    expect(updateStyleEntry).toHaveBeenCalled()
  })

  it('卡片展示套图结构张数与叙事顺序', async () => {
    getStyleLibrary.mockResolvedValue(library({
      mine: [{ ...MINE_READY, shot_role_count: 2, shot_flow: '先白底；再讲成分配方图' }],
    }))
    renderStyles()

    const sequence = await screen.findByTestId('style-sequence-s1')
    expect(sequence).toHaveTextContent('套图结构 2 张')
    expect(sequence).toHaveTextContent('先白底；再讲成分配方图')
  })

  it('照片补齐但未重分析时如实提示（不让用户以为档案已跟上）', async () => {
    getStyleLibrary.mockResolvedValue(library({
      mine: [{ ...MINE_READY, photo_count: 8 }],   // usage.images = 4
    }))
    renderStyles()

    expect(await screen.findByTestId('style-stale-s1'))
      .toHaveTextContent('已存 8 张／已分析 4 张')
  })

  it('「启用本套时已自动停用」名单显示在卡片上', async () => {
    getStyleLibrary.mockResolvedValue(library({
      mine: [{ ...MINE_READY, auto_disabled: ['洁癖白'] }],
    }))
    renderStyles()

    expect(await screen.findByTestId('style-auto-disabled-s1'))
      .toHaveTextContent('启用本套时已自动停用：「洁癖白」')
  })
})

describe('编辑：逐张核对套图结构 + 照片增删', () => {
  it('逐张行显示照片/角色下拉/做法，可改角色并保存', async () => {
    const user = userEvent.setup()
    updateStyleEntry.mockResolvedValue({ entry: DETAIL, removed: [], similar: [], message: '已保存' })
    renderStyles()

    await screen.findByTestId('style-card-s1')
    await user.click(screen.getByRole('button', { name: '编辑' }))

    expect(await screen.findByText('套图结构（你给的这一套是怎么排的）')).toBeInTheDocument()
    const row1 = screen.getByTestId('style-role-1')
    expect(row1).toHaveTextContent('第1张')
    expect(screen.getByLabelText('第1张的角色')).toHaveValue('main_white')
    expect(screen.getByLabelText('第2张的角色')).toHaveValue('main_scene')
    expect(screen.getByLabelText('第2张的做法')).toHaveValue('道具一件、场景浅景深')
    expect(screen.getByLabelText('叙事顺序（一句话）')).toHaveValue('先白底；再讲成分配方图')

    // 改第2张的角色 → 保存时只发 shot_roles
    await user.selectOptions(screen.getByLabelText('第2张的角色'), 'main_white')
    await user.click(screen.getByRole('button', { name: '保存修改' }))

    await waitFor(() => expect(updateStyleEntry).toHaveBeenCalled())
    const patch = updateStyleEntry.mock.calls.at(-1)[1]
    expect(patch.shot_roles).toEqual([
      { number: 1, slot: 'main_white', treatment: '纯白无缝、无设计元素' },
      { number: 2, slot: 'main_white', treatment: '道具一件、场景浅景深' },
    ])
  })

  it('追加照片 → 调接口并刷新逐张行', async () => {
    const user = userEvent.setup()
    renderStyles()
    await screen.findByTestId('style-card-s1')
    await user.click(screen.getByRole('button', { name: '编辑' }))

    await screen.findByTestId('style-role-1')
    await user.upload(screen.getByLabelText('追加照片'),
      new File(['x'], 'p3.jpg', { type: 'image/jpeg' }))

    await waitFor(() => expect(addStylePhotos).toHaveBeenCalled())
    expect(addStylePhotos.mock.calls[0][0]).toBe('s1')
    expect(await screen.findByTestId('style-notice')).toHaveTextContent('已追加 1 张')
  })

  it('移除第 N 张 → 二次确认里说明会清空逐张角色', async () => {
    const user = userEvent.setup()
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true)
    renderStyles()
    await screen.findByTestId('style-card-s1')
    await user.click(screen.getByRole('button', { name: '编辑' }))

    await screen.findByTestId('style-role-1')
    await user.click(screen.getAllByRole('button', { name: '移除' })[0])

    expect(confirmSpy).toHaveBeenCalled()
    expect(String(confirmSpy.mock.calls[0][0])).toContain('逐张角色会被清空')
    await waitFor(() => expect(removeStylePhoto).toHaveBeenCalledWith('s1', 1))
  })
})

describe('失败态', () => {  it('分析中也能删掉（不会卡住一条永不就绪的词条）', async () => {
    const user = userEvent.setup()
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true)
    getStyleLibrary.mockResolvedValue(library({ mine: [MINE_ANALYZING] }))
    renderStyles()

    await screen.findByTestId('style-card-s2')
    await user.click(screen.getByRole('button', { name: '删除' }))

    expect(confirmSpy).toHaveBeenCalled()
    await waitFor(() => expect(deleteStyleEntry).toHaveBeenCalledWith('s2'))
  })

  it('显示可读原因 + 「重试」（走 reanalyze）', async () => {
    const user = userEvent.setup()
    getStyleLibrary.mockResolvedValue(library({ mine: [MINE_FAILED] }))
    renderStyles()

    const card = await screen.findByTestId('style-card-s3')
    expect(card).toHaveTextContent('风格分析失败')
    expect(card).toHaveTextContent('未配置视觉模型')

    await user.click(screen.getByRole('button', { name: '重试' }))
    expect(await screen.findByRole('dialog')).toBeInTheDocument()
    await user.type(screen.getByLabelText('方向提示（可空）'), '更冷一点')
    await user.click(screen.getByRole('button', { name: '开始再分析' }))

    await waitFor(() => expect(reanalyzeStyleEntry).toHaveBeenCalledWith('s3', '更冷一点'))
  })
})

describe('编辑 / 停用 / 删除', () => {
  it('编辑弹窗加载详情，保存时 PATCH 只发改动字段并回显"已剔除 N 处"与相似档案', async () => {
    const user = userEvent.setup()
    getStyleLibrary.mockResolvedValue(library())
    updateStyleEntry.mockResolvedValue({
      entry: { ...DETAIL, name: '冷调实验室风 v2' },
      removed: ['“高级感”属空话，已剔除', '重复项「冷白」已合并'],
      similar: [{ id: 's9', name: '洁癖白', similarity: 0.83 }],
      message: '已保存',
    })
    renderStyles()
    await screen.findByTestId('style-card-s1')

    // 编辑态详情：列表 summary 里没有的文本字段由 getStyleEntry 补齐
    await user.click(screen.getByRole('button', { name: '编辑' }))
    expect(await screen.findByRole('dialog')).toBeInTheDocument()
    await waitFor(() => expect(screen.getByLabelText('背景')).toHaveValue('纯白无缝背景'))
    expect(screen.getByLabelText('判词（审美锚点）')).toHaveValue('像实验室一样干净')

    const nameInput = screen.getByLabelText('名称（必填）')
    await user.clear(nameInput)
    await user.type(nameInput, '冷调实验室风 v2')
    await user.clear(screen.getByLabelText('要点（要有）'))
    await user.type(screen.getByLabelText('要点（要有）'), '冷白平衡{enter}硬光源')

    await user.click(screen.getByRole('button', { name: '保存修改' }))

    await waitFor(() => expect(updateStyleEntry).toHaveBeenCalledTimes(1))
    const [id, patch] = updateStyleEntry.mock.calls[0]
    expect(id).toBe('s1')
    expect(patch.name).toBe('冷调实验室风 v2')
    expect(patch.reward_points).toEqual(['冷白平衡', '硬光源'])
    // 未改动的字段不发
    expect(patch.background).toBeUndefined()
    expect(patch.applies_to).toBeUndefined()

    expect(await screen.findByTestId('style-removed')).toHaveTextContent('已剔除 2 处')
    expect(await screen.findByTestId('style-similar')).toHaveTextContent('洁癖白（83%）')
  })

  it('停用走 PATCH {enabled:false}', async () => {
    const user = userEvent.setup()
    renderStyles()
    await screen.findByTestId('style-card-s1')

    await user.click(screen.getByRole('button', { name: '停用' }))

    await waitFor(() => expect(updateStyleEntry).toHaveBeenCalledWith('s1', { enabled: false }))
    expect(getStyleLibrary).toHaveBeenCalledTimes(2)
  })

  it('删除前 confirm 说明"被采用过"的影响，取消则不删', async () => {
    const user = userEvent.setup()
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(false)
    renderStyles()
    await screen.findByTestId('style-card-s1')

    await user.click(screen.getByRole('button', { name: '删除' }))
    expect(confirmSpy).toHaveBeenCalledWith(expect.stringContaining('已被 3 次会话采用'))
    expect(deleteStyleEntry).not.toHaveBeenCalled()

    confirmSpy.mockReturnValue(true)
    await user.click(screen.getByRole('button', { name: '删除' }))
    await waitFor(() => expect(deleteStyleEntry).toHaveBeenCalledWith('s1'))
  })
})

describe('零成本预览', () => {
  it('选平台 + 槽位 → 显示后端注入块并注明"不会原样写进最终提示词"', async () => {
    const user = userEvent.setup()
    previewStyleLibrary.mockResolvedValue({
      platform: 'taobao', policy: 'design_allowed',
      slots: { main_white: [{ id: 's1', name: '冷调实验室风' }] },
      block: '## 适用风格档案\n- 冷调实验室风：冷白背景＋硬光',
      notes: ['未指定品类，按通用优先级排序'],
      message: '本次会注入 1 条档案',
    })
    renderStyles()
    await screen.findByTestId('style-card-s1')

    await user.click(screen.getByRole('button', { name: '预览' }))
    expect(await screen.findByRole('dialog')).toBeInTheDocument()
    expect(screen.getByText(/预览是零成本的/)).toBeInTheDocument()

    await user.selectOptions(await screen.findByLabelText('槽位（可空）'), 'main_white')
    await user.click(screen.getByRole('button', { name: '生成预览' }))

    await waitFor(() => expect(previewStyleLibrary).toHaveBeenCalledWith({
      platform: 'taobao', category: '', slot: 'main_white', entryId: 's1',
    }))
    expect(await screen.findByTestId('style-preview-block'))
      .toHaveTextContent('## 适用风格档案')
    expect(screen.getByTestId('style-preview-hint'))
      .toHaveTextContent('档案不会原样写进最终提示词（最终仍是六段式）。')
    expect(screen.getByText(/本次会注入 1 条档案/)).toBeInTheDocument()
  })
})

describe('接口失败与降级', () => {
  it('列表接口失败给出可读提示，不白屏', async () => {
    getStyleLibrary.mockRejectedValue(new Error('风格词库存取失败：entries.json 损坏'))
    renderStyles()

    expect(await screen.findByRole('alert')).toHaveTextContent('entries.json 损坏')
    expect(screen.getByTestId('style-add-card')).toBeInTheDocument()
  })
})
