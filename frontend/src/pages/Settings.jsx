import { useState, useEffect, useCallback } from 'react'
import {
  getSettings, saveApiKeys, saveTenantKey, saveProviderConfig, testProviderConnection,
  saveOutputDir, getProviderPresets, addCustomProvider, deleteCustomProvider,
  moveProviderCredential, pullProviderModels, getStoredApiKey, setStoredApiKey,
  saveChatSettings,
  saveImageSettings,
  getPricing, savePricing, calibratePricing,
} from '../api'
import ModelMappingEditor from '../components/ModelMappingEditor'
import ProviderCard from '../components/ProviderCard'

/** 图像生成类路由（仅 image 能力，不含文本/视觉）——用于分组展示 */
function isImageRoute(route) {
  const caps = route.capabilities || ''
  return caps.includes('image') && !caps.includes('vision')
}

export default function Settings() {
  const [settings, setSettings] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [openRoute, setOpenRoute] = useState('')   // 一次只展开一张编辑卡片
  const [frontendKey, setFrontendKey] = useState(getStoredApiKey())
  const [frontendMsg, setFrontendMsg] = useState('')
  const [tenantInputs, setTenantInputs] = useState({})  // tenant_id → 新输入明文
  const [tenantMsg, setTenantMsg] = useState({})        // tenant_id → 操作反馈
  const [tenantConfirm, setTenantConfirm] = useState({})  // tenant_id → 删除二次确认
  const [outputDir, setOutputDir] = useState('')
  const [outputBusy, setOutputBusy] = useState(false)
  const [outputMsg, setOutputMsg] = useState('')
  const [outputErr, setOutputErr] = useState('')
  // 会话策略（B1：连续失败即停；2026-09-18 追加：提示词体检 + 审美审核）
  // 默认值与后端 config/default.yaml 一致：服务端未返回这些键时**不能**当成"关闭"
  // （否则一次保存就会把提示词审核静默关掉 —— 同 A57 的三态布尔陷阱）
  const [chatPolicy, setChatPolicy] = useState({
    max_consecutive_review_failures: 2,
    require_prompt_review: true,
    prompt_aesthetic_threshold: '85',
    prompt_review_max_rounds: '1',
    require_prompt_confirm: false,
    style_library_enabled: true,
    style_library_max: '2',
  })
  const [chatBusy, setChatBusy] = useState(false)
  const [chatMsg, setChatMsg] = useState('')
  const [chatErr, setChatErr] = useState('')

  // ── 计价（A94-A96）：用量是事实、金额是估算；未标定的模型不估算金额 ──
  const [pricing, setPricing] = useState(null)
  const [priceInputs, setPriceInputs] = useState({})       // 模型 id → 单价字符串
  const [priceBusy, setPriceBusy] = useState(false)
  const [priceMsg, setPriceMsg] = useState('')
  const [priceErr, setPriceErr] = useState('')
  const [calib, setCalib] = useState({ model: '', actual: '', images: '1', currency: 'CNY' })

  // 添加自定义服务商（用户反馈："我无法自己添加模型服务商"）
  const [addOpen, setAddOpen] = useState(false)
  const [presets, setPresets] = useState([])
  const [addBusy, setAddBusy] = useState(false)
  const [addMsg, setAddMsg] = useState('')
  const [addErr, setAddErr] = useState('')
  const [draft, setDraft] = useState({
    route: '', label: '', kind: 'openai', base_url: '', api_key_env: '',
    capabilities: ['text'], models: '', credential_hint: '',
  })

  useEffect(() => {
    if (!addOpen || presets.length) return
    getProviderPresets().then(d => setPresets(d?.presets || [])).catch(() => {})
  }, [addOpen, presets.length])

  function fillPreset(preset) {
    setDraft({
      route: preset.route, label: preset.label, kind: preset.kind || 'openai',
      base_url: preset.base_url, api_key_env: preset.api_key_env,
      capabilities: preset.capabilities || ['text'],
      models: (preset.models || []).join('\n'),
      credential_hint: preset.credential_hint || '',
    })
    setAddMsg(`已填入「${preset.label}」预设，可直接添加或先改字段`)
    setAddErr('')
  }

  function toggleCapability(cap) {
    setDraft(prev => ({
      ...prev,
      capabilities: prev.capabilities.includes(cap)
        ? prev.capabilities.filter(c => c !== cap)
        : [...prev.capabilities, cap],
    }))
  }

  async function submitProvider() {
    setAddBusy(true)
    setAddMsg('')
    setAddErr('')
    try {
      const payload = {
        ...draft,
        route: draft.route.trim().toLowerCase(),
        models: draft.models.split('\n').map(m => m.trim()).filter(Boolean),
      }
      setSettings(await addCustomProvider(payload))
      setAddMsg(`✅ 已添加「${payload.label}」，在下方填入它的 API Key 即可启用`)
      setDraft({ route: '', label: '', kind: 'openai', base_url: '', api_key_env: '',
                 capabilities: ['text'], models: '', credential_hint: '' })
    } catch (err) {
      setAddErr(err.message || '添加失败')
    }
    setAddBusy(false)
  }

  async function removeProvider(route) {
    setAddMsg('')
    setAddErr('')
    try {
      setSettings(await deleteCustomProvider(route))
      setAddMsg(`✅ 已删除服务商「${route}」`)
    } catch (err) {
      setAddErr(err.message || '删除失败')
    }
  }

  useEffect(() => {
    setOutputDir(settings?.output?.dir || '')
  }, [settings?.output?.dir])

  useEffect(() => {
    const chat = settings?.chat
    if (!chat) return
    setChatPolicy((prev) => ({
      ...prev,
      ...(chat.max_consecutive_review_failures != null
        ? { max_consecutive_review_failures: String(chat.max_consecutive_review_failures) } : {}),
      ...(chat.prompt_aesthetic_threshold != null
        ? { prompt_aesthetic_threshold: String(chat.prompt_aesthetic_threshold) } : {}),
      ...(chat.prompt_review_max_rounds != null
        ? { prompt_review_max_rounds: String(chat.prompt_review_max_rounds) } : {}),
      ...(chat.require_prompt_review != null
        ? { require_prompt_review: !!chat.require_prompt_review } : {}),
      ...(chat.require_prompt_confirm != null
        ? { require_prompt_confirm: !!chat.require_prompt_confirm } : {}),
      ...(chat.style_library_enabled != null
        ? { style_library_enabled: !!chat.style_library_enabled } : {}),
      ...(chat.style_library_max != null
        ? { style_library_max: String(chat.style_library_max) } : {}),
    }))
  }, [settings?.chat])

  async function handleSaveChatPolicy() {
    setChatBusy(true)
    setChatMsg('')
    setChatErr('')
    try {
      const raw = String(chatPolicy.max_consecutive_review_failures).trim()
      const value = Number(raw === '' ? 2 : raw)
      if (!Number.isInteger(value) || value < 0 || value > 20) {
        throw new Error('请输入 0–20 的整数（0 = 关闭该保护）')
      }
      const threshold = Number(String(chatPolicy.prompt_aesthetic_threshold ?? 85).trim())
      if (!Number.isFinite(threshold) || threshold < 0 || threshold > 100) {
        throw new Error('审美阈值请输入 0–100 的数字')
      }
      const rounds = Number(String(chatPolicy.prompt_review_max_rounds ?? 1).trim())
      if (!Number.isInteger(rounds) || rounds < 0 || rounds > 3) {
        throw new Error('提示词重写轮数请输入 0–3 的整数（0 = 不打回重写）')
      }
      const styleMax = Number(String(chatPolicy.style_library_max ?? 2).trim())
      if (!Number.isInteger(styleMax) || styleMax < 0 || styleMax > 4) {
        throw new Error('每张注入档案条数请输入 0–4 的整数（0 = 不注入）')
      }
      const data = await saveChatSettings({
        max_consecutive_review_failures: value,
        prompt_aesthetic_threshold: threshold,
        prompt_review_max_rounds: rounds,
        require_prompt_review: !!chatPolicy.require_prompt_review,
        require_prompt_confirm: !!chatPolicy.require_prompt_confirm,
        style_library_enabled: !!chatPolicy.style_library_enabled,
        style_library_max: styleMax,
      })
      setSettings(data)
      setChatMsg(
        (value === 0
          ? '✅ 已保存：已关闭「连续失败即停」；'
          : `✅ 已保存：审查/合规连续失败 ${value} 次将自动停止会话；`)
        + (chatPolicy.require_prompt_review
          ? `提示词审核开启（审美阈值 ${threshold}，最多重写 ${rounds} 轮）`
          : '提示词审核已关闭')
        + (chatPolicy.style_library_enabled
          ? `；风格档案每张最多 ${styleMax} 条`
          : '；风格档案不注入')
      )
    } catch (err) {
      setChatErr(err.message || '保存失败')
    }
    setChatBusy(false)
  }

  // ── 计价：拉取现状（已标定条目 + 你实际用过的模型）──
  const refreshPricing = useCallback(async () => {
    try {
      const data = await getPricing()
      setPricing(data)
      const inputs = {}
      for (const item of data?.models || []) {
        const key = item.key || item.model
        inputs[key] = item.priced && item.in != null ? String(item.in) : ''
      }
      setPriceInputs(inputs)
      setPriceErr('')
    } catch (err) {
      setPriceErr(err.message || '加载价格表失败')
    }
  }, [])

  useEffect(() => {
    if (settings && !settings.redacted) refreshPricing()
  }, [settings, refreshPricing])

  async function handleSavePricing() {
    setPriceBusy(true)
    setPriceMsg('')
    setPriceErr('')
    try {
      const prices = {}
      for (const item of pricing?.models || []) {
        const key = item.key || item.model
        const raw = String(priceInputs[key] ?? '').trim()
        if (raw === '') continue
        const value = Number(raw)
        if (!Number.isFinite(value) || value < 0) {
          throw new Error(`「${item.model}」的单价必须是 ≥ 0 的数字（留空 = 不估算金额）`)
        }
        const unit = item.capability === 'image' ? 'image' : '1M_tokens'
        prices[key] = { unit, in: value, out: value,
                        currency: item.currency || 'USD', source: '用户填写' }
      }
      if (Object.keys(prices).length === 0) {
        throw new Error('至少填一个模型的单价（留空 = 该模型不估算金额）')
      }
      const data = await savePricing(prices)
      setPriceMsg(data?.message || '✅ 已保存')
      await refreshPricing()
    } catch (err) {
      setPriceErr(err.message || '保存失败')
    }
    setPriceBusy(false)
  }

  async function handleCalibrate() {
    setPriceBusy(true)
    setPriceMsg('')
    setPriceErr('')
    try {
      const model = String(calib.model || '').trim()
      if (!model) throw new Error('请选择要标定的模型')
      const amount = Number(String(calib.actual ?? '').trim())
      if (!Number.isFinite(amount) || amount < 0) {
        throw new Error('请填写本次实际花费（从供应商控制台读到的数字）')
      }
      const count = Number(String(calib.images ?? '1').trim())
      if (!Number.isInteger(count) || count < 1) throw new Error('张数/次数必须是正整数')
      const data = await calibratePricing({
        model, capability: calib.capability || 'image', actual_amount: amount,
        usage: { images: count, currency: calib.currency || 'CNY' },
      })
      setPriceMsg(data?.message || '✅ 已标定')
      await refreshPricing()
    } catch (err) {
      setPriceErr(err.message || '标定失败')
    }
    setPriceBusy(false)
  }

  // ── 生图质量策略（文字策略/参考图模式/水印 + 信息图排版样式）──
  const [imagePolicy, setImagePolicy] = useState({
    text_strategy: 'preserve', reference_mode: 'auto', watermark: false, max_references: 4,
    font_scale: '1', max_items: '6', brand_color: '#1860AC', accent_color: '#24945F',
    show_footer: true,
  })
  const [imageBusy, setImageBusy] = useState(false)
  const [imageMsg, setImageMsg] = useState('')
  const [imageErr, setImageErr] = useState('')

  useEffect(() => {
    const cfg = settings?.image
    if (cfg) {
      const typo = cfg.typography || {}
      setImagePolicy({
        text_strategy: cfg.text_strategy || 'preserve',
        reference_mode: cfg.reference_mode || 'auto',
        watermark: !!cfg.watermark,
        max_references: String(cfg.max_references ?? 4),
        font_scale: String(typo.font_scale ?? 1),
        max_items: String(typo.max_items ?? 6),
        brand_color: typo.brand_color || '#1860AC',
        accent_color: typo.accent_color || '#24945F',
        show_footer: typo.show_footer !== false,
      })
    }
  }, [settings?.image])

  async function handleSaveImagePolicy() {
    setImageBusy(true)
    setImageMsg('')
    setImageErr('')
    try {
      const refs = Number(String(imagePolicy.max_references).trim() || 4)
      if (!Number.isInteger(refs) || refs < 1 || refs > 8) {
        throw new Error('参考图张数请填 1–8 的整数')
      }
      const scale = Number(String(imagePolicy.font_scale).trim() || 1)
      if (!Number.isFinite(scale) || scale < 0.6 || scale > 1.8) {
        throw new Error('字号倍率请填 0.6–1.8（1 = 默认）')
      }
      const items = Number(String(imagePolicy.max_items).trim() || 6)
      if (!Number.isInteger(items) || items < 1 || items > 8) {
        throw new Error('每图最多条目请填 1–8 的整数')
      }
      const data = await saveImageSettings({
        text_strategy: imagePolicy.text_strategy,
        reference_mode: imagePolicy.reference_mode,
        watermark: imagePolicy.watermark,
        max_references: refs,
        typography: {
          font_scale: scale,
          max_items: items,
          brand_color: imagePolicy.brand_color,
          accent_color: imagePolicy.accent_color,
          show_footer: !!imagePolicy.show_footer,
        },
      })
      setSettings(data)
      setImageMsg('✅ 已保存，下次出图即生效（信息图排版样式同步生效）')
    } catch (err) {
      setImageErr(err.message || '保存失败')
    }
    setImageBusy(false)
  }

  async function handleSaveOutput(next) {
    setOutputBusy(true)
    setOutputMsg('')
    setOutputErr('')
    try {
      const data = await saveOutputDir(next !== undefined ? next : outputDir.trim())
      setSettings(data)
      setOutputMsg('✅ 已保存，后续生成图写入新目录')
    } catch (err) {
      setOutputErr(err.message || '保存失败')
    }
    setOutputBusy(false)
  }

  const refresh = useCallback(async () => {
    try {
      const data = await getSettings()
      setSettings(data)
      setError('')
    } catch (err) {
      setError(err.message || '加载设置失败')
    }
    setLoading(false)
  }, [])

  useEffect(() => { refresh() }, [refresh])

  async function handleSaveKey(env, value) {
    const data = await saveApiKeys({ [env]: value })
    setSettings(data)
  }

  async function handleClearKey(env) {
    const data = await saveApiKeys({ [env]: '' })
    setSettings(data)
  }

  async function handleSaveProviderConfig(route, payload) {
    const data = await saveProviderConfig(route, payload)
    setSettings(data)
  }

  /** 测试连接（B3-24）：返回结果对象（不回写 settings——测试不改配置） */
  async function handleTestConnection(route, payload) {
    return testProviderConnection(route, payload)
  }

  /** 把放错槽位的凭据迁移到正确服务商（实测场景：方舟 Key 填进了旧签名卡片） */
  async function handleMoveCredential(fromEnv, toEnv) {
    const data = await moveProviderCredential(fromEnv, toEnv)
    setSettings(data)
    setAddMsg(`✅ 已把 ${fromEnv} 的密钥迁移到 ${toEnv}，可在「${toEnv}」所属服务商上重新测试连接`)
    setAddErr('')
  }

  /** 拉取该服务商账号实际可用的模型（模型 id 不必再猜） */
  async function handlePullModels(route) {
    return pullProviderModels(route)
  }

  async function handleTenantKey(tenantId, value) {
    setTenantMsg(prev => ({ ...prev, [tenantId]: '' }))
    try {
      const res = await saveTenantKey(tenantId, value)
      const entry = (res.tenant_keys || []).find(t => t.tenant_id === tenantId)
      setTenantInputs(prev => ({ ...prev, [tenantId]: '' }))
      setTenantMsg(prev => ({
        ...prev,
        [tenantId]: entry?.configured ? '✅ 已保存并立即生效' : '✅ 已删除',
      }))
      refresh()
    } catch (err) {
      setTenantMsg(prev => ({ ...prev, [tenantId]: '❌ ' + (err.message || '操作失败') }))
    }
  }

  if (loading) {
    return (
      <div className="card text-center" style={{ minHeight: 200, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
        <div><div className="spinner" /><p className="text-sm mt-2">加载设置...</p></div>
      </div>
    )
  }

  if (error && !settings) {
    return <div className="card card-error mt-2"><h3 className="mb-1">加载失败</h3><p className="text-sm">{error}</p></div>
  }

  const apiKeys = settings?.api_keys || []
  const tenantKeys = settings?.tenant_keys || []
  const providerRoutes = (settings?.provider_routes || []).map(r => {
    const credentials = apiKeys.filter(k => k.provider === r.route)
    return {
      ...r,
      credentials,
      configured: credentials.some(c => c.configured),
      available: credentials.some(c => c.available),
    }
  })
  const configuredCount = apiKeys.filter(k => k.configured).length
  const activeCount = providerRoutes.filter(r => r.configured && r.available).length
  const customCount = providerRoutes.filter(r => r.base_url_custom || (r.models || []).length > 0).length
  const llmRoutes = providerRoutes.filter(r => !isImageRoute(r))
  const imageRoutes = providerRoutes.filter(isImageRoute)

  const renderRows = (rows) => rows.map(r => (
    <ProviderCard
      key={r.route}
      provider={r}
      expanded={openRoute === r.route}
      onToggle={() => setOpenRoute(openRoute === r.route ? '' : r.route)}
      onSaveKey={handleSaveKey}
      onClearKey={handleClearKey}
      onSaveConfig={handleSaveProviderConfig}
      onTest={handleTestConnection}
      onDelete={r.custom ? removeProvider : undefined}
      onMoveCredential={handleMoveCredential}
      onPullModels={handlePullModels}
    />
  ))

  return (
    <div>
      <h1 className="page-title">系统设置</h1>
      <p className="page-sub">
        配置各 AI 服务商的 API Key、端点与模型：保存后立即重建 Provider 注册表生效；
        密钥持久化到 <code className="text-xs">config/secrets.yaml</code>，
        端点与模型目录持久化到 <code className="text-xs">config/providers.yaml</code>（均已 gitignore）。
      </p>

      {/* 概览：运行模式 + 配置进度 + 自定义端点/模型 */}
      <div className="card mb-2 settings-overview">
        <div className="overview-metric">
          <span className="text-xs">运行模式</span>
          <span className={`badge ${settings?.mock_mode ? 'badge-warn' : 'badge-ok'}`}>
            {settings?.mock_mode ? 'Mock（模板数据）' : '真实 API'}
          </span>
        </div>
        <div className="overview-metric">
          <span className="text-xs">已配置凭据</span>
          <span className="n">{configuredCount}<span className="text-sm"> / {apiKeys.length}</span></span>
        </div>
        <div className="overview-metric">
          <span className="text-xs">已生效 Provider</span>
          <span className="n">{activeCount}</span>
        </div>
        <div className="overview-metric">
          <span className="text-xs">自定义端点/模型</span>
          <span className="n">{customCount}</span>
        </div>
        <span className="provider-spacer" />
        <span className="text-xs" style={{ maxWidth: 420 }}>
          {settings?.mock_mode
            ? '未配置任何 Key → 所有 Agent 使用 Mock 模板数据运行（可完整验证流程）；配置任意 Key 后自动切换真实 API。'
            : '已有密钥生效，Agent 将调用真实模型服务；未配置的服务商回落 Mock。'}
        </span>
      </div>

      {/* 租户 Key 访问管理面时的提示（后端返回脱敏子集，不再下发端点/密钥/租户清单） */}
      {settings?.redacted && (
        <div className="card card-warn mb-2" role="status">
          <h3 className="mb-1">🏷️ 当前以租户 Key 访问</h3>
          <p className="text-sm" style={{ marginBottom: 0 }}>
            服务商 Key、自定义端点与模型、租户 Key 清单属管理面配置，仅对全局
            <code className="text-xs"> ECOMM_API_KEY </code>可见，此处不再显示。
            Agent 列表与模型映射请见「Agent 配置」页；需要管理这些配置请使用管理员 Key。
          </p>
        </div>
      )}

      {/* 模型服务商：一行一 Provider（凭据 + 端点 + 模型目录） */}
      {!settings?.redacted && (
      <div className="card mb-2">
        <div className="flex items-center justify-between mb-1">
          <h3 style={{ marginBottom: 0 }}>🔑 模型服务商</h3>
          <div className="flex items-center gap-2">
            <span className="text-xs">密钥只写保存，页面从不回显</span>
            <button type="button" className="btn btn-ghost btn-sm"
                    onClick={() => { setAddOpen(!addOpen); setAddMsg(''); setAddErr('') }}>
              {addOpen ? '收起' : '➕ 添加服务商'}
            </button>
          </div>
        </div>

        {addOpen && (
          <div className="provider-add">
            <p className="text-sm mb-2">
              支持任意 <b>OpenAI 兼容</b>（智谱 / Kimi / 硅基流动 / OpenRouter / 火山方舟 / Ollama…）
              或 <b>Anthropic 兼容</b>服务商：填端点与凭据变量名即可，保存后立刻出现在下方列表。
            </p>
            {presets.length > 0 && (
              <div className="flex gap-1 items-center flex-wrap mb-2">
                <span className="text-xs">一键预设：</span>
                {presets.map(p => (
                  <button key={p.route} type="button" className="btn btn-ghost btn-sm"
                          disabled={!p.available} title={p.reason || ''}
                          onClick={() => fillPreset(p)}>
                    {p.label}{p.available ? '' : '（已存在）'}
                  </button>
                ))}
              </div>
            )}
            <div className="grid-2">
              <div className="form-group">
                <label className="label" htmlFor="np-route">路由 id（小写字母开头）</label>
                <input id="np-route" className="input" autoComplete="off" spellCheck="false"
                       placeholder="zhipu" value={draft.route}
                       onChange={e => setDraft({ ...draft, route: e.target.value })} />
              </div>
              <div className="form-group">
                <label className="label" htmlFor="np-label">展示名</label>
                <input id="np-label" className="input" autoComplete="off"
                       placeholder="智谱 GLM" value={draft.label}
                       onChange={e => setDraft({ ...draft, label: e.target.value })} />
              </div>
            </div>
            <div className="grid-2">
              <div className="form-group">
                <label className="label" htmlFor="np-kind">兼容协议</label>
                <select id="np-kind" className="select" value={draft.kind}
                        onChange={e => setDraft({ ...draft, kind: e.target.value })}>
                  <option value="openai">OpenAI 兼容（chat/completions + images/generations）</option>
                  <option value="anthropic">Anthropic 兼容（Messages API）</option>
                </select>
              </div>
              <div className="form-group">
                <label className="label" htmlFor="np-env">凭据环境变量名</label>
                <input id="np-env" className="input mono" autoComplete="off" spellCheck="false"
                       placeholder="ZHIPU_API_KEY" value={draft.api_key_env}
                       onChange={e => setDraft({ ...draft, api_key_env: e.target.value })} />
              </div>
            </div>
            <div className="form-group">
              <label className="label" htmlFor="np-base">API 端点（Base URL）</label>
              <input id="np-base" className="input mono" autoComplete="off" spellCheck="false"
                     placeholder="https://open.bigmodel.cn/api/paas/v4" value={draft.base_url}
                     onChange={e => setDraft({ ...draft, base_url: e.target.value })} />
            </div>
            <div className="form-group">
              <span className="label">支持能力</span>
              <div className="flex gap-2 items-center" style={{ flexWrap: 'wrap' }}>
                {[['text', '文本'], ['vision', '视觉（看图）'], ['image', '生图']].map(([cap, cn]) => (
                  <label key={cap} className="text-sm flex items-center" style={{ gap: 4 }}>
                    <input type="checkbox" checked={draft.capabilities.includes(cap)}
                           onChange={() => toggleCapability(cap)} />
                    {cn}
                  </label>
                ))}
              </div>
            </div>
            <div className="form-group">
              <label className="label" htmlFor="np-models">模型 id（每行一个，可留空）</label>
              <textarea id="np-models" className="textarea mono" rows={3} spellCheck="false"
                        placeholder={'glm-4.6\nglm-4v-plus'} value={draft.models}
                        onChange={e => setDraft({ ...draft, models: e.target.value })} />
            </div>
            <div className="flex gap-1 items-center">
              <button type="button" className="btn btn-primary btn-sm"
                      aria-label="确认添加服务商"
                      disabled={addBusy || !draft.route || !draft.label || !draft.base_url ||
                                !draft.api_key_env || draft.capabilities.length === 0}
                      onClick={submitProvider}>
                {addBusy ? '添加中...' : '添加服务商'}
              </button>
              <span className="text-xs">
                持久化到 <code className="text-xs">config/custom_providers.yaml</code>（已 gitignore）
              </span>
            </div>
            {addErr && <div className="key-error" role="alert">{addErr}</div>}
            {addMsg && <div className="key-status ok" role="status">{addMsg}</div>}
          </div>
        )}

        <div className="provider-list mt-1">
          <div className="provider-group-title">文本与视觉（多模态大模型）</div>
          {renderRows(llmRoutes)}
          <div className="provider-group-title">图像生成</div>
          {renderRows(imageRoutes)}
        </div>
      </div>
      )}

      {/* 模型映射（能力 → 模型） */}
      {!settings?.redacted && (
      <div className="card mb-2">
        <h3 className="mb-1">🔀 模型映射（config/models.yaml）</h3>
        <p className="text-sm mb-2">
          Agent 只声明能力（vision/text/image），具体用哪个模型由此处决定。
          格式 <code className="text-xs">provider/model</code>，例如 <code className="text-xs">deepseek/deepseek-v4-flash</code>；
          上方为 Provider 添加的模型会出现在建议列表中（用于第三方 coding plan 的自有模型名）。
        </p>
        <ModelMappingEditor settings={settings} onSaved={refresh} />
      </div>
      )}

      {/* 生成图输出目录（用户反馈：没法自己设置导出路径） */}
      {!settings?.redacted && (
      <div className="card mb-2">
        <h3 className="mb-1">📁 生成图输出目录</h3>
        <p className="text-sm mb-2">
          生成图会自动落盘到 <code className="text-xs">{'{输出根}/{租户}/{会话ID}/{平台}_{品类}_{序号}.png'}</code>，
          会话页可单张下载或打包导出 ZIP。默认 <code className="text-xs">&lt;项目根&gt;/output</code>
          （与 Docker 的 <code className="text-xs">harness_output</code> 卷一致）。
        </p>
        <div className="flex gap-1 items-center flex-wrap">
          <input
            className="input"
            style={{ maxWidth: 460 }}
            autoComplete="off"
            spellCheck="false"
            aria-label="输出目录"
            disabled={settings?.output?.env_locked || outputBusy}
            placeholder="留空 = 默认 <项目根>/output（可填绝对路径或相对项目根的路径）"
            value={outputDir}
            onChange={e => { setOutputDir(e.target.value); setOutputMsg(''); setOutputErr('') }}
          />
          <button type="button" className="btn btn-primary btn-sm"
                  aria-label="保存输出目录"
                  disabled={settings?.output?.env_locked || outputBusy}
                  onClick={() => handleSaveOutput()}>
            {outputBusy ? '保存中...' : '保存'}
          </button>
          <button type="button" className="btn btn-ghost btn-sm"
                  disabled={settings?.output?.env_locked || outputBusy || !settings?.output?.dir}
                  onClick={() => handleSaveOutput('')}>
            恢复默认
          </button>
        </div>
        <div className="kv mt-1">
          <span className="k">生效路径</span>
          <span className="v mono text-xs" style={{ wordBreak: 'break-all' }}>
            {settings?.output?.effective_dir || '—'}
          </span>
        </div>
        <div className="flex gap-1 items-center mt-1" style={{ flexWrap: 'wrap' }}>
          <span className={`chip ${settings?.output?.writable ? 'chip-ok' : 'chip-warn'}`}>
            {settings?.output?.writable ? '可写' : '不可写'}
          </span>
          <span className="chip">
            {{ env: '由环境变量提供', file: '由设置页配置', default: '默认目录' }[settings?.output?.source] || '默认目录'}
          </span>
          {settings?.output?.env_locked && (
            <span className="text-xs">
              🔒 由环境变量 <code className="text-xs">ECOMM_OUTPUT_DIR</code> 提供，修改后需重启
            </span>
          )}
        </div>
        {settings?.output?.error && (
          <div className="key-error" role="alert">路径不可用：{settings.output.error}</div>
        )}
        {outputErr && <div className="key-error" role="alert">{outputErr}</div>}
        {outputMsg && <div className="key-status ok" role="status">{outputMsg}</div>}
      </div>
      )}

      {/* 会话策略（B1：用户要求「审查/合规连续失败 N 次即停」可设置） */}
      {!settings?.redacted && (
      <div className="card mb-2">
        <h3 className="mb-1">🛑 会话策略（config/default.yaml 的 chat 段）</h3>
        <p className="text-sm mb-2">
          审查或合规**连续**未通过达到阈值时，会话自动停止——避免协调者反复重新生成图片、
          持续消耗额度（每次生图约 ¥1）。计数在任一环节通过时清零，人工 approve/retry 后也清零。
          <b>填 0 = 关闭该保护</b>（仅靠最大轮次兜底）。
        </p>
        <div className="flex gap-1 items-center flex-wrap">
          <label className="text-sm" htmlFor="max-consecutive-failures">连续失败即停</label>
          <input
            id="max-consecutive-failures"
            className="input"
            type="number"
            min="0"
            max="20"
            style={{ maxWidth: 110 }}
            aria-label="连续失败即停次数"
            disabled={chatBusy}
            value={chatPolicy.max_consecutive_review_failures}
            onChange={e => {
              setChatPolicy(p => ({ ...p, max_consecutive_review_failures: e.target.value }))
              setChatMsg(''); setChatErr('')
            }}
          />
          <span className="text-sm">次</span>
          <button type="button" className="btn btn-primary btn-sm"
                  aria-label="保存会话策略"
                  disabled={chatBusy}
                  onClick={handleSaveChatPolicy}>
            {chatBusy ? '保存中...' : '保存'}
          </button>
          <span className="text-xs text-muted">
            最大轮次 {settings?.chat?.max_turns ?? 15}｜会话 TTL {settings?.chat?.session_ttl_hours ?? 24} 小时
          </span>
        </div>

        {/* 提示词审核（用户 2026-09-18：让提示词更接近大众商品图审美） */}
        <div className="flex gap-2 items-center flex-wrap mt-2">
          <label className="text-sm" htmlFor="require-prompt-review">
            <input id="require-prompt-review" type="checkbox" aria-label="启用提示词审核"
                   checked={!!chatPolicy.require_prompt_review}
                   disabled={chatBusy}
                   onChange={e => {
                     setChatPolicy(p => ({ ...p, require_prompt_review: e.target.checked }))
                     setChatMsg(''); setChatErr('')
                   }} />
            {' '}出图前审核提示词（体检 + 审美改写）
          </label>
          <label className="text-sm" htmlFor="aesthetic-threshold">审美阈值</label>
          <input id="aesthetic-threshold" className="input" type="number" min="0" max="100"
                 style={{ maxWidth: 90 }} aria-label="审美阈值"
                 disabled={chatBusy || !chatPolicy.require_prompt_review}
                 value={chatPolicy.prompt_aesthetic_threshold ?? '85'}
                 onChange={e => {
                   setChatPolicy(p => ({ ...p, prompt_aesthetic_threshold: e.target.value }))
                   setChatMsg(''); setChatErr('')
                 }} />
          <label className="text-sm" htmlFor="review-rounds">重写轮数</label>
          <input id="review-rounds" className="input" type="number" min="0" max="3"
                 style={{ maxWidth: 80 }} aria-label="提示词重写轮数"
                 disabled={chatBusy || !chatPolicy.require_prompt_review}
                 value={chatPolicy.prompt_review_max_rounds ?? '1'}
                 onChange={e => {
                   setChatPolicy(p => ({ ...p, prompt_review_max_rounds: e.target.value }))
                   setChatMsg(''); setChatErr('')
                 }} />
          <label className="text-sm" htmlFor="require-prompt-confirm">
            <input id="require-prompt-confirm" type="checkbox" aria-label="提示词不达标时暂停"
                   checked={!!chatPolicy.require_prompt_confirm}
                   disabled={chatBusy}
                   onChange={e => {
                     setChatPolicy(p => ({ ...p, require_prompt_confirm: e.target.checked }))
                     setChatMsg(''); setChatErr('')
                   }} />
            {' '}提示词不达标时暂停等我确认
          </label>
        </div>
        <p className="text-xs text-muted mt-1">
          体检（缺槽位/身份词/包装版式复述/设计语言/反模式）是零成本的确定性检查；
          审美审核由「提示词审核优化员」按平面设计标准逐张打分，
          <b>低于阈值的槽位会给改写稿</b>（改写稿必须再过一遍体检才落地）。
        </p>

        {/* 风格词库（A79-A96：用户指定的「风格词库」，逐槽位注入设计档案） */}
        <div className="flex gap-2 items-center flex-wrap mt-2">
          <label className="text-sm" htmlFor="style-library-enabled">
            <input id="style-library-enabled" type="checkbox" aria-label="启用风格词库"
                   checked={!!chatPolicy.style_library_enabled}
                   disabled={chatBusy}
                   onChange={e => {
                     setChatPolicy(p => ({ ...p, style_library_enabled: e.target.checked }))
                     setChatMsg(''); setChatErr('')
                   }} />
            {' '}注入风格档案（「🎨 风格词库」里的设计原型与你的锚点）
          </label>
          <label className="text-sm" htmlFor="style-library-max">整套最多用</label>
          <input id="style-library-max" className="input" type="number" min="0" max="4"
                 style={{ maxWidth: 80 }} aria-label="整套最多用几种风格"
                 disabled={chatBusy || !chatPolicy.style_library_enabled}
                 value={chatPolicy.style_library_max ?? '1'}
                 onChange={e => {
                   setChatPolicy(p => ({ ...p, style_library_max: e.target.value }))
                   setChatMsg(''); setChatErr('')
                 }} />
          <span className="text-sm">种风格</span>
        </div>
        <p className="text-xs text-muted mt-1">
          档案按「品类 + 平台 + 槽位」逐张检索（`config/style_library.yaml` 内置 8 条 +
          你在「🎨 风格词库」里导入的），给<b>写提示词的人</b>与<b>审提示词的人</b>看；
          <b>不会写进最终提示词</b>（最终仍是六段式）。填 0 = 不注入。
          <b>1（默认）= 一轮会话只用你在词库里启用的那一套风格词</b>——参考套图没覆盖的槽位只按
          平台槽位契约写，不会再塞一套内置原型（此前每张图会被两套互相矛盾的档案同时指挥）。
          调到 2 以上才允许内置原型去补未覆盖的槽位（风格会混，属于你显式选择）。
        </p>
        {chatErr && <div className="key-error" role="alert">{chatErr}</div>}
        {chatMsg && <div className="key-status ok" role="status">{chatMsg}</div>}
      </div>
      )}

      {/* 计价（A94-A96：用户质疑"硬编码价格表会误导"） */}
      {!settings?.redacted && (
      <div className="card mb-2">
        <h3 className="mb-1">💰 计价（可选，写入 config/pricing.yaml）</h3>
        <p className="text-sm mb-2">
          <b>用量是事实，金额是估算</b>：会话页/审计里的张数、调用次数、耗时永远显示；
          金额只在<b>你填了单价</b>（或供应商回报了实际费用）时才显示，并带 <b>≈</b> 前缀。
          没填的模型一律显示"<b>未标定</b>"——<b>系统不会替你猜价格</b>
          （模型商改价时，改这里即可，不需要改代码）。
        </p>

        {pricing?.staleness?.length > 0 && (
          <div className="alert alert-error mb-1" role="alert">
            ⚠️ 价格可能已变化，建议重新标定：
            {pricing.staleness.slice(0, 3).map(item => (
              <span key={item.key || item.model} className="ml-1">
                {item.model}（{item.reason}）
              </span>
            ))}
          </div>
        )}

        {!pricing?.models?.length ? (
          <p className="text-sm text-muted">
            还沒有可标定的模型记录。跑过一次真实会话后，这里会自动列出<b>你实际用过的</b>路由与模型。
          </p>
        ) : (
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th>模型（你实际用过）</th><th>能力</th><th>用量口径</th>
                  <th>单价</th><th>状态</th>
                </tr>
              </thead>
              <tbody>
                {pricing.models.map(item => {
                  const key = item.key || item.model
                  return (
                    <tr key={key}>
                      <td className="mono text-sm">{item.route ? `${item.route}/` : ''}{item.model}</td>
                      <td className="text-sm">{item.capability}</td>
                      <td className="text-sm text-muted">
                        {item.capability === 'image' ? '按张' : '每 1M tokens'}
                      </td>
                      <td>
                        <input className="input" style={{ maxWidth: 120 }}
                               type="number" min="0" step="0.0001"
                               aria-label={`${item.model} 的单价`}
                               placeholder="留空 = 不估算"
                               value={priceInputs[key] ?? ''}
                               onChange={e => {
                                 setPriceInputs(p => ({ ...p, [key]: e.target.value }))
                                 setPriceMsg(''); setPriceErr('')
                               }} />
                      </td>
                      <td>
                        <span className={`badge ${item.priced ? 'badge-ok' : 'badge-warn'}`}>
                          {item.priced ? '已标定' : '未标定'}
                        </span>
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        )}

        <div className="flex gap-1 items-center flex-wrap mt-2">
          <button type="button" className="btn btn-primary btn-sm"
                  aria-label="保存价格表" disabled={priceBusy}
                  onClick={handleSavePricing}>
            {priceBusy ? '保存中...' : '保存价格'}
          </button>
          <span className="text-xs text-muted">
            单位：生图按<b>张</b>；文本/视觉按<b>每百万 tokens</b>（in 与 out 同价填写）
          </span>
        </div>

        <div className="flex gap-2 items-center flex-wrap mt-2">
          <label className="text-sm" htmlFor="calib-model">标定</label>
          <select id="calib-model" className="select" style={{ maxWidth: 260 }}
                  aria-label="标定模型"
                  value={calib.model}
                  onChange={e => {
                    const model = e.target.value
                    const found = (pricing?.models || []).find(m => m.model === model)
                    setCalib(p => ({ ...p, model, capability: found?.capability || 'image' }))
                    setPriceMsg(''); setPriceErr('')
                  }}>
            <option value="">选择模型…</option>
            {(pricing?.models || []).map(item => (
              <option key={item.key || item.model} value={item.model}>
                {item.route ? `${item.route}/` : ''}{item.model}
              </option>
            ))}
          </select>
          <label className="text-sm" htmlFor="calib-amount">本次实际花费</label>
          <input id="calib-amount" className="input" type="number" min="0" step="0.0001"
                 style={{ maxWidth: 110 }} aria-label="本次实际花费"
                 placeholder="控制台读数"
                 value={calib.actual}
                 onChange={e => { setCalib(p => ({ ...p, actual: e.target.value }))
                                  setPriceMsg(''); setPriceErr('') }} />
          <select className="select" style={{ maxWidth: 90 }} aria-label="标定币种"
                  value={calib.currency}
                  onChange={e => setCalib(p => ({ ...p, currency: e.target.value }))}>
            <option value="CNY">¥</option>
            <option value="USD">$</option>
          </select>
          <label className="text-sm" htmlFor="calib-count">／</label>
          <input id="calib-count" className="input" type="number" min="1" step="1"
                 style={{ maxWidth: 90 }} aria-label="标定张数"
                 value={calib.images}
                 onChange={e => setCalib(p => ({ ...p, images: e.target.value }))} />
          <span className="text-sm">
            {calib.capability === 'image' ? '张图' : '次调用'}
          </span>
          <button type="button" className="btn btn-ghost btn-sm"
                  aria-label="按实际花费标定" disabled={priceBusy}
                  onClick={handleCalibrate}>
            按实际花费反推单价
          </button>
        </div>
        <p className="text-xs text-muted mt-1">
          标定 = 用"实际花费 ÷ 实际用量"反推单价并写入（来源标为"实测标定"）。
          这是唯一能把估算变成可信数字的办法——<b>不要填猜的价格</b>。
        </p>
        {priceErr && <div className="key-error" role="alert">{priceErr}</div>}
        {priceMsg && <div className="key-status ok" role="status">{priceMsg}</div>}
      </div>
      )}

      {/* 生图质量策略（用户要求：文字/参考图/水印可设置） */}
      {!settings?.redacted && (
      <div className="card mb-2">
        <h3 className="mb-1">🖼️ 生图质量策略（写入 config/image.yaml）</h3>
        <p className="text-sm mb-2">
          生图是<b>文+图双条件</b>：参考图（你上传的真实商品图）负责商品身份，
          文本提示词负责场景与构图。<b>没有参考图时文字一律做干净虚化</b>——
          实测纯文生图会把包装上的品牌编错（`DEFOEBUENA®` → `NUTRIVA®`）。
        </p>
        <div className="flex gap-2 items-center flex-wrap mb-1">
          <label className="text-sm" htmlFor="text-strategy">包装文字</label>
          <select id="text-strategy" className="select" style={{ maxWidth: 220 }}
                  aria-label="文字策略"
                  value={imagePolicy.text_strategy}
                  onChange={e => setImagePolicy(p => ({ ...p, text_strategy: e.target.value }))}>
            <option value="preserve">逐字还原（靠参考图，不靠文字描述）</option>
            <option value="blur">干净虚化（交设计师后期贴图）</option>
            <option value="none">画面不出现任何文字</option>
          </select>

          <label className="text-sm" htmlFor="reference-mode">参考图</label>
          <select id="reference-mode" className="select" style={{ maxWidth: 200 }}
                  aria-label="参考图模式"
                  value={imagePolicy.reference_mode}
                  onChange={e => setImagePolicy(p => ({ ...p, reference_mode: e.target.value }))}>
            <option value="auto">有上传图就用（推荐）</option>
            <option value="off">关闭（纯文生图，对照实验用）</option>
          </select>

          <label className="text-sm" htmlFor="max-references">最多参考图</label>
          <input id="max-references" className="input" type="number" min="1" max="8"
                 style={{ maxWidth: 80 }} aria-label="参考图张数"
                 value={imagePolicy.max_references}
                 onChange={e => setImagePolicy(p => ({ ...p, max_references: e.target.value }))} />

          <label className="text-sm flex items-center gap-1">
            <input type="checkbox" aria-label="平台水印"
                   checked={imagePolicy.watermark}
                   onChange={e => setImagePolicy(p => ({ ...p, watermark: e.target.checked }))} />
            保留平台水印（默认关闭：<code className="text-xs">watermark:false</code> 可去掉右下角「AI生成」）
          </label>
        </div>

        {/* 信息图排版样式（本地绘制文字层；只有信息类槽位用到） */}
        <p className="text-sm mb-1 mt-1">
          <b>信息图排版</b>（卖点/功效/成分/人群/规格/用法/资质/对比 这些图上的文字由系统本地绘制）：
        </p>
        <div className="flex gap-2 items-center flex-wrap mb-1">
          <label className="text-sm" htmlFor="typo-scale">字号倍率</label>
          <input id="typo-scale" className="input" type="number" step="0.1" min="0.6" max="1.8"
                 style={{ maxWidth: 90 }} aria-label="字号倍率"
                 value={imagePolicy.font_scale}
                 onChange={e => setImagePolicy(p => ({ ...p, font_scale: e.target.value }))} />

          <label className="text-sm" htmlFor="typo-items">每图最多条目</label>
          <input id="typo-items" className="input" type="number" min="1" max="8"
                 style={{ maxWidth: 80 }} aria-label="每图最多条目"
                 value={imagePolicy.max_items}
                 onChange={e => setImagePolicy(p => ({ ...p, max_items: e.target.value }))} />

          <label className="text-sm" htmlFor="typo-brand">主色</label>
          <input id="typo-brand" type="color" aria-label="主色"
                 value={imagePolicy.brand_color}
                 onChange={e => setImagePolicy(p => ({ ...p, brand_color: e.target.value }))} />

          <label className="text-sm" htmlFor="typo-accent">辅色</label>
          <input id="typo-accent" type="color" aria-label="辅色"
                 value={imagePolicy.accent_color}
                 onChange={e => setImagePolicy(p => ({ ...p, accent_color: e.target.value }))} />

          <label className="text-sm flex items-center gap-1">
            <input type="checkbox" aria-label="显示页脚"
                   checked={imagePolicy.show_footer}
                   onChange={e => setImagePolicy(p => ({ ...p, show_footer: e.target.checked }))} />
            页脚显示品牌｜规格｜认证
          </label>
        </div>
        <div className="flex gap-1 items-center flex-wrap">
          <button type="button" className="btn btn-primary btn-sm" aria-label="保存生图质量策略"
                  disabled={imageBusy} onClick={handleSaveImagePolicy}>
            {imageBusy ? '保存中...' : '保存'}
          </button>
          <span className="text-xs text-muted">
            体检阈值：背景白度 ≥ {settings?.image?.quality?.edge_whiteness ?? 250}
            ｜身份相似度 ≥ {settings?.image?.quality?.identity_similarity_min ?? 0.45}
            ｜套图每槽 {settings?.image?.slot_candidates ?? 1} 张
          </span>
        </div>
        {imageErr && <div className="key-error" role="alert">{imageErr}</div>}
        {imageMsg && <div className="key-status ok" role="status">{imageMsg}</div>}
      </div>
      )}

      <div className="grid-2" style={{ alignItems: 'start' }}>
        {/* 租户独立 API Key（C2 方案①：每租户一把钥匙） */}
        {!settings?.redacted && (
        <div className="card">
          <h3 className="mb-1">🏢 租户 API Key（每租户独立钥匙）</h3>
          <p className="text-sm mb-2" style={{ color: '#94a3b8' }}>
            每个租户一把独立 Key：持租户 Key 的请求身份绑定该租户（<code className="text-xs">X-Tenant-ID</code> 声明会被忽略，防冒充），
            且无权访问管理端点。持久化到 <code className="text-xs">config/tenant_keys.yaml</code>（已 gitignore）。
          </p>
          {tenantKeys.length === 0 && (
            <p className="text-sm text-muted">暂无已注册租户（只有 default）。配置 ECOMM_TENANTS 后可在此分发租户 Key。</p>
          )}
          {tenantKeys.map(t => (
            <div key={t.tenant_id} className="flex gap-1 items-center" style={{ marginBottom: 8, flexWrap: 'wrap' }}>
              <span className={`dot ${t.source === 'env' ? 'dot-info' : t.configured ? 'dot-ok' : 'dot-idle'}`} aria-hidden="true" />
              <span className="text-sm mono" style={{ minWidth: 96 }}>{t.tenant_id}</span>
              <span className="chip">{t.tier}</span>
              <span className="provider-state">{t.configured ? '已配置' : '未配置'}</span>
              {t.source === 'env' ? (
                <span className="text-xs" style={{ color: '#93c5fd', alignSelf: 'center' }}>
                  🔒 由 <code className="text-xs">ECOMM_TENANT_KEYS</code> 环境变量提供（修改需改环境变量并重启）
                </span>
              ) : (
                <>
                  <input
                    className="input"
                    type="password"
                    autoComplete="off"
                    aria-label={`租户 ${t.tenant_id} 的 Key`}
                    placeholder={t.configured ? '输入新 Key 轮换，留空不修改' : '为租户创建 Key（≥16 字符）'}
                    value={tenantInputs[t.tenant_id] ?? ''}
                    onChange={e => setTenantInputs(prev => ({ ...prev, [t.tenant_id]: e.target.value }))}
                    style={{ maxWidth: 260 }}
                  />
                  <button
                    type="button"
                    className="btn btn-primary btn-sm"
                    disabled={!(tenantInputs[t.tenant_id] ?? '').trim()}
                    onClick={() => handleTenantKey(t.tenant_id, (tenantInputs[t.tenant_id] ?? '').trim())}
                  >
                    {t.configured ? '保存/轮换' : '创建'}
                  </button>
                  {t.configured && !tenantConfirm[t.tenant_id] && (
                    <button
                      type="button"
                      className="btn btn-danger btn-sm"
                      onClick={() => setTenantConfirm(prev => ({ ...prev, [t.tenant_id]: true }))}
                    >
                      删除
                    </button>
                  )}
                  {t.configured && tenantConfirm[t.tenant_id] && (
                    <>
                      <span className="text-xs">确认删除 <b>{t.tenant_id}</b> 的 Key？</span>
                      <button type="button" className="btn btn-danger btn-sm"
                              onClick={() => {
                                setTenantConfirm(prev => ({ ...prev, [t.tenant_id]: false }))
                                handleTenantKey(t.tenant_id, '')
                              }}>
                        确认删除
                      </button>
                      <button type="button" className="btn btn-ghost btn-sm"
                              onClick={() => setTenantConfirm(prev => ({ ...prev, [t.tenant_id]: false }))}>
                        取消
                      </button>
                    </>
                  )}
                  {tenantMsg[t.tenant_id] && (
                    <span className="text-sm" role="status"
                          style={{ color: tenantMsg[t.tenant_id].startsWith('✅') ? '#4ade80' : '#f87171', alignSelf: 'center' }}>
                      {tenantMsg[t.tenant_id]}
                    </span>
                  )}
                </>
              )}
            </div>
          ))}
        </div>
        )}

        <div className="flex flex-col gap-2">
          {/* 前端 API Key（浏览器本地，审计修复：生产启用鉴权后前端需携带 Key） */}
          <div className="card">
            <h3 className="mb-1">🔐 前端 API Key（浏览器本地存储）</h3>
            <p className="text-sm mb-2" style={{ color: '#94a3b8' }}>
              后端配置 <code className="text-xs">ECOMM_API_KEY</code> 后，所有请求需携带 Key。在此填入同一把 Key 保存在浏览器 localStorage，
              前端将自动附加 <code className="text-xs">X-API-Key</code> 头与 WS 的 <code className="text-xs">?api_key=</code> 参数。开发模式（未配置 Key）可留空。
            </p>
            <div className="flex gap-1">
              <input
                className="input" type="password" autoComplete="off"
                aria-label="前端 API Key"
                placeholder="粘贴 ECOMM_API_KEY（与后端一致）"
                value={frontendKey}
                onChange={e => setFrontendKey(e.target.value)}
                style={{ maxWidth: 420 }}
              />
              <button type="button" className="btn btn-primary btn-sm" onClick={() => {
                setStoredApiKey(frontendKey.trim())
                setFrontendMsg(frontendKey.trim() ? '✅ 已保存到浏览器，后续请求自动携带' : '✅ 已清除')
              }}>保存</button>
            </div>
            {frontendMsg && (
              <div className="text-sm mt-1" role="status" style={{ color: '#4ade80' }}>{frontendMsg}</div>
            )}
          </div>

          {/* 运行信息：CORS + 当前可用 Provider */}
          <div className="card">
            <h3 className="mb-1">⚙️ 运行信息</h3>
            <div className="kv">
              <span className="k">可用 Provider</span>
              <span className="v flex flex-wrap gap-1">
                {(settings?.providers || []).map(p => (
                  <span key={p.name} className="chip chip-ok">
                    {p.name} · {p.capabilities.join('/')}
                  </span>
                ))}
                {(settings?.providers || []).length === 0 && <span className="chip">无</span>}
              </span>
            </div>
            <div className="kv mt-1">
              <span className="k">CORS 白名单</span>
              <span className="v flex flex-wrap gap-1">
                {(settings?.cors_origins || []).map(o => <span key={o} className="chip mono">{o}</span>)}
              </span>
            </div>
            <p className="text-xs mt-1 text-muted">CORS 通过环境变量 ECOMM_CORS_ORIGINS 配置。</p>
          </div>
        </div>
      </div>
    </div>
  )
}
