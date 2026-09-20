import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import Settings from './Settings'

vi.mock('../api', () => ({
  getSettings: vi.fn(),
  saveApiKeys: vi.fn(),
  saveTenantKey: vi.fn(),
  saveProviderConfig: vi.fn(),
  testProviderConnection: vi.fn(),
  saveOutputDir: vi.fn(),
  saveChatSettings: vi.fn(),
  saveImageSettings: vi.fn(),
  getProviderPresets: vi.fn(),
  addCustomProvider: vi.fn(),
  deleteCustomProvider: vi.fn(),
  getStoredApiKey: vi.fn(() => ''),
  setStoredApiKey: vi.fn(),
  // 计价（A94-A96）：给一个空的价格表，避免未 mock 时组件拿 undefined 崩掉
  getPricing: vi.fn(() => Promise.resolve({ models: [], entries: [], staleness: [] })),
  savePricing: vi.fn(),
  calibratePricing: vi.fn(),
}))
import {
  getSettings, saveApiKeys, saveTenantKey, saveProviderConfig, testProviderConnection,
  saveOutputDir, getProviderPresets, addCustomProvider, deleteCustomProvider,
  setStoredApiKey, saveChatSettings, saveImageSettings,
  getPricing, savePricing, calibratePricing,
} from '../api'

const PRESETS = {
  presets: [
    { route: 'zhipu', label: '智谱 GLM', kind: 'openai', available: true, reason: '',
      base_url: 'https://open.bigmodel.cn/api/paas/v4', api_key_env: 'ZHIPU_API_KEY',
      capabilities: ['text', 'vision'], models: ['glm-4.6', 'glm-4v-plus'],
      credential_hint: '智谱开放平台 Key' },
    { route: 'ark', label: '火山引擎方舟（Ark）', kind: 'openai', available: false,
      reason: '已存在（内置或已添加）', base_url: 'https://ark.cn-beijing.volces.com/api/v3',
      api_key_env: 'ARK_API_KEY', capabilities: ['text', 'vision', 'image'], models: [] },
  ],
  existing_routes: ['ark', 'openai'],
}

const ROUTE = (route, label, capabilities, extra = {}) => ({
  route, label, capabilities,
  default_base_url: capabilities.includes('image') && !capabilities.includes('vision')
    ? '' : `https://api.${route}.example/v1`,
  base_url_supported: !(capabilities.includes('image') && !capabilities.includes('vision')),
  credential_hint: '', effective_base_url: '', base_url_custom: false, base_url_source: '',
  models: [], official_models: [], ...extra,
})

const SETTINGS = {
  mock_mode: false,
  api_keys: [
    { name: 'OpenAI', env: 'OPENAI_API_KEY', provider: 'openai', capabilities: 'vision / text / image', configured: true, available: true, source: 'file' },
    { name: 'DeepSeek', env: 'DEEPSEEK_API_KEY', provider: 'deepseek', capabilities: 'text / vision', configured: false, available: false, source: '' },
    { name: 'Anthropic', env: 'ANTHROPIC_API_KEY', provider: 'anthropic', capabilities: 'vision / text', configured: false, available: false, source: '' },
    { name: 'Seedream（即梦）', env: 'SEEDREAM_API_KEY', provider: 'seedream', capabilities: 'image', configured: true, available: false, source: 'env' },
    { name: '火山引擎 AccessKey', env: 'VOLCANO_ACCESS_KEY', provider: 'seedream', capabilities: 'image（签名）', configured: false, available: false, source: '' },
  ],
  provider_routes: [
    ROUTE('openai', 'OpenAI', 'vision / text / image', {
      effective_base_url: 'https://api.openai.example/v1',
      credential_hint: '官方 Key 或第三方 coding plan / 代理 Key',
    }),
    ROUTE('deepseek', 'DeepSeek', 'text / vision', { effective_base_url: 'https://api.deepseek.example/v1' }),
    ROUTE('anthropic', 'Anthropic', 'vision / text', { effective_base_url: 'https://api.anthropic.example/v1' }),
    ROUTE('qwen', '通义千问（Qwen）', 'vision / text', { effective_base_url: 'https://api.qwen.example/v1' }),
    ROUTE('seedream', 'Seedream（即梦）', 'image', { credential_hint: '即梦 API Key，或火山引擎 AK + SK（任选其一）' }),
  ],
  tenant_keys: [
    { tenant_id: 'tenant_a', name: 'Tenant A', tier: 'pro', configured: true, source: 'env' },
    { tenant_id: 'tenant_b', name: 'Tenant B', tier: 'free', configured: false, source: 'file' },
    { tenant_id: 'tenant_c', name: 'Tenant C', tier: 'pro', configured: true, source: 'file' },
  ],
  providers: [{ name: 'deepseek', capabilities: ['text', 'vision'] }],
  agents: [],
  models_config: { capabilities: {}, agent_overrides: {} },
  model_catalog: {},
  cors_origins: ['http://localhost:5173'],
  output: {
    dir: '', effective_dir: 'D:/proj/output', source: 'default',
    env_locked: false, writable: true, error: '',
  },
}

beforeEach(() => {
  vi.clearAllMocks()
  getSettings.mockResolvedValue(SETTINGS)
  saveApiKeys.mockResolvedValue(SETTINGS)
  saveProviderConfig.mockResolvedValue(SETTINGS)
  testProviderConnection.mockResolvedValue({ ok: true, model: 'gpt-4o', latency_ms: 120 })
  saveOutputDir.mockResolvedValue({
    ...SETTINGS,
    output: { ...SETTINGS.output, dir: 'D:/exports', effective_dir: 'D:/exports', source: 'file' },
  })
  saveTenantKey.mockResolvedValue({ tenant_keys: SETTINGS.tenant_keys })
  getProviderPresets.mockResolvedValue(PRESETS)
  addCustomProvider.mockResolvedValue(SETTINGS)
  deleteCustomProvider.mockResolvedValue(SETTINGS)
})

describe('添加模型服务商（用户反馈：无法自己添加）', () => {
  it('展开后加载预设，点预设一键填入表单', async () => {
    const user = userEvent.setup()
    render(<Settings />)
    await screen.findByText('🔑 模型服务商')

    await user.click(screen.getByRole('button', { name: /添加服务商/ }))
    await user.click(await screen.findByRole('button', { name: /智谱 GLM/ }))

    expect(screen.getByLabelText('路由 id（小写字母开头）')).toHaveValue('zhipu')
    expect(screen.getByLabelText('API 端点（Base URL）'))
      .toHaveValue('https://open.bigmodel.cn/api/paas/v4')
    expect(screen.getByLabelText('凭据环境变量名')).toHaveValue('ZHIPU_API_KEY')
    expect(screen.getByLabelText('模型 id（每行一个，可留空）'))
      .toHaveValue('glm-4.6\nglm-4v-plus')
    expect(screen.getByText(/已填入/)).toBeInTheDocument()
  })

  it('已存在的路由在预设里禁用（避免与内置重复）', async () => {
    const user = userEvent.setup()
    render(<Settings />)
    await screen.findByText('🔑 模型服务商')

    await user.click(screen.getByRole('button', { name: /添加服务商/ }))

    expect(await screen.findByRole('button', { name: /火山引擎方舟（Ark）（已存在）/ })).toBeDisabled()
  })

  it('提交调用 addCustomProvider 并提示成功', async () => {
    const user = userEvent.setup()
    render(<Settings />)
    await screen.findByText('🔑 模型服务商')
    await user.click(screen.getByRole('button', { name: /添加服务商/ }))
    await user.click(await screen.findByRole('button', { name: /智谱 GLM/ }))

    await user.click(screen.getByRole('button', { name: '确认添加服务商' }))

    expect(addCustomProvider).toHaveBeenCalledWith(expect.objectContaining({
      route: 'zhipu', label: '智谱 GLM', kind: 'openai',
      base_url: 'https://open.bigmodel.cn/api/paas/v4',
      api_key_env: 'ZHIPU_API_KEY', capabilities: ['text', 'vision'],
      models: ['glm-4.6', 'glm-4v-plus'],
    }))
    expect(await screen.findByText(/已添加/)).toBeInTheDocument()
  })

  it('校验失败时展示后端原因（不静默）', async () => {
    const user = userEvent.setup()
    addCustomProvider.mockRejectedValue(new Error('route 已存在（内置服务商，请换一个 id）'))
    render(<Settings />)
    await screen.findByText('🔑 模型服务商')
    await user.click(screen.getByRole('button', { name: /添加服务商/ }))
    await user.click(await screen.findByRole('button', { name: /智谱 GLM/ }))

    await user.click(screen.getByRole('button', { name: '确认添加服务商' }))

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('route 已存在')
  })

  it('自定义服务商在列表里带「自定义服务商」标记并可删除', async () => {
    const user = userEvent.setup()
    const customRoute = {
      route: 'zhipu', label: '智谱 GLM', capabilities: 'text / vision',
      default_base_url: 'https://open.bigmodel.cn/api/paas/v4', base_url_supported: true,
      credential_hint: '', effective_base_url: 'https://open.bigmodel.cn/api/paas/v4',
      base_url_custom: false, base_url_source: '', models: [], official_models: [],
      custom: true, kind: 'openai', api_key_env: 'ZHIPU_API_KEY',
      credentials: [{ name: '智谱 GLM', env: 'ZHIPU_API_KEY', provider: 'zhipu',
                      capabilities: 'text / vision', configured: false, available: false, source: '' }],
      configured: false, available: false,
    }
    getSettings.mockResolvedValue({
      ...SETTINGS,
      provider_routes: [...SETTINGS.provider_routes, customRoute],
      api_keys: [...SETTINGS.api_keys, ...customRoute.credentials],
    })
    render(<Settings />)
    await screen.findByText('🔑 模型服务商')

    expect(screen.getByText('自定义服务商')).toBeInTheDocument()
    await user.click(screen.getAllByRole('button', { name: /智谱 GLM/ })[0])
    await user.click(screen.getByRole('button', { name: '删除该服务商' }))
    await user.click(screen.getByRole('button', { name: '确认删除' }))

    expect(deleteCustomProvider).toHaveBeenCalledWith('zhipu')
  })
})

describe('输出目录（用户反馈：没法自己设置导出路径）', () => {
  it('展示生效路径、来源与可写状态', async () => {
    render(<Settings />)

    expect(await screen.findByText('📁 生成图输出目录')).toBeInTheDocument()
    expect(screen.getByText('D:/proj/output')).toBeInTheDocument()
    expect(screen.getByText('可写')).toBeInTheDocument()
    expect(screen.getByText('默认目录')).toBeInTheDocument()
  })

  it('填写路径并保存 → 调 saveOutputDir 并提示', async () => {
    const user = userEvent.setup()
    render(<Settings />)
    const input = await screen.findByLabelText('输出目录')

    await user.type(input, 'D:/exports')
    await user.click(screen.getByRole('button', { name: '保存输出目录' }))

    expect(saveOutputDir).toHaveBeenCalledWith('D:/exports')
    expect(await screen.findByText(/已保存/)).toBeInTheDocument()
  })

  it('环境变量提供时锁定输入并说明', async () => {
    getSettings.mockResolvedValue({
      ...SETTINGS,
      output: { ...SETTINGS.output, source: 'env', env_locked: true, dir: 'D:/from-env',
                effective_dir: 'D:/from-env' },
    })
    render(<Settings />)

    expect(await screen.findByLabelText('输出目录')).toBeDisabled()
    expect(screen.getByText(/ECOMM_OUTPUT_DIR/)).toBeInTheDocument()
  })

  it('路径不可写时给出明确告警', async () => {
    getSettings.mockResolvedValue({
      ...SETTINGS,
      output: { ...SETTINGS.output, writable: false, error: 'PermissionError: access denied' },
    })
    render(<Settings />)

    expect(await screen.findByText('不可写')).toBeInTheDocument()
    expect(screen.getByText(/PermissionError/)).toBeInTheDocument()
  })

  it('保存失败展示后端原因（不静默）', async () => {
    const user = userEvent.setup()
    saveOutputDir.mockRejectedValue(new Error('输出目录不可用（PermissionError）——请检查盘符与写入权限'))
    render(<Settings />)

    await user.type(await screen.findByLabelText('输出目录'), 'Z:/nope')
    await user.click(screen.getByRole('button', { name: '保存输出目录' }))

    expect(await screen.findByText(/输出目录不可用/)).toBeInTheDocument()
  })
})

describe('Settings 加载与渲染', () => {
  it('测试连接：设置页把请求转给 API 并展示结果（B3-24）', async () => {
    const user = userEvent.setup()
    render(<Settings />)
    await screen.findByText('🔑 模型服务商')

    const head = screen.getAllByRole('button', { name: /OpenAI/ })[0]
    await user.click(head)
    await user.click(screen.getByRole('button', { name: '测试连接' }))

    expect(testProviderConnection).toHaveBeenCalledWith('openai', { allow_image: false })
    expect(await screen.findByText(/连接正常/)).toBeInTheDocument()
  })
  it('租户脱敏载荷：显示提示并隐藏管理面区块（B0-1）', async () => {
    getSettings.mockResolvedValue({ ...SETTINGS, redacted: true, api_keys: [], tenant_keys: [], provider_routes: [] })
    render(<Settings />)

    expect(await screen.findByText('🏷️ 当前以租户 Key 访问')).toBeInTheDocument()
    expect(screen.queryByText('🔑 模型服务商')).not.toBeInTheDocument()
    expect(screen.queryByText('🔀 模型映射（config/models.yaml）')).not.toBeInTheDocument()
    expect(screen.queryByText('🏢 租户 API Key（每租户独立钥匙）')).not.toBeInTheDocument()
    // 与租户相关的本地配置仍可用
    expect(screen.getByText('🔐 前端 API Key（浏览器本地存储）')).toBeInTheDocument()
  })

  it('管理员全量载荷：不显示脱敏提示（回归）', async () => {
    render(<Settings />)

    expect(await screen.findByText('🔑 模型服务商')).toBeInTheDocument()
    expect(screen.queryByText('🏷️ 当前以租户 Key 访问')).not.toBeInTheDocument()
  })

  it('加载中显示 spinner', () => {
    let resolveFn
    getSettings.mockReturnValue(new Promise(r => { resolveFn = r }))
    render(<Settings />)
    expect(screen.getByText('加载设置...')).toBeInTheDocument()
    resolveFn(SETTINGS)
  })

  it('概览区显示凭据进度、已生效 Provider 与自定义端点/模型计数', async () => {
    render(<Settings />)
    const creds = (await screen.findByText('已配置凭据')).closest('.overview-metric')
    const active = screen.getByText('已生效 Provider').closest('.overview-metric')
    const custom = screen.getByText('自定义端点/模型').closest('.overview-metric')
    expect(creds.textContent).toContain('2')      // OpenAI + Seedream
    expect(creds.textContent).toContain('/ 5')
    expect(active.textContent).toContain('1')     // 仅 OpenAI 注册表可用
    expect(custom.textContent).toContain('0')
  })

  it('一行一 Provider 分组渲染（文本与视觉 / 图像生成）', async () => {
    render(<Settings />)
    expect(await screen.findByText('文本与视觉（多模态大模型）')).toBeInTheDocument()
    expect(screen.getByText('图像生成')).toBeInTheDocument()
    const openaiRow = screen.getByRole('button', { name: /OpenAI/ })
    expect(openaiRow).toHaveTextContent('openai')
    expect(openaiRow).toHaveTextContent('已配置 · 生效中')
    expect(screen.getByRole('button', { name: /Seedream/ })).toHaveTextContent('已配置 · 未激活')
    expect(screen.getByRole('button', { name: /DeepSeek/ })).toHaveTextContent('未配置')
  })

  it('展开卡片显示该 Provider 的凭据槽（含多凭据与环境变量锁定）', async () => {
    const user = userEvent.setup()
    render(<Settings />)
    await user.click(await screen.findByRole('button', { name: /Seedream/ }))
    // 可编辑槽（火山 AK/SK）
    expect(screen.getByLabelText('火山引擎 AccessKey 的 API 密钥')).toBeInTheDocument()
    // env 供给槽 → 只读锁而非输入框
    expect(screen.queryByLabelText('Seedream（即梦） 的 API 密钥')).toBeNull()
    expect(document.querySelector('.lock-note').textContent).toContain('SEEDREAM_API_KEY')
    expect(screen.getByText('即梦 API Key，或火山引擎 AK + SK（任选其一）')).toBeInTheDocument()
  })
})

describe('Settings 服务商密钥交互', () => {
  it('一次只展开一张卡片', async () => {
    const user = userEvent.setup()
    render(<Settings />)
    await user.click(await screen.findByRole('button', { name: /OpenAI/ }))
    expect(screen.getByLabelText('OpenAI 的 API 密钥')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: /DeepSeek/ }))
    expect(screen.getAllByLabelText(/的 API 密钥/)).toHaveLength(1)
    expect(screen.getByRole('button', { name: /OpenAI/ })).toHaveAttribute('aria-expanded', 'false')
  })

  it('应用密钥 → saveApiKeys 仅提交该环境变量', async () => {
    const user = userEvent.setup()
    render(<Settings />)
    await user.click(await screen.findByRole('button', { name: /DeepSeek/ }))
    await user.type(screen.getByLabelText('DeepSeek 的 API 密钥'), 'sk-ds-new')
    await user.click(screen.getByRole('button', { name: '应用' }))
    await waitFor(() => expect(saveApiKeys).toHaveBeenCalledWith({ DEEPSEEK_API_KEY: 'sk-ds-new' }))
  })

  it('env 供给的凭据显示只读锁', async () => {
    const user = userEvent.setup()
    render(<Settings />)
    await user.click(await screen.findByRole('button', { name: /Seedream/ }))
    const lock = document.querySelector('.lock-note')
    expect(lock).not.toBeNull()
    expect(lock.textContent).toContain('设置页保存不会生效')
  })

  it('自定义端点 + 模型 → saveProviderConfig(route, payload)', async () => {
    const user = userEvent.setup()
    render(<Settings />)
    await user.click(await screen.findByRole('button', { name: /OpenAI/ }))
    await user.click(screen.getByText('自定义设置（端点与模型）'))
    const baseUrl = screen.getByLabelText('API 端点（Base URL）')
    await user.clear(baseUrl)
    await user.type(baseUrl, 'https://my-coding-plan.example.com/v1')
    await user.click(screen.getByRole('button', { name: '+ 添加模型' }))
    await user.type(screen.getByLabelText('模型 id 1'), 'claude-sonnet-4-5-20250929')
    await user.click(screen.getByRole('button', { name: '保存自定义设置' }))
    await waitFor(() => expect(saveProviderConfig).toHaveBeenCalledWith('openai', {
      models: ['claude-sonnet-4-5-20250929'],
      base_url: 'https://my-coding-plan.example.com/v1',
    }))
    expect(await screen.findByRole('status')).toHaveTextContent('已保存并立即生效')
  })

  it('保存失败展示后端诊断', async () => {
    const user = userEvent.setup()
    saveProviderConfig.mockRejectedValue(new Error('Seedream（即梦） 使用官方固定端点（多上游），暂不支持自定义端点'))
    render(<Settings />)
    await user.click(await screen.findByRole('button', { name: /Seedream/ }))
    await user.click(screen.getByText('自定义设置（端点与模型）'))
    await user.click(screen.getByRole('button', { name: '+ 添加模型' }))
    await user.type(screen.getByLabelText('模型 id 1'), 'seedream-6.0')
    await user.click(screen.getByRole('button', { name: '保存自定义设置' }))
    expect(await screen.findByRole('status')).toHaveTextContent('暂不支持自定义端点')
  })

  it('清除密钥（两步确认）→ saveApiKeys 提交空值', async () => {
    const user = userEvent.setup()
    render(<Settings />)
    await user.click(await screen.findByRole('button', { name: /OpenAI/ }))
    await user.click(screen.getByRole('button', { name: '清除' }))
    await user.click(screen.getByRole('button', { name: '确认清除' }))
    await waitFor(() => expect(saveApiKeys).toHaveBeenCalledWith({ OPENAI_API_KEY: '' }))
  })
})

describe('Settings 租户 Key', () => {
  it('env 供给的租户 Key 不可编辑（无输入框，显示环境变量说明）', async () => {
    render(<Settings />)
    const row = await screen.findByText('tenant_a')
    expect(row.closest('div').textContent).toContain('ECOMM_TENANT_KEYS')
    expect(row.closest('div').querySelector('input')).toBeNull()
  })

  it('file 来源的租户显示创建输入框', async () => {
    render(<Settings />)
    const row = await screen.findByText('tenant_b')
    expect(row.closest('div').querySelector('input')).not.toBeNull()
    expect(screen.getByRole('button', { name: '创建' })).toBeInTheDocument()
  })

  it('租户 Key 创建 → saveTenantKey 携带 tenant_id 与密钥', async () => {
    const user = userEvent.setup()
    render(<Settings />)
    const input = await screen.findByPlaceholderText('为租户创建 Key（≥16 字符）')
    await user.type(input, 'tenant-b-key-1234567890')
    await user.click(screen.getByRole('button', { name: '创建' }))
    await waitFor(() => expect(saveTenantKey).toHaveBeenCalledWith('tenant_b', 'tenant-b-key-1234567890'))
  })

  it('租户 Key 保存失败显示错误', async () => {
    const user = userEvent.setup()
    saveTenantKey.mockRejectedValue(new Error('租户 Key 无权访问管理端点'))
    render(<Settings />)
    const input = await screen.findByPlaceholderText('为租户创建 Key（≥16 字符）')
    await user.type(input, 'tenant-b-key-1234567890')
    await user.click(screen.getByRole('button', { name: '创建' }))
    expect(await screen.findByText(/租户 Key 无权访问/)).toBeInTheDocument()
  })

  it('已配置租户：空输入时「保存/轮换」禁用（防一次误点删除密钥）', async () => {
    const user = userEvent.setup()
    render(<Settings />)
    const input = await screen.findByLabelText('租户 tenant_c 的 Key')
    const btn = screen.getByRole('button', { name: '保存/轮换' })
    expect(btn).toBeDisabled()                 // 与 placeholder「留空不修改」一致
    await user.type(input, 'tenant-c-key-1234567890')
    expect(btn).toBeEnabled()
  })

  it('租户 Key 删除需两步确认，确认后才提交空值', async () => {
    const user = userEvent.setup()
    render(<Settings />)
    await user.click(await screen.findByRole('button', { name: '删除' }))
    expect(saveTenantKey).not.toHaveBeenCalled()          // 首次点击仅展开确认
    await user.click(screen.getByRole('button', { name: '确认删除' }))
    await waitFor(() => expect(saveTenantKey).toHaveBeenCalledWith('tenant_c', ''))
  })

  it('租户 Key 删除可取消', async () => {
    const user = userEvent.setup()
    render(<Settings />)
    await user.click(await screen.findByRole('button', { name: '删除' }))
    await user.click(screen.getByRole('button', { name: '取消' }))
    expect(saveTenantKey).not.toHaveBeenCalled()
    expect(screen.getByRole('button', { name: '删除' })).toBeInTheDocument()
  })
})

describe('Settings 前端 API Key 与运行信息', () => {
  it('前端 API Key 保存 → setStoredApiKey', async () => {
    const user = userEvent.setup()
    render(<Settings />)
    const frontendInput = await screen.findByPlaceholderText('粘贴 ECOMM_API_KEY（与后端一致）')
    await user.type(frontendInput, 'admin-key-123')
    await user.click(screen.getByRole('button', { name: '保存' }))
    expect(setStoredApiKey).toHaveBeenCalledWith('admin-key-123')
  })

  it('运行信息展示可用 Provider 与 CORS 白名单', async () => {
    render(<Settings />)
    expect(await screen.findByText('⚙️ 运行信息')).toBeInTheDocument()
    expect(screen.getByText('deepseek · text/vision')).toBeInTheDocument()
    expect(screen.getByText('http://localhost:5173')).toBeInTheDocument()
  })
})


describe('会话策略（B1：审查/合规连续失败 N 次即停 + 提示词审核）', () => {
  const SETTINGS_WITH_CHAT = () => ({
    mock_mode: false,
    redacted: false,
    api_keys: [],
    tenant_keys: [],
    provider_routes: [],
    providers: [],
    agents: [],
    models_config: { capabilities: {}, agent_overrides: {} },
    model_catalog: {},
    agent_overrides_issues: [],
    output: { dir: '', effective_dir: '/tmp/out', source: 'default', writable: true, env_locked: false },
    chat: { max_consecutive_review_failures: 2, max_turns: 15, session_ttl_hours: 24 },
  })

  // 一次保存会提交整组策略（体检/审美审核/风格档案的默认值来自前端初始状态）
  const CHAT_PAYLOAD = (failures) => ({
    max_consecutive_review_failures: failures,
    prompt_aesthetic_threshold: 85,
    prompt_review_max_rounds: 1,
    require_prompt_review: true,
    require_prompt_confirm: false,
    style_library_enabled: true,
    style_library_max: 2,
  })

  it('展示当前阈值并提供保存', async () => {
    const user = userEvent.setup()
    getSettings.mockResolvedValue(SETTINGS_WITH_CHAT())
    render(<Settings />)

    const input = await screen.findByLabelText('连续失败即停次数')
    expect(input).toHaveValue(2)

    saveChatSettings.mockResolvedValue({
      ...SETTINGS_WITH_CHAT(),
      chat: { max_consecutive_review_failures: 4, max_turns: 15, session_ttl_hours: 24 },
    })
    await user.clear(input)
    await user.type(input, '4')
    await user.click(screen.getByLabelText('保存会话策略'))

    await waitFor(() => expect(saveChatSettings).toHaveBeenCalledWith(CHAT_PAYLOAD(4)))
    expect(await screen.findByText(/连续失败 4 次将自动停止/)).toBeInTheDocument()  })

  it('填 0 表示关闭该保护（明确提示）', async () => {
    const user = userEvent.setup()
    getSettings.mockResolvedValue(SETTINGS_WITH_CHAT())
    render(<Settings />)

    const input = await screen.findByLabelText('连续失败即停次数')
    saveChatSettings.mockResolvedValue({
      ...SETTINGS_WITH_CHAT(),
      chat: { max_consecutive_review_failures: 0, max_turns: 15, session_ttl_hours: 24 },
    })
    await user.clear(input)
    await user.type(input, '0')
    await user.click(screen.getByLabelText('保存会话策略'))

    await waitFor(() => expect(saveChatSettings).toHaveBeenCalledWith(CHAT_PAYLOAD(0)))
    expect(await screen.findByText(/已关闭「连续失败即停」/)).toBeInTheDocument()
  })

  it('非法输入不发请求并给出可读提示', async () => {
    const user = userEvent.setup()
    getSettings.mockResolvedValue(SETTINGS_WITH_CHAT())
    render(<Settings />)

    const input = await screen.findByLabelText('连续失败即停次数')
    await user.clear(input)
    await user.type(input, '99')
    await user.click(screen.getByLabelText('保存会话策略'))

    expect(await screen.findByText(/0–20 的整数/)).toBeInTheDocument()
    expect(saveChatSettings).not.toHaveBeenCalled()
  })

  // 用户 2026-09-18："让生成图提示词更接近大众的商品图审美"
  it('提示词审核控件：默认开启、阈值 85、可关闭并随保存提交', async () => {
    const user = userEvent.setup()
    getSettings.mockResolvedValue(SETTINGS_WITH_CHAT())
    saveChatSettings.mockResolvedValue(SETTINGS_WITH_CHAT())
    render(<Settings />)

    const toggle = await screen.findByLabelText('启用提示词审核')
    expect(toggle).toBeChecked()
    expect(screen.getByLabelText('审美阈值')).toHaveValue(85)
    expect(screen.getByLabelText('提示词重写轮数')).toHaveValue(1)

    await user.click(toggle)
    await user.click(screen.getByLabelText('保存会话策略'))

    await waitFor(() => expect(saveChatSettings).toHaveBeenCalledWith(
      { ...CHAT_PAYLOAD(2), require_prompt_review: false }))
    expect(await screen.findByText(/提示词审核已关闭/)).toBeInTheDocument()
  })

  it('审美阈值超出 0–100 时不发请求', async () => {
    const user = userEvent.setup()
    getSettings.mockResolvedValue(SETTINGS_WITH_CHAT())
    render(<Settings />)

    const threshold = await screen.findByLabelText('审美阈值')
    await user.clear(threshold)
    await user.type(threshold, '150')
    await user.click(screen.getByLabelText('保存会话策略'))

    expect(await screen.findByText(/审美阈值请输入 0–100 的数字/)).toBeInTheDocument()
    expect(saveChatSettings).not.toHaveBeenCalled()
  })

  it('服务端未返回审核开关时保持默认开启（不能静默关掉）', async () => {
    getSettings.mockResolvedValue(SETTINGS_WITH_CHAT())
    render(<Settings />)
    expect(await screen.findByLabelText('启用提示词审核')).toBeChecked()
  })
})

describe('生图质量策略卡片（用户要求：文字/参考图/水印可设置）', () => {
  function settingsWithImage() {
    return {
      mock_mode: false,
      redacted: false,
      api_keys: [],
      tenant_keys: [],
      provider_routes: [],
      providers: [],
      agents: [],
      models_config: { capabilities: {}, agent_overrides: {} },
      model_catalog: {},
      agent_overrides_issues: [],
      output: { dir: '', effective_dir: '/tmp/out', source: 'default', writable: true, env_locked: false },
      chat: { max_consecutive_review_failures: 2, max_turns: 15, session_ttl_hours: 24 },
      image: { text_strategy: 'preserve', reference_mode: 'auto', watermark: false,
               max_references: 4, slot_candidates: 1,
               quality: { edge_whiteness: 250, identity_similarity_min: 0.45 } },
    }
  }

  it('展示当前策略（文字/参考图/水印/阈值）', async () => {
    getSettings.mockResolvedValue(settingsWithImage())
    render(<Settings />)

    expect(await screen.findByText('🖼️ 生图质量策略（写入 config/image.yaml）')).toBeInTheDocument()
    expect(screen.getByLabelText('文字策略')).toHaveValue('preserve')
    expect(screen.getByLabelText('参考图模式')).toHaveValue('auto')
    expect(screen.getByLabelText('平台水印')).not.toBeChecked()
    expect(screen.getByText(/身份相似度 ≥ 0.45/)).toBeInTheDocument()
  })

  it('修改并保存 → 调 saveImageSettings 且提示生效', async () => {
    const user = userEvent.setup()
    getSettings.mockResolvedValue(settingsWithImage())
    saveImageSettings.mockResolvedValue({
      ...settingsWithImage(),
      image: { ...settingsWithImage().image, text_strategy: 'blur', watermark: true },
    })
    render(<Settings />)

    await user.selectOptions(await screen.findByLabelText('文字策略'), 'blur')
    await user.click(screen.getByLabelText('平台水印'))
    await user.click(screen.getByLabelText('保存生图质量策略'))

    await waitFor(() => expect(saveImageSettings).toHaveBeenCalledWith(
      { text_strategy: 'blur', reference_mode: 'auto', watermark: true, max_references: 4,
        typography: { font_scale: 1, max_items: 6, brand_color: '#1860AC',
                      accent_color: '#24945F', show_footer: true } }))
    expect(await screen.findByText(/下次出图即生效/)).toBeInTheDocument()
  })

  it('信息图排版样式可调（字号/条目数/页脚）并随保存提交', async () => {
    const user = userEvent.setup()
    getSettings.mockResolvedValue(settingsWithImage())
    saveImageSettings.mockResolvedValue(settingsWithImage())
    render(<Settings />)

    expect(await screen.findByText(/信息图排版/)).toBeInTheDocument()
    const scale = screen.getByLabelText('字号倍率')
    expect(scale).toHaveValue(1)
    await user.clear(scale)
    await user.type(scale, '1.3')

    const items = screen.getByLabelText('每图最多条目')
    await user.clear(items)
    await user.type(items, '4')

    await user.click(screen.getByLabelText('显示页脚'))
    await user.click(screen.getByLabelText('保存生图质量策略'))

    await waitFor(() => {
      const payload = saveImageSettings.mock.calls.at(-1)[0]
      expect(payload.typography.font_scale).toBe(1.3)
      expect(payload.typography.max_items).toBe(4)
      expect(payload.typography.show_footer).toBe(false)
    })
  })

  it('排版参数非法时不发请求', async () => {
    const user = userEvent.setup()
    getSettings.mockResolvedValue(settingsWithImage())
    render(<Settings />)

    const scale = await screen.findByLabelText('字号倍率')
    await user.clear(scale)
    await user.type(scale, '9')
    await user.click(screen.getByLabelText('保存生图质量策略'))
    expect(await screen.findByText(/0.6–1.8/)).toBeInTheDocument()
    expect(saveImageSettings).not.toHaveBeenCalled()
  })

  it('参考图张数非法时不发请求', async () => {
    const user = userEvent.setup()
    getSettings.mockResolvedValue(settingsWithImage())
    render(<Settings />)

    const input = await screen.findByLabelText('参考图张数')
    await user.clear(input)
    await user.type(input, '99')
    await user.click(screen.getByLabelText('保存生图质量策略'))

    expect(await screen.findByText(/1–8 的整数/)).toBeInTheDocument()
    expect(saveImageSettings).not.toHaveBeenCalled()
  })
})

// ── 风格档案开关 + 计价（A79-A96）──
describe('Settings 风格档案与计价', () => {
  const settingsBasic = () => ({
    mock_mode: true,
    chat: { max_consecutive_review_failures: 2, max_turns: 15, session_ttl_hours: 24,
            style_library_enabled: true, style_library_max: 2 },
  })

  beforeEach(() => {
    vi.clearAllMocks()
    getProviderPresets.mockResolvedValue({ presets: [] })
    getPricing.mockResolvedValue({ models: [], entries: [], staleness: [] })
  })

  it('风格档案开关与条数会随会话策略一起提交', async () => {
    const user = userEvent.setup()
    getSettings.mockResolvedValue(settingsBasic())
    saveChatSettings.mockResolvedValue(settingsBasic())
    render(<Settings />)

    const toggle = await screen.findByLabelText('启用风格词库')
    expect(toggle).toBeChecked()
    await user.click(toggle)
    await user.click(screen.getByLabelText('保存会话策略'))

    await waitFor(() => expect(saveChatSettings).toHaveBeenCalledWith(
      expect.objectContaining({ style_library_enabled: false, style_library_max: 2 })))
    expect(await screen.findByText(/风格档案不注入/)).toBeInTheDocument()
  })

  it('未标定模型显示"未标定"，不给金额', async () => {
    getSettings.mockResolvedValue(settingsBasic())
    getPricing.mockResolvedValue({
      models: [{ key: 'doubao-seedream-5-0-260128', route: 'ark',
                 model: 'doubao-seedream-5-0-260128', capability: 'image',
                 currency: 'CNY', priced: false }],
      entries: [], staleness: [],
    })
    render(<Settings />)
    // 等**表格行**出现再断言：卡片里的说明文字也含"未标定"三个字（只等文字会提前通过）
    const input = await screen.findByLabelText('doubao-seedream-5-0-260128 的单价')
    expect(input).toHaveValue(null)
    expect(screen.getAllByText('未标定').length).toBeGreaterThan(0)
  })

  it('填单价保存 → savePricing 带上用量口径与币种', async () => {
    const user = userEvent.setup()
    getSettings.mockResolvedValue(settingsBasic())
    getPricing.mockResolvedValue({
      models: [{ key: 'doubao-seedream-5-0-260128', route: 'ark',
                 model: 'doubao-seedream-5-0-260128', capability: 'image',
                 currency: 'CNY', priced: false }],
      entries: [], staleness: [],
    })
    savePricing.mockResolvedValue({ entries: [], message: '已保存 1 条价格' })
    render(<Settings />)

    const input = await screen.findByLabelText('doubao-seedream-5-0-260128 的单价')
    await user.type(input, '0.28')
    await user.click(screen.getByLabelText('保存价格表'))

    await waitFor(() => expect(savePricing).toHaveBeenCalledWith({
      'doubao-seedream-5-0-260128': { unit: 'image', in: 0.28, out: 0.28,
                                      currency: 'CNY', source: '用户填写' },
    }))
  })

  it('标定：按实际花费反推单价（带张数与币种）', async () => {
    const user = userEvent.setup()
    getSettings.mockResolvedValue(settingsBasic())
    getPricing.mockResolvedValue({
      models: [{ key: 'doubao-seedream-5-0-260128', route: 'ark',
                 model: 'doubao-seedream-5-0-260128', capability: 'image',
                 currency: 'CNY', priced: false }],
      entries: [], staleness: [],
    })
    calibratePricing.mockResolvedValue({ entry: {}, message: '已按实际花费反推单价' })
    render(<Settings />)

    await screen.findByLabelText('doubao-seedream-5-0-260128 的单价')
    await user.selectOptions(screen.getByLabelText('标定模型'), 'doubao-seedream-5-0-260128')
    await user.type(screen.getByLabelText('本次实际花费'), '1.4')
    await user.clear(screen.getByLabelText('标定张数'))
    await user.type(screen.getByLabelText('标定张数'), '5')
    await user.click(screen.getByLabelText('按实际花费标定'))

    await waitFor(() => expect(calibratePricing).toHaveBeenCalledWith(
      expect.objectContaining({ model: 'doubao-seedream-5-0-260128', capability: 'image',
                                actual_amount: 1.4,
                                usage: { images: 5, currency: 'CNY' } })))
  })
})
