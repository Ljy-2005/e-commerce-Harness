import { describe, it, expect, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import ProviderCard, { routeStatus, modelListFailure } from './ProviderCard'

const OPENAI = {
  route: 'openai',
  label: 'OpenAI',
  capabilities: 'vision / text / image',
  default_base_url: 'https://api.openai.com/v1',
  base_url_supported: true,
  credential_hint: '官方 Key 或第三方 coding plan / 代理 Key',
  effective_base_url: 'https://api.openai.com/v1',
  base_url_custom: false,
  base_url_source: '',
  models: [],
  official_models: ['gpt-4o', 'dall-e-3'],
  kind: 'openai',
  credentials: [
    { name: 'OpenAI', env: 'OPENAI_API_KEY', provider: 'openai', capabilities: 'vision / text / image',
      configured: true, available: true, source: 'file' },
  ],
  configured: true,
  available: true,
}

function renderCard(overrides = {}, handlers = {}) {
  const provider = { ...OPENAI, ...overrides }
  const props = {
    provider,
    expanded: true,
    onToggle: vi.fn(),
    onSaveKey: vi.fn().mockResolvedValue(undefined),
    onClearKey: vi.fn().mockResolvedValue(undefined),
    onSaveConfig: vi.fn().mockResolvedValue(undefined),
    onTest: vi.fn().mockResolvedValue({ ok: true, model: 'gpt-4o', latency_ms: 812, detail: '连接正常' }),
    ...handlers,
  }
  render(<ProviderCard {...props} />)
  return props
}

const IMAGE_ONLY = {
  route: 'seedream', label: 'Seedream（即梦）', capabilities: 'image',
  base_url_supported: false, default_base_url: '', effective_base_url: '',
  credential_hint: '', models: [], official_models: [], credentials: [
    { name: 'Seedream（即梦）', env: 'SEEDREAM_API_KEY', provider: 'seedream',
      capabilities: 'image', configured: true, available: true, source: 'file' },
  ],
  configured: true, available: true,
}

async function openAdvanced(user) {
  await user.click(screen.getByText('自定义设置（端点与模型）'))
}

describe('routeStatus / modelListFailure（纯函数）', () => {
  it('路由状态三态', () => {
    expect(routeStatus({ configured: true, available: true })).toMatchObject({ dot: 'dot-ok' })
    expect(routeStatus({ configured: true, available: false })).toMatchObject({ dot: 'dot-warn' })
    expect(routeStatus({ configured: false, available: false })).toMatchObject({ dot: 'dot-idle' })
  })
  it('模型 id 校验：空格/中文非法，正常 id 通过', () => {
    expect(modelListFailure(['gpt-5-codex', 'claude-sonnet-4-5-20250929'])).toBe('')
    expect(modelListFailure(['a b'])).toContain('非法字符')
    expect(modelListFailure(['中文模型'])).toContain('非法字符')
    expect(modelListFailure([''])).toBe('')   // 空行 = 未填写，忽略
  })
})

describe('ProviderCard 行与卡片', () => {
  it('行头展示 provider/能力/状态，点击调用 onToggle', async () => {
    const user = userEvent.setup()
    const props = renderCard()
    const head = screen.getByRole('button', { name: /OpenAI/ })
    expect(head).toHaveTextContent('openai')
    expect(head).toHaveTextContent('已配置 · 生效中')
    expect(head).toHaveAttribute('aria-expanded', 'true')
    await user.click(head)
    expect(props.onToggle).toHaveBeenCalled()
  })

  it('自定义端点/自定义模型在行头有标记', () => {
    renderCard({ base_url_custom: true, effective_base_url: 'https://proxy.example.com/v1', models: ['gpt-5-codex'] })
    expect(screen.getByText('自定义端点')).toBeInTheDocument()
    expect(screen.getByText('自定义模型 ×1')).toBeInTheDocument()
  })

  it('多凭据 Provider：每个凭据槽独立渲染（Seedream 三种）', () => {
    renderCard({
      route: 'seedream', label: 'Seedream（即梦）', capabilities: 'image',
      base_url_supported: false, default_base_url: '',
      credential_hint: '即梦 API Key，或火山引擎 AK + SK（任选其一）',
      credentials: [
        { name: 'Seedream（即梦）', env: 'SEEDREAM_API_KEY', provider: 'seedream', capabilities: 'image', configured: false, available: false, source: '' },
        { name: '火山引擎 AccessKey', env: 'VOLCANO_ACCESS_KEY', provider: 'seedream', capabilities: 'image（签名）', configured: false, available: false, source: '' },
        { name: '火山引擎 SecretKey', env: 'VOLCANO_SECRET_KEY', provider: 'seedream', capabilities: 'image（签名）', configured: false, available: false, source: '' },
      ],
      configured: false, available: false,
    })
    expect(screen.getByLabelText('Seedream（即梦） 的 API 密钥')).toBeInTheDocument()
    expect(screen.getByLabelText('火山引擎 AccessKey 的 API 密钥')).toBeInTheDocument()
    expect(screen.getByLabelText('火山引擎 SecretKey 的 API 密钥')).toBeInTheDocument()
    expect(screen.getByText('即梦 API Key，或火山引擎 AK + SK（任选其一）')).toBeInTheDocument()
  })

  it('收起时不渲染卡片内容', () => {
    renderCard({}, { expanded: false })
    expect(screen.queryByText('自定义设置（端点与模型）')).toBeNull()
    expect(screen.queryByText('凭据')).toBeNull()
  })
})

describe('ProviderCard 自定义设置：端点与模型', () => {
  it('端点：默认显示官方端点，可改为第三方 coding plan 端点并保存', async () => {
    const user = userEvent.setup()
    const props = renderCard()
    await openAdvanced(user)
    const input = screen.getByLabelText('API 端点（Base URL）')
    expect(input).toHaveValue('https://api.openai.com/v1')
    expect(screen.getByText(/留空 = 使用官方端点/)).toBeInTheDocument()

    await user.clear(input)
    await user.type(input, 'https://proxy.example.com/v1')
    await user.click(screen.getByRole('button', { name: '保存自定义设置' }))
    await waitFor(() => expect(props.onSaveConfig).toHaveBeenCalledWith('openai', {
      models: [], base_url: 'https://proxy.example.com/v1',
    }))
    expect(await screen.findByRole('status')).toHaveTextContent('已保存并立即生效')
  })

  it('端点：恢复官方端点按钮清空输入', async () => {
    const user = userEvent.setup()
    renderCard({ base_url_custom: true, effective_base_url: 'https://proxy.example.com/v1' })
    await openAdvanced(user)
    await user.click(screen.getByRole('button', { name: '恢复官方端点' }))
    expect(screen.getByLabelText('API 端点（Base URL）')).toHaveValue('')
  })

  it('端点：env 供给时只读锁定且提示环境变量名', async () => {
    const user = userEvent.setup()
    renderCard({ base_url_source: 'env', effective_base_url: 'https://env.example.com/v1' })
    await openAdvanced(user)
    expect(screen.getByLabelText('API 端点（Base URL）')).toBeDisabled()
    expect(screen.getByText(/OPENAI_BASE_URL/)).toBeInTheDocument()
  })

  it('模型：添加/移除 + 保存 payload 仅含 models', async () => {
    const user = userEvent.setup()
    const props = renderCard()
    await openAdvanced(user)
    expect(screen.getByText(/使用内置目录/)).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: '+ 添加模型' }))
    await user.type(screen.getByLabelText('模型 id 1'), 'gpt-5-codex')
    await user.click(screen.getByRole('button', { name: '+ 添加模型' }))
    await user.type(screen.getByLabelText('模型 id 2'), 'claude-sonnet-4-5-20250929')
    await user.click(screen.getByRole('button', { name: '保存自定义设置' }))
    await waitFor(() => expect(props.onSaveConfig).toHaveBeenCalledWith('openai', {
      models: ['gpt-5-codex', 'claude-sonnet-4-5-20250929'],
      base_url: 'https://api.openai.com/v1',
    }))

    // 移除第二个
    await user.click(screen.getByRole('button', { name: '移除模型 2' }))
    expect(screen.queryByLabelText('模型 id 2')).toBeNull()
  })

  it('模型：非法 id 时给出错误并禁用保存', async () => {
    const user = userEvent.setup()
    renderCard()
    await openAdvanced(user)
    await user.click(screen.getByRole('button', { name: '+ 添加模型' }))
    await user.type(screen.getByLabelText('模型 id 1'), 'bad model')
    expect(screen.getByRole('alert')).toHaveTextContent('非法字符')
    expect(screen.getByRole('button', { name: '保存自定义设置' })).toBeDisabled()
  })

  it('图像路由：端点固定为官方（无端点输入），仍可配置模型', async () => {
    const user = userEvent.setup()
    const props = renderCard({
      route: 'seedream', label: 'Seedream（即梦）', capabilities: 'image',
      base_url_supported: false, default_base_url: '', effective_base_url: '',
      credentials: [{ name: 'Seedream（即梦）', env: 'SEEDREAM_API_KEY', provider: 'seedream',
                      capabilities: 'image', configured: true, available: true, source: 'file' }],
    })
    await openAdvanced(user)
    expect(screen.queryByLabelText('API 端点（Base URL）')).toBeNull()
    expect(screen.getByText(/官方固定端点/)).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: '+ 添加模型' }))
    await user.type(screen.getByLabelText('模型 id 1'), 'seedream-6.0')
    await user.click(screen.getByRole('button', { name: '保存自定义设置' }))
    await waitFor(() => expect(props.onSaveConfig).toHaveBeenCalledWith('seedream', {
      models: ['seedream-6.0'],
    }))
  })

  it('保存失败展示 Host 诊断', async () => {
    const user = userEvent.setup()
    const onSaveConfig = vi.fn().mockRejectedValue(new Error('端点必须是可解析的 http(s) URL'))
    renderCard({}, { onSaveConfig })
    await openAdvanced(user)
    await user.click(screen.getByRole('button', { name: '+ 添加模型' }))
    await user.type(screen.getByLabelText('模型 id 1'), 'm1')
    await user.click(screen.getByRole('button', { name: '保存自定义设置' }))
    expect(await screen.findByText(/http\(s\) URL/)).toBeInTheDocument()
  })

  it('父级刷新（如保存凭据后 setSettings）不覆盖正在编辑的模型草稿', async () => {
    const user = userEvent.setup()
    const provider = { ...OPENAI, models: [] }
    const props = {
      provider, expanded: true, onToggle: vi.fn(),
      onSaveKey: vi.fn().mockResolvedValue(undefined),
      onClearKey: vi.fn().mockResolvedValue(undefined),
      onSaveConfig: vi.fn().mockResolvedValue(undefined),
    }
    const { rerender } = render(<ProviderCard {...props} />)
    await openAdvanced(user)
    await user.click(screen.getByRole('button', { name: '+ 添加模型' }))
    await user.type(screen.getByLabelText('模型 id 1'), 'gpt-5-codex')

    // 模拟父级保存了别的东西 → settings 重新拉取 → provider 对象与 models 数组都是新引用
    rerender(<ProviderCard {...props} provider={{ ...OPENAI, models: [] }} />)
    expect(screen.getByLabelText('模型 id 1')).toHaveValue('gpt-5-codex')
  })
})

describe('测试连接（B3-24）', () => {
  it('点击后调用 onTest 并展示成功结果（模型 + 耗时）', async () => {
    const user = userEvent.setup()
    const props = renderCard()

    await user.click(screen.getByRole('button', { name: '测试连接' }))

    expect(props.onTest).toHaveBeenCalledWith('openai', { allow_image: false })
    const line = await screen.findByRole('status')
    expect(line).toHaveTextContent('连接正常')
    expect(line).toHaveTextContent('gpt-4o')
    expect(line).toHaveTextContent('812ms')
  })

  it('连接失败展示上游原文（400/401 可区分）', async () => {
    const user = userEvent.setup()
    renderCard({}, {
      onTest: vi.fn().mockResolvedValue({
        ok: false, skipped: false,
        detail: 'OpenAI API error: 401 @ https://gw.example/v1 — invalid api key',
      }),
    })

    await user.click(screen.getByRole('button', { name: '测试连接' }))

    const line = await screen.findByRole('status')
    expect(line).toHaveTextContent('401')
    expect(line).toHaveTextContent('invalid api key')
  })

  it('Mock / 跳过场景展示原因而不是报错', async () => {
    const user = userEvent.setup()
    renderCard({}, {
      onTest: vi.fn().mockResolvedValue({
        ok: false, skipped: true, is_mock: true,
        reason: '当前为 Mock（未配置该 Provider 的真实凭据）：未发起真实调用',
      }),
    })

    await user.click(screen.getByRole('button', { name: '测试连接' }))

    const line = await screen.findByRole('status')
    expect(line).toHaveTextContent('Mock')
    expect(line.className).toContain('warn')
  })

  it('图像路由：默认不带生图测试，勾选后 allow_image=true', async () => {
    const user = userEvent.setup()
    const props = renderCard(IMAGE_ONLY)
    expect(screen.getByText('包含生图测试（会产生费用）')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: '测试连接' }))
    expect(props.onTest).toHaveBeenCalledWith('seedream', { allow_image: false })

    await user.click(screen.getByRole('checkbox'))
    await user.click(screen.getByRole('button', { name: '测试连接' }))
    expect(props.onTest).toHaveBeenLastCalledWith('seedream', { allow_image: true })
  })

  it('未传 onTest 时不渲染测试区（向后兼容）', () => {
    renderCard({}, { onTest: undefined })
    expect(screen.queryByRole('button', { name: '测试连接' })).not.toBeInTheDocument()
  })

  it('旧版通道展示引导横幅与「旧版通道」标记', () => {
    renderCard({
      route: 'seedream', label: '火山视觉智能（旧版 AK/SK 签名）', capabilities: 'image',
      deprecated: true,
      deprecated_hint: '如果你用的是火山方舟 API Key（ark- 开头），它属于「火山引擎方舟（Ark）」卡片',
    })

    const banner = screen.getByRole('note')
    expect(banner).toHaveTextContent('旧版通道')
    expect(banner).toHaveTextContent('火山引擎方舟（Ark）')
    expect(screen.getAllByText('旧版通道').length).toBeGreaterThan(1)  // 行头徽章 + 横幅
  })

  it('Key 放错槽位时给出一键迁移按钮并回调', async () => {
    const user = userEvent.setup()
    const onMoveCredential = vi.fn().mockResolvedValue(undefined)
    renderCard({}, {
      onMoveCredential,
      onTest: vi.fn().mockResolvedValue({
        ok: false, skipped: false,
        detail: 'VOLCANO_ACCESS_KEY 里填的看起来是火山方舟 API Key（ark- 开头）…',
        suggested: { route: 'ark', env: 'ARK_API_KEY', from_env: 'VOLCANO_ACCESS_KEY' },
      }),
    })

    await user.click(screen.getByRole('button', { name: '测试连接' }))
    await user.click(await screen.findByRole('button', { name: '迁移凭据到正确的服务商' }))

    expect(onMoveCredential).toHaveBeenCalledWith('VOLCANO_ACCESS_KEY', 'ARK_API_KEY')
  })

  it('正常失败结果不显示迁移按钮（回归）', async () => {
    const user = userEvent.setup()
    renderCard({}, {
      onMoveCredential: vi.fn(),
      onTest: vi.fn().mockResolvedValue({ ok: false, detail: 'OpenAI API error: 401', suggested: null }),
    })

    await user.click(screen.getByRole('button', { name: '测试连接' }))

    await screen.findByRole('status')
    expect(screen.queryByRole('button', { name: '迁移凭据到正确的服务商' })).not.toBeInTheDocument()
  })

  it('拉取可用模型：点击调用 onPullModels 并按能力展示可点选的 id', async () => {
    const user = userEvent.setup()
    const props = renderCard({}, {
      onPullModels: vi.fn().mockResolvedValue({
        ok: true, total: 131, base_url: 'https://ark...',
        applicable: {
          image: ['doubao-seedream-5-0-260128', 'doubao-seedream-4-5-251128'],
          text: ['doubao-seed-2-1-pro-260628'],
        },
        models: [
          { id: 'doubao-seedream-5-0-260128', name: 'doubao-seedream-5-0', version: '260128',
            recommended: true, skip_reason: '' },
          { id: 'doubao-seedream-4-5-251128', name: 'doubao-seedream-4-5', version: '251128',
            recommended: true, skip_reason: '' },
          { id: 'doubao-seed-2-1-pro-260628', name: 'doubao-seed-2-1-pro', version: '260628',
            recommended: true, skip_reason: '' },
        ],
        detail: '',
      }),
    })
    await openAdvanced(user)

    await user.click(screen.getByRole('button', { name: /拉取可用模型/ }))

    expect(props.onPullModels).toHaveBeenCalledWith('openai')
    // 展示可读的模型名（而不是裸 id）
    expect(await screen.findByRole('button', { name: 'doubao-seedream-5-0' })).toBeInTheDocument()
    expect(screen.getByText(/该账号可见/)).toBeInTheDocument()
  })

  it('点击拉取到的模型名，填入的是完整 id', async () => {
    const user = userEvent.setup()
    renderCard({}, {
      onPullModels: vi.fn().mockResolvedValue({
        ok: true, total: 2, applicable: { image: ['doubao-seedream-5-0-260128'] },
        models: [{ id: 'doubao-seedream-5-0-260128', name: 'doubao-seedream-5-0',
                   version: '260128', recommended: true, skip_reason: '' }],
        detail: '',
      }),
    })
    await openAdvanced(user)
    await user.click(screen.getByRole('button', { name: /拉取可用模型/ }))
    await user.click(await screen.findByRole('button', { name: 'doubao-seedream-5-0' }))

    expect(screen.getByLabelText('模型 id 1')).toHaveValue('doubao-seedream-5-0-260128')
  })

  it('展示"已过滤"数量（即将下线 / 专用模型）', async () => {
    const user = userEvent.setup()
    renderCard({}, {
      onPullModels: vi.fn().mockResolvedValue({
        ok: true, total: 133, applicable: { image: ['doubao-seedream-5-0-260128'] },
        models: [
          { id: 'doubao-seedream-5-0-260128', name: 'doubao-seedream-5-0', recommended: true,
            skip_reason: '' },
          { id: 'doubao-seed-1-6-250615', name: 'doubao-seed-1-6', recommended: false,
            skip_reason: '即将下线' },
          { id: 'doubao-seed-translation-250915', name: 'doubao-seed-translation',
            recommended: false, skip_reason: '专用模型（translation）' },
        ],
        detail: '',
      }),
    })
    await openAdvanced(user)

    await user.click(screen.getByRole('button', { name: /拉取可用模型/ }))

    expect(await screen.findByText(/已过滤 2 个/)).toBeInTheDocument()
  })

  it('拉取失败展示原因（不静默）', async () => {
    const user = userEvent.setup()
    renderCard({}, {
      onPullModels: vi.fn().mockResolvedValue({
        ok: false, detail: '火山引擎方舟（Ark） 模型列表接口返回 404（部分服务商不提供 /models）',
        models: [], applicable: {},
      }),
    })
    await openAdvanced(user)

    await user.click(screen.getByRole('button', { name: /拉取可用模型/ }))

    expect(await screen.findByText(/不提供 \/models/)).toBeInTheDocument()
  })

  it('非 OpenAI 兼容路由不渲染拉取按钮（模型列表接口不可用）', async () => {
    const user = userEvent.setup()
    renderCard({ route: 'flux', label: 'FLUX', capabilities: 'image', kind: 'flux',
                 base_url_supported: false, models: [] }, { onPullModels: vi.fn() })

    await openAdvanced(user)
    expect(screen.queryByRole('button', { name: /拉取可用模型/ })).not.toBeInTheDocument()
  })
})
