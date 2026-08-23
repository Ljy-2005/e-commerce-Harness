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
    expect(imageModels).toEqual(['openai/dall-e-3', 'seedream/seedream-5.0', 'flux/flux.1-dev'])
    expect(imageModels.every(m => /^(seedream\/|flux\/|openai\/dall-e)/.test(m))).toBe(true)
    expect(llmModels).toContain('deepseek/deepseek-v4-flash')
    expect(llmModels).not.toContain('seedream/seedream-5.0')
    expect(llmModels).not.toContain('openai/dall-e-3')
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
