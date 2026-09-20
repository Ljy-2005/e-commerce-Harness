import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Routes, Route } from 'react-router-dom'
import Session from './Session'

vi.mock('../api', () => ({
  getSession: vi.fn(),
  deleteSession: vi.fn(),
  downloadSessionImage: vi.fn(),
  exportSessionImages: vi.fn(),
  saveSessionFacts: vi.fn(),
  interjectSession: vi.fn(),
}))
// 群聊/审批/A-B 面板与本用例无关（各自有专测），避免 WS 与额外网络依赖
vi.mock('../components/ChatPanel', () => ({ default: () => <div data-testid="chat-panel" /> }))
vi.mock('../components/HITLPanel', () => ({ default: () => <div /> }))
vi.mock('../components/ABTestPanel', () => ({ default: () => <div /> }))
vi.mock('../components/ReviewChart', () => ({ default: () => <div /> }))

import { getSession, downloadSessionImage, exportSessionImages,
         saveSessionFacts, interjectSession } from '../api'

const BASE_SESSION = {
  session_id: 'abcdef1234567890',
  status: 'failed',
  turn_count: 3,
  cost_so_far: 0.0012,
  messages: [],
  artifacts: {},
  task: { product_info: '测试商品' },
  error_history: [],
}

function renderSession() {
  return render(
    <MemoryRouter initialEntries={['/session/abcdef1234567890']}>
      <Routes>
        <Route path="/session/:id" element={<Session />} />
      </Routes>
    </MemoryRouter>,
  )
}

beforeEach(() => {
  vi.clearAllMocks()
  URL.createObjectURL = vi.fn(() => 'blob:mock')
  URL.revokeObjectURL = vi.fn()
  vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})
})

describe('生成图导出（用户反馈：没法设置导出路径）', () => {
  const IMAGES = [
    { prompt_name: 'variant_1', model_used: 'mock/svg-placeholder', base64_data: 'AAA',
      saved_path: 'default/sess1/taobao_保健品_1.svg' },
    { prompt_name: 'variant_2', model_used: 'seedream', image_url: 'https://cdn.example/2.png',
      saved_path: 'default/sess1/taobao_保健品_2.png' },
  ]

  it('图片 tab 显示落盘路径与下载按钮', async () => {
    getSession.mockResolvedValue({
      ...BASE_SESSION, status: 'completed',
      artifacts: { images: IMAGES },
    })
    const user = userEvent.setup()
    renderSession()

    await user.click(await screen.findByRole('button', { name: /生成图片/ }))

    expect(screen.getByText('default/sess1/taobao_保健品_1.svg')).toBeInTheDocument()
    expect(screen.getByText('default/sess1/taobao_保健品_2.png')).toBeInTheDocument()
    expect(screen.getAllByRole('button', { name: '下载' })).toHaveLength(2)
  })

  it('单张下载按序号请求并触发浏览器下载', async () => {
    getSession.mockResolvedValue({ ...BASE_SESSION, status: 'completed', artifacts: { images: IMAGES } })
    downloadSessionImage.mockResolvedValue(new Blob(['x']))
    const user = userEvent.setup()
    renderSession()

    await user.click(await screen.findByRole('button', { name: /生成图片/ }))
    await user.click(screen.getAllByRole('button', { name: '下载' })[1])

    expect(downloadSessionImage).toHaveBeenCalledWith('abcdef1234567890', 2)
    expect(URL.createObjectURL).toHaveBeenCalled()
  })

  it('导出全部 → 调 ZIP 导出并提示', async () => {
    getSession.mockResolvedValue({ ...BASE_SESSION, status: 'completed', artifacts: { images: IMAGES } })
    exportSessionImages.mockResolvedValue(new Blob(['zip']))
    const user = userEvent.setup()
    renderSession()

    await user.click(await screen.findByRole('button', { name: /生成图片/ }))
    await user.click(screen.getByRole('button', { name: /导出全部/ }))

    expect(exportSessionImages).toHaveBeenCalledWith('abcdef1234567890')
    expect(await screen.findByText(/已导出 ZIP/)).toBeInTheDocument()
  })

  it('下载失败给出可读提示（不静默）', async () => {
    getSession.mockResolvedValue({ ...BASE_SESSION, status: 'completed', artifacts: { images: IMAGES } })
    downloadSessionImage.mockRejectedValue(new Error('会话的第 1 张图尚未落盘'))
    const user = userEvent.setup()
    renderSession()

    await user.click(await screen.findByRole('button', { name: /生成图片/ }))
    await user.click(screen.getAllByRole('button', { name: '下载' })[0])

    expect(await screen.findByText(/尚未落盘/)).toBeInTheDocument()
  })
})

describe('会话失败原因（B3-25）', () => {
  it('failed 会话展示 error_history 与审计深链', async () => {
    getSession.mockResolvedValue({
      ...BASE_SESSION,
      error_history: [
        { agent: '提示词生成员', kind: 'agent', turn: 2, timestamp: 't',
          error: 'DeepSeek API error: 401 @ https://api.deepseek.com/v1 — invalid api key' },
      ],
    })
    renderSession()

    expect(await screen.findByText('❌ 会话失败原因')).toBeInTheDocument()
    expect(screen.getByText('Agent 报错')).toBeInTheDocument()
    expect(screen.getByText(/401 @ https:\/\/api\.deepseek\.com/)).toBeInTheDocument()
    const link = screen.getByRole('link', { name: /查看该会话的审计日志/ })
    expect(link).toHaveAttribute('href', '/audit?session=abcdef1234567890')
  })

  it('failed 但无历史时给出可解释的说明（而非空白）', async () => {
    getSession.mockResolvedValue({ ...BASE_SESSION, error_history: [] })
    renderSession()

    expect(await screen.findByText(/未记录具体原因/)).toBeInTheDocument()
  })

  it('completed 会话不显示失败卡片（回归）', async () => {
    getSession.mockResolvedValue({ ...BASE_SESSION, status: 'completed' })
    renderSession()

    await screen.findByText(/会话/)
    expect(screen.queryByText('❌ 会话失败原因')).not.toBeInTheDocument()
  })
})


describe('A42 失败可见性', () => {
  it('没有图像数据的图格给出原因提示并禁用下载', async () => {
    const user = userEvent.setup()
    getSession.mockResolvedValue({
      ...BASE_SESSION, status: 'failed',
      artifacts: {
        images: [
          { prompt_name: 'variant_1', model_used: 'ark', image_url: '', base64_data: '',
            processing_status: 'raw' },
        ],
      },
    })
    renderSession()

    await user.click(await screen.findByRole('button', { name: /生成图片/ }))

    expect(await screen.findByText(/没有图像数据/)).toBeInTheDocument()
    const download = screen.getByRole('button', { name: '下载' })
    expect(download.disabled).toBe(true)
    expect(screen.getByText('生成失败')).toBeInTheDocument()
  })

  it('审查未评分时不显示 None，而是"未给出分数"', async () => {
    const user = userEvent.setup()
    getSession.mockResolvedValue({
      ...BASE_SESSION, status: 'failed',
      artifacts: {
        review: { overall_score: null, verdict: 'retry', needs_human_review: true,
                  review_blocked_reason: 'no_image_accessible',
                  dimension_scores: { texture: null }, top_issues: [] },
      },
    })
    renderSession()

    await user.click(await screen.findByRole('button', { name: /审查评分/ }))

    expect(await screen.findByText('未给出分数')).toBeInTheDocument()
    expect(screen.getByText(/判定 retry/)).toBeInTheDocument()
    expect(screen.queryByText(/None/)).not.toBeInTheDocument()
    expect(screen.getByText('—')).toBeInTheDocument()
  })

  it('只有文本的审查结果也会渲染出来（此前整页空白）', async () => {
    const user = userEvent.setup()
    getSession.mockResolvedValue({
      ...BASE_SESSION, status: 'failed',
      artifacts: { review: { text: '本次审查未收到任何可访问的生成图片' } },
    })
    renderSession()

    await user.click(await screen.findByRole('button', { name: /审查评分/ }))

    expect(await screen.findByText(/未收到任何可访问的生成图片/)).toBeInTheDocument()
    expect(screen.getByText('判定缺失')).toBeInTheDocument()
  })

  describe('商品身份 / 套图 / 体检（用户三点反馈的界面闭环）', () => {
    async function openOutputs(artifacts) {
      getSession.mockResolvedValue({ ...BASE_SESSION, status: 'completed', artifacts })
      const user = userEvent.setup()
      renderSession()
      await user.click(await screen.findByRole('button', { name: /产出物/ }))
      return user
    }

    it('已确认身份：醒目展示品牌/品名/规格', async () => {
      await openOutputs({
        product_identity: {
          status: 'confirmed', source: 'vision', brand: 'DEFOEBUENA®',
          product_name: '金裝強力肝迅康', spec: "60's",
          certifications: ['德國 GMP 優質產品'], missing: [],
          visible_text: { lines: [{ text: '金裝強力肝迅康' }] },
        },
      })
      expect(await screen.findByText('🏷️ 商品身份')).toBeInTheDocument()
      expect(screen.getByText('DEFOEBUENA®')).toBeInTheDocument()
      expect(screen.getByText('金裝強力肝迅康')).toBeInTheDocument()
      expect(screen.getByText('已确认')).toBeInTheDocument()
    })

    it('未确认身份：告警并说明文字会虚化', async () => {
      await openOutputs({
        product_identity: { status: 'uncertain', source: 'vision', brand: '',
                            product_name: '', missing: ['品牌', '商品名'] },
      })
      expect(await screen.findByText('⚠️ 商品身份未确认')).toBeInTheDocument()
      expect(screen.getByText(/交设计师后期贴图/)).toBeInTheDocument()
    })

    it('套图编排展示槽位与完成度', async () => {
      await openOutputs({
        set_plan: { platform: 'pinduoduo', platform_label: '拼多多',
                    slots: [{ slot_id: 'main_white', role: '白底主图' },
                            { slot_id: 'main_scene', role: '使用场景' }] },
        set_plan_coverage: { expected: 2, produced: 1, done_slots: ['main_white'],
                             missing_slots: ['main_scene'], complete: false },
      })
      expect(await screen.findByText('🧩 套图编排')).toBeInTheDocument()
      expect(screen.getByText(/main_white（白底主图）/)).toBeInTheDocument()
      expect(screen.getByText(/缺 1\/2/)).toBeInTheDocument()
      expect(screen.getByText(/缺槽位：main_scene/)).toBeInTheDocument()
    })

    it('本地体检展示白底/水印/身份相似度结论', async () => {
      await openOutputs({
        quality_report: { count: 3, white_bg_ok: false, watermark_free: false,
                          identity_lost: ['main_white'], near_copy: [],
                          issues: ['main_white：背景不是纯白：边缘平均亮度 238 < 250'] },
      })
      expect(await screen.findByText('🔬 本地体检')).toBeInTheDocument()
      expect(screen.getByText(/商品身份相似度过低/)).toBeInTheDocument()
      expect(screen.getByText(/背景不是纯白/)).toBeInTheDocument()
    })

    it('图片卡显示槽位徽章与文+图参数', async () => {
      getSession.mockResolvedValue({
        ...BASE_SESSION, status: 'completed',
        artifacts: { images: [{
          prompt_name: 'main_white', slot_id: 'main_white', image_url: 'https://cdn/1.png',
          processing_status: 'raw',
          generation_params: { reference_count: 1, text_strategy: 'preserve' },
        }] },
      })
      const user = userEvent.setup()
      renderSession()
      await user.click(await screen.findByRole('button', { name: /生成图片/ }))

      expect(await screen.findByText('槽位 main_white')).toBeInTheDocument()
      expect(screen.getByText(/文\+图：参考图 × 1/)).toBeInTheDocument()
    })

    it('信息图显示"图文已排版"与图上实际画了什么字', async () => {
      getSession.mockResolvedValue({
        ...BASE_SESSION, status: 'completed',
        artifacts: { images: [{
          prompt_name: 'main_selling_point', slot_id: 'main_selling_point',
          image_url: 'data:image/jpeg;base64,AAA', processing_status: 'info_composed',
          text_status: 'composed', compose: { ok: true, layout: 'top_title_bullets', items: 2 },
          slot_copy: { title: '金裝強力肝迅康', items: ['德國來源標示', 'GMP 認證'],
                       footer: "60's ｜ 德國GMP優質產品", sources: ['selling_points'] },
        }] },
      })
      const user = userEvent.setup()
      renderSession()
      await user.click(await screen.findByRole('button', { name: /生成图片/ }))

      expect(await screen.findByText('图文已排版')).toBeInTheDocument()
      expect(screen.getByText(/top_title_bullets/)).toBeInTheDocument()
      expect(screen.getByText('· 德國來源標示')).toBeInTheDocument()
      expect(screen.getByText('· GMP 認證')).toBeInTheDocument()
    })

    it('缺素材的信息槽位显示原因（不让用户以为图生成好了）', async () => {
      getSession.mockResolvedValue({
        ...BASE_SESSION, status: 'completed',
        artifacts: {
          images: [{
            prompt_name: 'main_ingredients', slot_id: 'main_ingredients',
            base64_data: 'AAA', processing_status: 'blocked: 需要成分表',
            text_status: 'blocked',
            text_reason: '包装正面看不到成分表：请上传包装背面/成分表照片，或手工填写成分',
            slot_copy: { title: '', items: [], footer: '', reason: '需要成分表' },
          }],
          set_plan: { platform: 'pinduoduo', platform_label: '拼多多',
                      slots: [{ slot_id: 'main_ingredients', role: '成分配方图', kind: 'info' }] },
          set_plan_coverage: { expected: 1, produced: 0, done_slots: [], missing_slots: [],
                               blocked_slots: ['main_ingredients'], complete: false },
        },
      })
      const user = userEvent.setup()
      renderSession()
      await user.click(await screen.findByRole('button', { name: /生成图片/ }))
      expect(await screen.findByText('缺素材·未生成')).toBeInTheDocument()
      expect(screen.getByText(/请上传包装背面/)).toBeInTheDocument()
    })

    it('缺素材时可就地补充事实并通知重新出图（不编造：填了才画）', async () => {
      getSession.mockResolvedValue({
        ...BASE_SESSION, status: 'completed',
        artifacts: { images: [{
          prompt_name: 'main_ingredients', slot_id: 'main_ingredients',
          base64_data: 'AAA', processing_status: 'blocked: 需要成分表',
          text_status: 'blocked', text_reason: '包装正面看不到成分表：请上传包装背面/成分表照片',
          slot_copy: { title: '', items: [], footer: '', reason: '需要成分表' },
        }] },
      })
      saveSessionFacts.mockResolvedValue({ session_id: 'abcdef1234567890' })
      interjectSession.mockResolvedValue({ ok: true })
      const user = userEvent.setup()
      renderSession()
      await user.click(await screen.findByRole('button', { name: /生成图片/ }))

      expect(await screen.findByText('✍️ 补充素材')).toBeInTheDocument()
      await user.type(screen.getByLabelText('补充 ingredients'), '水飛薊提取物{enter}姜黃素')
      await user.click(screen.getByLabelText('保存素材并重新出图'))

      await waitFor(() => expect(saveSessionFacts).toHaveBeenCalledWith(
        'abcdef1234567890', { ingredients: ['水飛薊提取物', '姜黃素'] }))
      await waitFor(() => expect(interjectSession).toHaveBeenCalled())
      expect(await screen.findByText(/已补充并通知协调者重新出图/)).toBeInTheDocument()
    })

    it('补充表单为空时不发请求', async () => {
      getSession.mockResolvedValue({
        ...BASE_SESSION, status: 'completed',
        artifacts: { images: [{
          prompt_name: 'main_usage', slot_id: 'main_usage', base64_data: 'AAA',
          text_status: 'blocked', text_reason: '包装上未见食用方法',
          slot_copy: { title: '', items: [], footer: '' },
        }] },
      })
      const user = userEvent.setup()
      renderSession()
      await user.click(await screen.findByRole('button', { name: /生成图片/ }))
      await user.click(await screen.findByLabelText('保存素材并重新出图'))

      expect(await screen.findByText('请至少填写一项')).toBeInTheDocument()
      expect(saveSessionFacts).not.toHaveBeenCalled()
    })
  })

  // 用户反馈（2026-09-20）："agent 的对话里面显示的会很直白，会给出代码原文，
  // 实际上我们只要文字的内容显示" —— 会话页同样不再把 JSON 摊给用户
  describe('提示词以人话展示（不再摊 JSON）', () => {
    it('产出物页把逐张提示词列成可读列表', async () => {
      getSession.mockResolvedValue({
        ...BASE_SESSION, status: 'completed',
        artifacts: {
          prompts: {
            platform: 'pinduoduo',
            message: '🎨 审美审核后定稿（4 张被改写）',
            main_image: { prompt: '纯白背景无缝，主体居中' },
            prompt_plan: [
              { number: 1, slot_id: 'main_white', role: '纯商品图', kind: 'photo',
                intent: '建立正规品牌的第一印象', prompt: '纯白 #FFFFFF 无缝底，主体居中占高 88%' },
              { number: 2, slot_id: 'main_ingredients', role: '成分配方图', kind: 'info',
                intent: '让买家看清配方', revised_by_reviewer: true,
                prompt: '上半部留白 40% 供成分条目，商品压右下角' },
            ],
          },
        },
      })
      const user = userEvent.setup()
      renderSession()
      await user.click(await screen.findByRole('button', { name: /产出物/ }))

      expect(await screen.findByText('本套逐张提示词（2 张）')).toBeInTheDocument()
      expect(screen.getByText(/第1张｜纯商品图/)).toBeInTheDocument()
      expect(screen.getByText(/想要：让买家看清配方/)).toBeInTheDocument()
      expect(screen.getAllByText('查看画面描述').length).toBe(2)
      // 原始字段键名不该出现在正文里
      expect(screen.queryByText(/"prompt_plan"/)).not.toBeInTheDocument()
    })

    it('展开详情字段后也看不到签名地址与内部路径', async () => {
      const signed = 'https://ark.tos-cn-beijing.volces.com/a.jpeg?X-Tos-Signature=deadbeef'
      getSession.mockResolvedValue({
        ...BASE_SESSION, status: 'completed',
        artifacts: { prompts: {
          message: '提示词已定稿',
          style_refs: { entries: [], slots: {}, anchors: [], notes: [], message: '🎨 采用风格档案' },
          image_url: signed,
          saved_path: 'default/abcdef1234567890/pinduoduo_main_white_1.jpg',
        } },
      })
      const user = userEvent.setup()
      renderSession()
      await user.click(await screen.findByRole('button', { name: /产出物/ }))

      expect(await screen.findByText('✍️ 提示词方案')).toBeInTheDocument()
      await user.click(screen.getByRole('button', { name: /其余提示词内容/ }))
      const text = document.body.textContent
      expect(text).not.toContain('X-Tos-Signature')
      expect(text).not.toContain('deadbeef')
      expect(text).not.toContain('default/abcdef1234567890/')
    })
  })

  // 用户反馈（2026-09-18）："未有明确约束每一张该有的提示词"——每张图要能核对
  // "这是第几张、提示词怎么写的、有没有被审美改写"
  describe('逐张提示词可见', () => {
    it('图片卡显示第N张、改写标记与完整提示词', async () => {
      getSession.mockResolvedValue({
        ...BASE_SESSION, status: 'completed',
        artifacts: { images: [{
          prompt_name: 'main_white', slot_id: 'main_white', prompt_number: 1,
          image_url: 'https://cdn/1.png', revised_by_reviewer: true,
          prompt_text: '第1张｜纯商品图（main_white）｜主图｜1:1\n【画面】纯白背景无缝…',
          prompt_sections: { must: ['纯白背景（#FFFFFF）无缝'], keep_clear: '' },
          prompt_notes: ['已从画面描述移除身份文字：DEFOEBUENA'],
        }] },
      })
      const user = userEvent.setup()
      renderSession()
      await user.click(await screen.findByRole('button', { name: /生成图片/ }))

      expect(await screen.findByText('第1张')).toBeInTheDocument()
      expect(screen.getByText('已按审美改写')).toBeInTheDocument()
      expect(screen.getByText('查看提示词')).toBeInTheDocument()
      expect(screen.getByText(/纯白背景无缝/)).toBeInTheDocument()
      expect(screen.getByText(/已从画面描述移除身份文字/)).toBeInTheDocument()
    })
  })

  describe('提示词审核卡片', () => {
    it('展示逐张审美分、被改写的槽位与体检结论', async () => {
      getSession.mockResolvedValue({
        ...BASE_SESSION, status: 'completed',
        artifacts: {
          prompt_lint: {
            checked: 2, expected: 2, errors: [], warnings: ['槽位 A 与 B 过于相似'],
            findings: [{ rule: 'duplicate_prompts', level: 'warning', number: 2,
                         message: '槽位 B 与 A 的画面描述过于相似（0.8）' }],
          },
          prompt_review: {
            status: 'reviewed', verdict: 'revised', threshold: 85,
            scores: { main_white: 60, main_scene: 92 },
            revised_slots: ['main_white'],
            refine_rejected: [],
            message: '🎨 提示词审核（2 张）：审美分 平均 76.0；已改写 1 张',
          },
        },
      })
      const user = userEvent.setup()
      renderSession()
      await user.click(await screen.findByRole('button', { name: /产出物/ }))

      expect(await screen.findByRole('heading', { name: /提示词审核/ })).toBeInTheDocument()
      expect(screen.getByText('审美阈值 85')).toBeInTheDocument()
      expect(screen.getByText('main_white 60')).toBeInTheDocument()
      expect(screen.getByText('main_scene 92')).toBeInTheDocument()
      expect(screen.getByText(/已按审美改写/)).toBeInTheDocument()
      await user.click(screen.getByText(/查看 1 条体检结论/))
      expect(screen.getByText(/画面描述过于相似/)).toBeInTheDocument()
    })
  })

  // 风格词库（用户："你亲自挑的风格会作为审美基准"）——产出物里要能核对本次采用了哪条
  describe('采用风格词条/原型', () => {
    const STYLE_REFS = {
      enabled: true,
      entries: [{ id: 's1', name: '冷调实验室风', source: 'user' }],
      slots: { main_white: [{ id: 's1', name: '冷调实验室风', source: 'user' }] },
      anchors: [{ id: 's1', name: '冷调实验室风' }],
      notes: [],
      message: '🎨 采用风格档案：第1张=冷调实验室风；锚点：「冷调实验室风」',
    }

    it('有 artifacts.prompts.style_refs 时显示采用行', async () => {
      getSession.mockResolvedValue({
        ...BASE_SESSION, status: 'completed',
        artifacts: {
          prompt_lint: { errors: [], warnings: [], findings: [] },
          prompts: { style_refs: STYLE_REFS },
        },
      })
      const user = userEvent.setup()
      renderSession()
      await user.click(await screen.findByRole('button', { name: /产出物/ }))

      const row = await screen.findByTestId('session-style-refs')
      expect(row).toHaveTextContent('🎨 采用风格词条/原型')
      expect(row).toHaveTextContent('第1张=冷调实验室风')
    })

    it('没有这个字段就整行不显示', async () => {
      getSession.mockResolvedValue({
        ...BASE_SESSION, status: 'completed',
        artifacts: {
          prompt_lint: { errors: [], warnings: [], findings: [] },
          prompts: { main_image: { prompt: '纯白背景' } },
        },
      })
      const user = userEvent.setup()
      renderSession()
      await user.click(await screen.findByRole('button', { name: /产出物/ }))

      expect(await screen.findByRole('heading', { name: /提示词审核/ })).toBeInTheDocument()
      expect(screen.queryByTestId('session-style-refs')).not.toBeInTheDocument()
    })

    it('会话锁：显示"本轮已固定"与「换风格」按钮，并展示覆盖差', async () => {
      getSession.mockResolvedValue({
        ...BASE_SESSION, status: 'completed',
        artifacts: {
          prompt_lint: { errors: [], warnings: [], findings: [] },
          prompts: {
            style_refs: {
              ...STYLE_REFS,
              locked_entry_id: 's1',
              active_entry: { id: 's1', name: '冷调实验室风' },
              coverage: [{ entry_id: 's1', name: '冷调实验室风', ref_count: 6,
                           matched: [{ slot: 'main_white', ref_number: 1 }],
                           missing_in_ref: ['main_spec', 'main_usage'], extra_in_ref: [] }],
            },
          },
        },
      })
      const user = userEvent.setup()
      renderSession()
      await user.click(await screen.findByRole('button', { name: /产出物/ }))

      expect(await screen.findByTestId('session-style-locked')).toHaveTextContent('本轮已固定')
      expect(screen.getByRole('button', { name: '换风格' })).toBeInTheDocument()
      expect(await screen.findByTestId('session-style-coverage'))
        .toHaveTextContent('本平台还有 2 张没有对应角色')
    })
  })
})
