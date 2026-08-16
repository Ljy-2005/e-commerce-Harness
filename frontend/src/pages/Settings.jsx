import { useState, useEffect, useCallback } from 'react'
import { getSettings, saveApiKeys } from '../api'
import ModelMappingEditor from '../components/ModelMappingEditor'

export default function Settings() {
  const [settings, setSettings] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [keyInputs, setKeyInputs] = useState({})   // env → 新输入的明文
  const [saving, setSaving] = useState(false)
  const [msg, setMsg] = useState('')
  const [showModels, setShowModels] = useState(false)

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

  async function handleSave() {
    setSaving(true)
    setMsg('')
    try {
      const payload = {}
      for (const [env, val] of Object.entries(keyInputs)) {
        if (val !== undefined && val !== null) payload[env] = val
      }
      if (!Object.keys(payload).length) {
        setMsg('⚠ 没有修改任何 Key')
        setSaving(false)
        return
      }
      await saveApiKeys(payload)
      setKeyInputs({})
      setMsg('✅ 已保存并立即生效（Provider 注册表已重载）')
      refresh()
    } catch (err) {
      setMsg('❌ ' + (err.message || '保存失败'))
    }
    setSaving(false)
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

  return (
    <div>
      <h1 className="page-title">系统设置</h1>
      <p className="page-sub">配置各 AI 服务商的 API Key，保存后立即生效并持久化到 <code className="text-xs">config/secrets.yaml</code>（已加入 .gitignore）</p>

      {/* Mock 模式提示 */}
      <div className={`alert ${settings?.mock_mode ? 'alert-info' : 'alert-success'} mb-2`}>
        {settings?.mock_mode
          ? '💡 当前为 Mock 模式：未配置任何 API Key，所有 Agent 使用模板数据运行（可完整验证流程）。配置下方任意 Key 后自动切换真实 API。'
          : '✅ 已配置 API Key，Agent 将调用真实模型服务。'}
      </div>

      <div className="grid-2" style={{ alignItems: 'start' }}>
        {/* API Key 表 */}
        <div className="card">
          <div className="flex items-center justify-between mb-1">
            <h3 style={{ marginBottom: 0 }}>🔑 API Key（{apiKeys.filter(k => k.configured).length}/{apiKeys.length} 已配置）</h3>
            <button className="btn btn-primary btn-sm" onClick={handleSave} disabled={saving}>
              {saving ? '保存中...' : '保存全部修改'}
            </button>
          </div>
          {msg && <div className={`alert ${msg.startsWith('✅') ? 'alert-success' : msg.startsWith('❌') ? 'alert-error' : 'alert-warn'} mt-1`}>{msg}</div>}
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr><th>服务商</th><th>能力</th><th>状态</th><th>密钥（留空 = 不修改）</th></tr>
              </thead>
              <tbody>
                {apiKeys.map(k => (
                  <tr key={k.env}>
                    <td>
                      <div className="strong">{k.name}</div>
                      <div className="text-xs mono">{k.env}</div>
                    </td>
                    <td className="text-xs">{k.capabilities}</td>
                    <td>
                      {k.configured
                        ? <span className="badge badge-ok">已配置 {k.masked}</span>
                        : <span className="badge badge-muted">未配置</span>}
                    </td>
                    <td>
                      <input
                        className="input"
                        type="password"
                        placeholder={k.configured ? `${k.masked}（输入新值覆盖，留空不修改）` : '粘贴 API Key'}
                        autoComplete="off"
                        value={keyInputs[k.env] ?? ''}
                        onChange={e => setKeyInputs(prev => ({ ...prev, [k.env]: e.target.value }))}
                      />
                      {k.configured && (
                        <button
                          className="btn btn-danger btn-sm mt-1"
                          onClick={() => setKeyInputs(prev => ({ ...prev, [k.env]: '' }))}
                        >
                          清除此 Key
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="text-xs mt-1 text-muted">说明：「清除此 Key」会在保存时生效。留空的输入框不会被提交。</p>
        </div>

        <div className="flex flex-col gap-2">
          {/* Provider 状态 */}
          <div className="card">
            <h3 className="mb-1">当前可用 Provider</h3>
            <div className="flex flex-wrap gap-1">
              {(settings?.providers || []).map(p => (
                <span key={p.name} className="badge badge-ok">
                  {p.name} <span className="text-xs">({p.capabilities.join('/')})</span>
                </span>
              ))}
              {(settings?.providers || []).length === 0 && <span className="badge badge-muted">无</span>}
            </div>
          </div>

          {/* 模型映射（可编辑） */}
          <div className="card">
            <div className="flex items-center justify-between mb-1">
              <h3 style={{ marginBottom: 0 }}>🔀 模型映射（config/models.yaml）</h3>
              <button className="btn btn-ghost btn-sm" onClick={() => setShowModels(!showModels)}>
                {showModels ? '收起原始 JSON' : '查看原始 JSON'}
              </button>
            </div>
            <p className="text-sm mb-2">
              Agent 只声明能力（vision/text/image），具体用哪个模型由此处决定。
              格式 <code className="text-xs">provider/model</code>，例如 <code className="text-xs">deepseek/deepseek-chat</code>。
            </p>
            {showModels && (
              <pre className="text-xs mb-2" style={{ background: '#0f172a', padding: 10, borderRadius: 6, overflowX: 'auto', color: '#94a3b8' }}>
                {JSON.stringify(settings?.models_config || {}, null, 2)}
              </pre>
            )}
            <ModelMappingEditor settings={settings} onSaved={refresh} />
          </div>

          {/* CORS */}
          <div className="card">
            <h3 className="mb-1">CORS 白名单</h3>
            <div className="flex flex-wrap gap-1">
              {(settings?.cors_origins || []).map(o => <span key={o} className="badge badge-muted mono">{o}</span>)}
            </div>
            <p className="text-xs mt-1 text-muted">通过环境变量 ECOMM_CORS_ORIGINS 配置。</p>
          </div>
        </div>
      </div>
    </div>
  )
}
