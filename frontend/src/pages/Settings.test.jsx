import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import Settings from './Settings'

vi.mock('../api', () => ({
  getSettings: vi.fn(),
  saveApiKeys: vi.fn(),
  saveTenantKey: vi.fn(),
  getStoredApiKey: vi.fn(() => ''),
  setStoredApiKey: vi.fn(),
}))
import { getSettings, saveApiKeys, saveTenantKey, setStoredApiKey } from '../api'

const SETTINGS = {
  mock_mode: false,
  api_keys: [
    { name: 'OpenAI', env: 'OPENAI_API_KEY', capabilities: 'vision / text / image', configured: true },
    { name: 'DeepSeek', env: 'DEEPSEEK_API_KEY', capabilities: 'text', configured: false },
  ],
  tenant_keys: [
    { tenant_id: 'tenant_a', name: 'Tenant A', tier: 'pro', configured: true, source: 'env' },
    { tenant_id: 'tenant_b', name: 'Tenant B', tier: 'free', configured: false, source: 'file' },
  ],
  providers: [],
  agents: [],
  models_config: { capabilities: {}, agent_overrides: {} },
  model_catalog: {},
  cors_origins: [],
}

beforeEach(() => {
  vi.clearAllMocks()
  getSettings.mockResolvedValue(SETTINGS)
  saveApiKeys.mockResolvedValue(SETTINGS)
  saveTenantKey.mockResolvedValue({ tenant_keys: SETTINGS.tenant_keys })
})

describe('Settings 加载与渲染', () => {
  it('加载中显示 spinner', () => {
    let resolveFn
    getSettings.mockReturnValue(new Promise(r => { resolveFn = r }))
    render(<Settings />)
    expect(screen.getByText('加载设置...')).toBeInTheDocument()
    resolveFn(SETTINGS)
  })

  it('渲染 API Key 表与配置状态徽章', async () => {
    render(<Settings />)
    expect(await screen.findByText('🔑 API Key（1/2 已配置）')).toBeInTheDocument()
    expect(screen.getAllByText('已配置').length).toBeGreaterThan(0)
  })

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
})

describe('Settings 交互', () => {
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

  it('API Key 保存 → saveApiKeys 提交改动项', async () => {
    const user = userEvent.setup()
    render(<Settings />)
    const inputs = await screen.findAllByPlaceholderText('已配置（输入新值覆盖，留空不修改）')
    await user.type(inputs[0], 'sk-new-key')
    await user.click(screen.getByRole('button', { name: '保存全部修改' }))
    await waitFor(() => expect(saveApiKeys).toHaveBeenCalledWith({ OPENAI_API_KEY: 'sk-new-key' }))
  })

  it('前端 API Key 保存 → setStoredApiKey', async () => {
    const user = userEvent.setup()
    render(<Settings />)
    const frontendInput = await screen.findByPlaceholderText('粘贴 ECOMM_API_KEY（与后端一致）')
    await user.type(frontendInput, 'admin-key-123')
    await user.click(screen.getByRole('button', { name: '保存' }))
    expect(setStoredApiKey).toHaveBeenCalledWith('admin-key-123')
  })
})
