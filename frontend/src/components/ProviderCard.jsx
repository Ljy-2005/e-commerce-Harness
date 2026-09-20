import { useEffect, useRef, useState } from 'react'
import CredentialField from './CredentialField'

/** 路由级状态点（凭据任一已配置 + 注册表是否真的注册了该路由） */
export function routeStatus({ configured, available }) {
  if (configured && available) return { dot: 'dot-ok', text: '已配置 · 生效中' }
  if (configured) return { dot: 'dot-warn', text: '已配置 · 未激活' }
  return { dot: 'dot-idle', text: '未配置' }
}

const MODEL_ID_OK = /^[\x21-\x7E]+$/

/** 模型 id 列表校验（镜像后端规则：可打印 ASCII、非空、无空格） */
export function modelListFailure(models) {
  for (const m of models) {
    if (!m) continue
    if (!MODEL_ID_OK.test(m)) return `模型 id 含非法字符（仅可打印 ASCII，无空格）：${m.slice(0, 30)}`
  }
  return ''
}

/**
 * 一个 Provider 一行 + 展开的编辑卡片（仿 DSH Models 页）。
 *
 * 卡片包含两部分：
 * 1. 凭据槽（该 Provider 的 1~3 个环境变量，各自只写、独立保存/清除）；
 * 2. 「自定义设置」折叠区 —— 端点（base URL）与该 Provider 的模型目录
 *    （第三方 coding plan / 代理 / 自建网关所需）。
 */
export default function ProviderCard({
  provider, expanded, onToggle, onSaveKey, onClearKey, onSaveConfig, onTest, onDelete,
  onMoveCredential, onPullModels,
}) {
  const [models, setModels] = useState(provider.models || [])
  const [baseUrl, setBaseUrl] = useState(provider.effective_base_url || '')
  const [busy, setBusy] = useState(false)
  const [feedback, setFeedback] = useState(null)
  const [testing, setTesting] = useState(false)
  const [testResult, setTestResult] = useState(null)
  const [allowImage, setAllowImage] = useState(false)
  const [confirmDelete, setConfirmDelete] = useState(false)
  const [pulling, setPulling] = useState(false)
  const [pulled, setPulled] = useState(null)

  // 保存后父级刷新 → 同步外部值；但**不覆盖用户正在编辑的草稿**：
  // 仅当本地与服务端基线一致（无未保存改动）时才采纳新值（第二轮审计修复：
  // 此前任何一次 setSettings（如保存凭据）都会把正在编辑的模型/端点静默回滚）
  const modelsRef = useRef(models)
  modelsRef.current = models
  const baseUrlRef = useRef(baseUrl)
  baseUrlRef.current = baseUrl
  const syncedModels = useRef(JSON.stringify(provider.models || []))
  const syncedBaseUrl = useRef(provider.effective_base_url || '')

  useEffect(() => {
    const incoming = JSON.stringify(provider.models || [])
    if (incoming === syncedModels.current) return           // 服务端未变
    const local = JSON.stringify(modelsRef.current.map(m => m.trim()).filter(Boolean))
    if (local === syncedModels.current) setModels(provider.models || [])  // 本地干净 → 采纳
    syncedModels.current = incoming                          // 基线前移（草稿保留）
  }, [provider.models])

  useEffect(() => {
    const incoming = provider.effective_base_url || ''
    if (incoming === syncedBaseUrl.current) return
    if (baseUrlRef.current === syncedBaseUrl.current) setBaseUrl(incoming)
    syncedBaseUrl.current = incoming
  }, [provider.effective_base_url])

  const status = routeStatus(provider)
  const envLocked = provider.base_url_source === 'env'
  const modelError = modelListFailure(models)
  const officialFallback = provider.default_base_url || ''
  const baseUrlDirty = provider.base_url_supported && !envLocked &&
    (baseUrl.trim().replace(/\/+$/, '') !== (provider.effective_base_url || ''))
  const modelsDirty = JSON.stringify(models.filter(m => m.trim())) !== JSON.stringify(provider.models || [])
  const canSave = !busy && !modelError && (baseUrlDirty || modelsDirty)

  async function saveConfig() {
    if (!canSave) return
    setBusy(true)
    setFeedback(null)
    const payload = { models: models.map(m => m.trim()).filter(Boolean) }
    if (provider.base_url_supported && !envLocked) payload.base_url = baseUrl.trim()
    try {
      await onSaveConfig(provider.route, payload)
      setFeedback({ ok: true, text: '已保存并立即生效（Provider 注册表已重载）' })
    } catch (err) {
      setFeedback({ ok: false, text: err?.message || '保存失败' })
    }
    setBusy(false)
  }

  // 仅图像能力的路由（Seedream/FLUX）：测试连接要走真实生图，默认跳过
  const caps = provider.capabilities || ''
  const imageOnly = caps.includes('image') && !caps.includes('vision') && !caps.includes('text')

  async function runTest() {
    if (!onTest || testing) return
    setTesting(true)
    setTestResult(null)
    try {
      setTestResult(await onTest(provider.route, { allow_image: allowImage }))
    } catch (err) {
      setTestResult({ ok: false, detail: err?.message || '测试请求失败' })
    }
    setTesting(false)
  }

  /** 拉取该服务商账号实际可用的模型（模型 id 不必再猜） */
  async function pullModels() {
    if (!onPullModels || pulling) return
    setPulling(true)
    setPulled(null)
    try {
      setPulled(await onPullModels(provider.route))
    } catch (err) {
      setPulled({ ok: false, detail: err?.message || '拉取失败' })
    }
    setPulling(false)
  }

  /** 把拉取到的模型追加进模型目录（去重） */
  function appendModel(id) {
    setModels(prev => (prev.includes(id) ? prev : [...prev.map(m => m.trim()).filter(Boolean), id]))
    setFeedback(null)
  }

  return (
    <div className={`provider-row${expanded ? ' open' : ''}`}>
      <button type="button" className="provider-row-head" aria-expanded={expanded} onClick={onToggle}>
        <span className={`dot ${status.dot}`} aria-hidden="true" />
        <span className="provider-name">{provider.label}</span>
        <span className="provider-env">{provider.route}</span>
        {provider.base_url_custom && (
          <span className="chip chip-warn" title={provider.effective_base_url}>自定义端点</span>
        )}
        {provider.custom && (
          <span className="chip chip-info" title={`自定义服务商（${provider.kind || 'openai'} 兼容）`}>
            自定义服务商
          </span>
        )}
        {provider.deprecated && (
          <span className="chip chip-warn" title={provider.deprecated_hint}>旧版通道</span>
        )}
        {(provider.models || []).length > 0 && (
          <span className="chip chip-ok">自定义模型 ×{provider.models.length}</span>
        )}
        <span className="provider-spacer" />
        <span className="provider-chips">
          {(provider.capabilities || '').split('/').map(c => c.trim()).filter(Boolean).map(c => (
            <span key={c} className="chip">{c}</span>
          ))}
        </span>
        <span className="provider-state">{status.text}</span>
        <span className="provider-chevron" aria-hidden="true">▾</span>
      </button>

      {expanded && (
        <div className="provider-card">
          {/* 0. 旧版通道引导（用户实测：把方舟 Key 填进了旧签名卡片的 AK 槽） */}
          {provider.deprecated && provider.deprecated_hint && (
            <div className="alert alert-warn mb-2" role="note" style={{ marginBottom: 8 }}>
              ⚠️ <b>旧版通道</b>：{provider.deprecated_hint}
            </div>
          )}

          {/* 1. 凭据 */}
          <div className="card-section-title">凭据</div>
          {provider.credential_hint && <div className="key-hint mb-1">{provider.credential_hint}</div>}
          {provider.credentials.map(c => (
            <CredentialField key={c.env} item={c} onSave={onSaveKey} onClear={onClearKey} />
          ))}

          {/* 1.5 测试连接（B3-24）：1 次最小调用验证 Key/端点/模型真的可用 */}
          {onTest && (
            <div className="provider-test">
              <div className="flex gap-1 items-center flex-wrap">
                <button
                  type="button"
                  className="btn btn-ghost btn-sm"
                  disabled={testing}
                  onClick={runTest}
                >
                  {testing ? '测试中...' : '测试连接'}
                </button>
                {imageOnly && (
                  <label className="text-xs flex items-center" style={{ gap: 4 }}>
                    <input
                      type="checkbox"
                      checked={allowImage}
                      onChange={e => setAllowImage(e.target.checked)}
                    />
                    包含生图测试（会产生费用）
                  </label>
                )}
                <span className="text-xs">
                  发起 1 次最小调用，验证端点与模型真的可用（第三方 coding plan 排错用）
                </span>
              </div>
              {testResult && (
                <div
                  className={`key-status ${testResult.ok ? 'ok' : testResult.skipped ? 'warn' : 'err'}`}
                  role="status"
                  aria-live="polite"
                >
                  {testResult.ok
                    ? `✅ 连接正常 · ${testResult.model || '默认模型'} · ${testResult.latency_ms}ms`
                    : testResult.skipped
                      ? `⏭ ${testResult.reason || '已跳过'}`
                      : `❌ ${testResult.detail || '连接失败'}`}
                </div>
              )}
              {/* Key 放错槽位时给一键迁移（用户实测场景：方舟 Key 填进了旧签名卡片） */}
              {testResult?.suggested?.from_env && onMoveCredential && (
                <div className="flex gap-1 items-center mt-1" style={{ flexWrap: 'wrap' }}>
                  <button type="button" className="btn btn-primary btn-sm"
                          aria-label="迁移凭据到正确的服务商"
                          onClick={() => onMoveCredential(testResult.suggested.from_env,
                                                          testResult.suggested.env)}>
                    把该 Key 迁移到「{testResult.suggested.route}」
                  </button>
                  <span className="text-xs">
                    迁移 = 写入 {testResult.suggested.env} 并清空
                    {testResult.suggested.from_env}（密钥不会回显）
                  </span>
                </div>
              )}
            </div>
          )}

          {/* 2. 自定义设置：端点 + 模型目录 */}
          <details className="key-advanced">
            <summary>自定义设置（端点与模型）</summary>
            <div className="key-advanced-body">
              {provider.base_url_supported ? (
                <div>
                  <label className="label" htmlFor={`baseurl-${provider.route}`}>API 端点（Base URL）</label>
                  <div className="flex gap-1 items-center flex-wrap">
                    <input
                      id={`baseurl-${provider.route}`}
                      className="input"
                      style={{ maxWidth: 420 }}
                      autoComplete="off"
                      spellCheck="false"
                      disabled={envLocked}
                      placeholder={officialFallback}
                      value={baseUrl}
                      onChange={e => { setBaseUrl(e.target.value); setFeedback(null) }}
                    />
                    <button
                      type="button"
                      className="btn btn-ghost btn-sm"
                      disabled={envLocked || busy || !baseUrl}
                      onClick={() => { setBaseUrl(''); setFeedback(null) }}
                    >
                      恢复官方端点
                    </button>
                  </div>
                  {envLocked ? (
                    <div className="key-hint">
                      🔒 该端点由环境变量 <code className="text-xs">{provider.route.toUpperCase()}_BASE_URL</code> 提供，
                      修改环境变量后重启方可变更。
                    </div>
                  ) : (
                    <div className="key-hint">
                      留空 = 使用官方端点 <code className="text-xs">{officialFallback}</code>。
                      使用第三方 coding plan / 中转时填其 OpenAI（或 Anthropic）兼容端点，例如
                      <code className="text-xs"> https://your-proxy.example.com/v1</code>。
                    </div>
                  )}
                </div>
              ) : (
                <div className="key-hint">
                  该 Provider 使用官方固定端点（多上游签名），端点在 <code className="text-xs">src/providers/</code> 中固定；
                  下方模型列表仍可自定义。
                </div>
              )}

              <div>
                <span className="label">模型目录（该 Provider 的模型 id）</span>
                {models.length === 0 && (
                  <div className="key-hint">未自定义：使用内置目录（
                    {(provider.official_models || []).slice(0, 3).join(', ')}
                    {(provider.official_models || []).length > 3 ? ' …' : ''}）
                  </div>
                )}
                {models.map((m, i) => (
                  <div key={i} className="flex gap-1 items-center" style={{ marginBottom: 6 }}>
                    <input
                      className="input"
                      style={{ maxWidth: 420 }}
                      autoComplete="off"
                      spellCheck="false"
                      aria-label={`模型 id ${i + 1}`}
                      placeholder="例如 gpt-5-codex / claude-sonnet-4-5-20250929"
                      value={m}
                      onChange={e => {
                        const next = [...models]
                        next[i] = e.target.value
                        setModels(next)
                        setFeedback(null)
                      }}
                    />
                    <button
                      type="button"
                      className="btn btn-ghost btn-sm"
                      aria-label={`移除模型 ${i + 1}`}
                      onClick={() => { setModels(models.filter((_, j) => j !== i)); setFeedback(null) }}
                    >
                      移除
                    </button>
                  </div>
                ))}
                <div className="flex gap-1 items-center">
                  <button
                    type="button"
                    className="btn btn-ghost btn-sm"
                    onClick={() => { setModels([...models, '']); setFeedback(null) }}
                  >
                    + 添加模型
                  </button>
                  {onPullModels && (provider.kind || 'openai') === 'openai' && (
                    <button type="button" className="btn btn-ghost btn-sm"
                            disabled={pulling} onClick={pullModels}>
                      {pulling ? '拉取中...' : '⬇️ 拉取可用模型'}
                    </button>
                  )}
                  <span className="text-xs">
                    自定义模型会进入「模型映射」的建议列表，可直接设为某能力的默认模型
                  </span>
                </div>

                {/* 拉取结果：模型 id 形态各家不同（方舟是「名称-日期」），点一下即填入 */}
                {pulled && (
                  <div className="model-pull" role="status">
                    {pulled.ok ? (() => {
                      const byId = Object.fromEntries((pulled.models || []).map(m => [m.id, m]))
                      const skipped = (pulled.models || []).filter(m => m.skip_reason).length
                      const usable = (pulled.models || []).filter(m => m.recommended).length
                      return (
                        <>
                          <div className="text-xs">
                            该账号可见 <b>{pulled.total}</b> 个模型，可推荐 <b>{usable}</b> 个
                            {skipped ? `（已过滤 ${skipped} 个即将下线/专用模型）` : ''}，点击即填入：
                          </div>
                          {[['image', '生图'], ['vision', '视觉'], ['text', '文本']].map(([cap, cn]) => {
                            const ids = pulled.applicable?.[cap] || []
                            if (!ids.length || !(provider.capabilities || '').includes(cap)) return null
                            return (
                              <div key={cap} className="flex gap-1 items-center flex-wrap mt-1">
                                <span className="text-xs">{cn}：</span>
                                {ids.slice(0, 12).map(id => {
                                  const meta = byId[id] || {}
                                  return (
                                    <button key={id} type="button" className="chip chip-clickable"
                                            title={`${id}${meta.version ? `（${meta.version}）` : ''} — 点击填入`}
                                            onClick={() => appendModel(id)}>
                                      {meta.name || id}
                                    </button>
                                  )
                                })}
                              </div>
                            )
                          })}
                          <div className="key-hint">
                            显示的是模型名，填入的是完整 id（「名称-日期」形态）；保存后生图能力优先用你填的第一个
                          </div>
                        </>
                      )
                    })() : (
                      <div className="key-error">{pulled.detail || '拉取失败'}</div>
                    )}
                  </div>
                )}
                {modelError && <div className="key-error" role="alert">{modelError}</div>}
              </div>

              <div className="flex gap-1 items-center">
                <button type="button" className="btn btn-primary btn-sm" disabled={!canSave} onClick={saveConfig}>
                  {busy ? '应用中...' : '保存自定义设置'}
                </button>
                <span className="text-xs">持久化到 config/providers.yaml（已 gitignore）</span>
              </div>

              {feedback && (
                <div className={`key-status ${feedback.ok ? 'ok' : 'err'}`} role="status" aria-live="polite">
                  {feedback.ok ? '✅ ' : '❌ '}{feedback.text}
                </div>
              )}
            </div>
          </details>

          {/* 3. 自定义服务商：整条删除（含其凭据变量与模型目录） */}
          {onDelete && (
            <div className="provider-danger">
              {confirmDelete ? (
                <>
                  <span className="text-sm">删除服务商 <b>{provider.label}</b>？其凭据变量与模型目录将一并移除。</span>
                  <button type="button" className="btn btn-danger btn-sm"
                          onClick={() => { setConfirmDelete(false); onDelete(provider.route) }}>
                    确认删除
                  </button>
                  <button type="button" className="btn btn-ghost btn-sm"
                          onClick={() => setConfirmDelete(false)}>
                    取消
                  </button>
                </>
              ) : (
                <button type="button" className="btn btn-danger btn-sm"
                        onClick={() => setConfirmDelete(true)}>
                  删除该服务商
                </button>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  )
}
