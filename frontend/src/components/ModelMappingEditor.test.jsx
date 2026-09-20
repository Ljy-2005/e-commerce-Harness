import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import ModelMappingEditor from './ModelMappingEditor'

vi.mock('../api', () => ({ saveModels: vi.fn() }))
import { saveModels } from '../api'

const SETTINGS = {
  models_config: {
    capabilities: {
      vision: { default: 'deepseek/deepseek-v4-flash-vision-exp', alternatives: ['anthropic/claude-sonnet-4-20250514'], fallback: ['mock'] },
      text: { default: 'deepseek/deepseek-v4-flash', alternatives: ['openai/gpt-4o'], fallback: ['mock'] },
      image: { default: 'seedream/seedream-5.0', alternatives: ['openai/dall-e-3'], fallback: ['mock'] },
    },
    agent_overrides: {},
  },
  model_catalog: {
    openai: ['gpt-4o', 'dall-e-3'],
    deepseek: ['deepseek-v4-flash', 'deepseek-v4-flash-vision-exp'],
    seedream: ['seedream-5.0'],
    flux: ['flux.1-dev'],
  },
  agents: [],
  // 只配了 DeepSeek —— 目录里其它服务商的模型仍会出现（平台目录），但必须被标注/告警
  providers: [{ name: 'deepseek', capabilities: ['text', 'vision'] }],
  provider_routes: [
    { route: 'deepseek', label: 'DeepSeek', capabilities: 'text / vision' },
    { route: 'openai', label: 'OpenAI', capabilities: 'vision / text / image',
      models_by_capability: { text: ['gpt-4o'], image: ['dall-e-3'] } },
    { route: 'seedream', label: '火山视觉智能（旧版 AK/SK 签名）', capabilities: 'image' },
    { route: 'flux', label: 'FLUX', capabilities: 'image' },
    { route: 'ark', label: '火山引擎方舟（Ark）', capabilities: 'text / vision / image',
      models_by_capability: { text: ['doubao-seed-2-1-pro-260628'],
                              image: ['doubao-seedream-5-0-260128'] } },
  ],
}

beforeEach(() => vi.clearAllMocks())

describe('ModelMappingEditor 渲染', () => {
  it('渲染三种能力行', () => {
    render(<ModelMappingEditor settings={SETTINGS} />)
    expect(screen.getByText('视觉（vision）')).toBeInTheDocument()
    expect(screen.getByText('文本（text）')).toBeInTheDocument()
    expect(screen.getByText('生图（image）')).toBeInTheDocument()
  })

  it('生图能力输入框只用生图模型建议列表（防 DeepSeek 误配）', () => {
    render(<ModelMappingEditor settings={SETTINGS} />)
    const rows = screen.getAllByRole('row')
    const imageRow = rows.find(r => r.textContent.includes('生图（image）'))
    const inputs = imageRow.querySelectorAll('input[list]')
    expect(inputs.length).toBe(3)
    for (const inp of inputs) {
      expect(inp.getAttribute('list')).toBe('model-suggestions-image')
    }
    const textRow = rows.find(r => r.textContent.includes('文本（text）'))
    expect(textRow.querySelector('input[list]').getAttribute('list')).toBe('model-suggestions-llm')
  })

  it('image datalist 只含生图模型，llm datalist 排除生图模型', () => {
    render(<ModelMappingEditor settings={SETTINGS} />)
    const imageList = document.getElementById('model-suggestions-image')
    const llmList = document.getElementById('model-suggestions-llm')
    const imageModels = [...imageList.querySelectorAll('option')].map(o => o.value)
    const llmModels = [...llmList.querySelectorAll('option')].map(o => o.value)
    // 生图判定按**路由能力**推导（deepseek 无 image 能力 → 不进生图列表）
    expect(imageModels).toEqual(['openai/dall-e-3', 'seedream/seedream-5.0', 'flux/flux.1-dev'])
    expect(llmModels).toContain('deepseek/deepseek-v4-flash')
    expect(llmModels).not.toContain('seedream/seedream-5.0')
    expect(llmModels).not.toContain('openai/dall-e-3')
  })

  it('可用性说明：列出已配置与未配置的服务商', () => {
    render(<ModelMappingEditor settings={SETTINGS} />)

    const hint = screen.getByText(/可选模型来自平台目录/).closest('div')
    expect(hint).toHaveTextContent('DeepSeek')          // 可用
    expect(hint).toHaveTextContent('OpenAI')            // 未配置
    expect(hint).toHaveTextContent('火山视觉智能')       // 未配置（旧版路由）
    expect(hint).toHaveTextContent('回落到 Mock')
  })

  it('映射指向未配置服务商时给出告警（实测踩过：生图指向未配置的 seedream）', () => {
    render(<ModelMappingEditor settings={SETTINGS} />)

    const alert = screen.getByRole('alert')
    expect(alert).toHaveTextContent('未配置')
    expect(alert).toHaveTextContent('生图（image） → seedream/seedream-5.0')
    expect(alert).toHaveTextContent('回落到 Mock')
  })

  it('建议把已配置服务商的模型排在前面，并标注可用性', () => {
    render(<ModelMappingEditor settings={SETTINGS} />)
    const llmOptions = [...document.getElementById('model-suggestions-llm').querySelectorAll('option')]

    expect(llmOptions[0].value.startsWith('deepseek/')).toBe(true)
    expect(llmOptions[0].getAttribute('label')).toBe('已配置')
    expect(llmOptions.find(o => o.value === 'openai/gpt-4o').getAttribute('label')).toContain('未配置')
  })

  it('自定义生图服务商的模型也会进生图建议（按能力推导而非写死前缀）', () => {
    const withCustom = {
      ...SETTINGS,
      provider_routes: [...SETTINGS.provider_routes,
        { route: 'myimg', label: '自建生图', capabilities: 'image' }],
      model_catalog: { ...SETTINGS.model_catalog, myimg: ['my-image-v2'] },
      providers: [...SETTINGS.providers, { name: 'myimg', capabilities: ['image'] }],
    }
    render(<ModelMappingEditor settings={withCustom} />)

    const imageModels = [...document.getElementById('model-suggestions-image')
      .querySelectorAll('option')].map(o => o.value)
    expect(imageModels).toContain('myimg/my-image-v2')
  })

  it('无 Agent 覆盖时显示空态', () => {
    render(<ModelMappingEditor settings={SETTINGS} />)
    expect(screen.getByText(/暂无覆盖配置/)).toBeInTheDocument()
  })

  it('已有覆盖配置时渲染行', () => {
    const withOverride = {
      ...SETTINGS,
      agents: [{ name: '提示词生成员' }],
      models_config: { ...SETTINGS.models_config, agent_overrides: { 提示词生成员: { text: 'deepseek/deepseek-v4-flash' } } },
    }
    render(<ModelMappingEditor settings={withOverride} />)
    expect(screen.getByText('提示词生成员')).toBeInTheDocument()
    // 覆盖行的模型输入 + text 能力默认值都是 deepseek-v4-flash（能力表 + 覆盖行两处）
    expect(screen.getAllByDisplayValue('deepseek/deepseek-v4-flash').length).toBeGreaterThanOrEqual(2)
  })
})

describe('ModelMappingEditor 保存', () => {
  it('保存提交解析后的 capabilities 与 agent_overrides', async () => {
    const user = userEvent.setup()
    saveModels.mockResolvedValue({})
    render(<ModelMappingEditor settings={SETTINGS} />)
    await user.click(screen.getByRole('button', { name: '保存模型映射' }))
    await waitFor(() => expect(saveModels).toHaveBeenCalledTimes(1))
    const [payload] = saveModels.mock.calls[0]
    expect(payload.capabilities.image.default).toBe('seedream/seedream-5.0')
    expect(payload.capabilities.image.alternatives).toEqual(['openai/dall-e-3'])
    expect(payload.capabilities.vision.default).toBe('deepseek/deepseek-v4-flash-vision-exp')
    expect(payload.agent_overrides).toEqual({})
  })

  it('保存失败显示错误信息', async () => {
    const user = userEvent.setup()
    saveModels.mockRejectedValue(new Error('未知模型'))
    render(<ModelMappingEditor settings={SETTINGS} />)
    await user.click(screen.getByRole('button', { name: '保存模型映射' }))
    expect(await screen.findByText(/未知模型/)).toBeInTheDocument()
  })
})


describe('A41 Agent 覆盖的能力校验', () => {
  const WITH_AGENTS = {
    ...SETTINGS,
    agents: [
      { name: '审查员', requires: ['vision'] },
      { name: '提示词生成员', requires: ['text'] },
    ],
  }

  it('能力下拉只给出该 Agent 的 requires，并自动纠正为 required 能力', async () => {
    const user = userEvent.setup()
    render(<ModelMappingEditor settings={WITH_AGENTS} />)

    await user.click(screen.getByText('+ 添加 Agent 覆盖'))
    const agentSelect = screen.getByDisplayValue('选择 Agent')
    await user.selectOptions(agentSelect, '审查员')

    // 选中 Agent 后能力被纠到 vision（否则覆盖键会被后端静默忽略）
    const capSelect = screen.getByDisplayValue('vision')
    const options = Array.from(capSelect.querySelectorAll('option')).map(o => o.value)
    expect(options).toEqual(['vision'])
  })

  it('未选 Agent 时给出全部能力（不锁死新增行）', async () => {
    const user = userEvent.setup()
    render(<ModelMappingEditor settings={WITH_AGENTS} />)

    await user.click(screen.getByText('+ 添加 Agent 覆盖'))

    const capSelect = screen.getByDisplayValue('text')
    const options = Array.from(capSelect.querySelectorAll('option')).map(o => o.value)
    expect(options).toContain('text')
    expect(options).toContain('image')
  })

  it('后端报出的失效覆盖会显示告警', () => {
    render(<ModelMappingEditor settings={{
      ...SETTINGS,
      agent_overrides_issues: [
        { agent: '审查员', capability: 'text', requires: ['vision'],
          reason: '该 Agent 需要 vision，"text"覆盖不会生效' },
      ],
    }} />)

    expect(screen.getAllByText(/不会生效/).length).toBeGreaterThan(0)
  })

  it('已有失效覆盖的行标"不生效"', () => {
    render(<ModelMappingEditor settings={{
      ...WITH_AGENTS,
      models_config: {
        ...SETTINGS.models_config,
        agent_overrides: { '审查员': { text: 'deepseek/deepseek-v4-flash' } },
      },
    }} />)

    expect(screen.getByText('不生效')).toBeInTheDocument()
  })
})
