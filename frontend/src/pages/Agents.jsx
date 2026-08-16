import { useState, useEffect, useCallback } from 'react'
import { getSettings, saveAgentParams } from '../api'

export default function Agents() {
  const [agents, setAgents] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [expanded, setExpanded] = useState(null)

  const refresh = useCallback(async () => {
    try {
      const data = await getSettings()
      setAgents(data?.agents || [])
      setError('')
    } catch (err) {
      setError(err.message || '加载 Agent 配置失败')
    }
    setLoading(false)
  }, [])

  useEffect(() => { refresh() }, [refresh])

  if (loading) {
    return (
      <div className="card text-center" style={{ minHeight: 200, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
        <div><div className="spinner" /><p className="text-sm mt-2">加载 Agent 配置...</p></div>
      </div>
    )
  }

  if (error && !agents.length) {
    return <div className="card card-error mt-2"><h3 className="mb-1">加载失败</h3><p className="text-sm">{error}</p></div>
  }

  return (
    <div>
      <h1 className="page-title">Agent 配置</h1>
      <p className="page-sub">
        共 {agents.length} 个已注册 Agent。修改参数会写入 <code className="text-xs">config/agents/*.yaml</code> 并立即重载生效。
        每个 Agent 使用哪个模型（🧠 徽章）由「系统设置 → 模型映射」决定。
      </p>

      <div className="grid-2" style={{ alignItems: 'start' }}>
        {agents.map(a => (
          <AgentCard
            key={a.name}
            agent={a}
            expanded={expanded === a.name}
            onToggle={() => setExpanded(expanded === a.name ? null : a.name)}
            onSaved={refresh}
          />
        ))}
      </div>
    </div>
  )
}

function AgentCard({ agent, expanded, onToggle, onSaved }) {
  const [values, setValues] = useState({})
  const [saving, setSaving] = useState(false)
  const [msg, setMsg] = useState('')

  function initialValues() {
    const v = {}
    for (const p of agent.params || []) v[p.key] = p.default
    return v
  }

  async function handleSave() {
    setSaving(true)
    setMsg('')
    try {
      await saveAgentParams(agent.name, values)
      setMsg('✅ 已保存并重载')
      onSaved()
    } catch (err) {
      setMsg('❌ ' + (err.message || '保存失败'))
    }
    setSaving(false)
  }

  function setParamValue(key, val) {
    setValues(prev => ({ ...prev, [key]: val }))
  }

  return (
    <div className="card">
      <div className="flex items-center justify-between mb-1">
        <h3 style={{ marginBottom: 0 }}>{agent.name}</h3>
        <button className="btn btn-ghost btn-sm" onClick={onToggle}>{expanded ? '收起' : '配置'}</button>
      </div>
      <p className="text-sm mb-1">{agent.description}</p>
      <div className="flex flex-wrap gap-1 mb-1">
        {agent.requires.map(r => <span key={r} className="badge badge-info">{r}</span>)}
        {agent.resolved_model && (
          <span className="badge badge-ok" title="当前生效模型（config/models.yaml 解析）">
            🧠 {agent.resolved_model}
          </span>
        )}
        <span className="badge badge-muted">v{agent.version}</span>
        <span className="badge badge-muted">超时 {agent.timeout_ms}ms</span>
        {agent.retry?.max_retries && <span className="badge badge-muted">重试 {agent.retry.max_retries} 次</span>}
      </div>
      <p className="text-xs text-muted">配置文件: config/agents/{agent.config_file}.yaml</p>

      {expanded && (
        <div className="mt-2">
          {(agent.params || []).length === 0 ? (
            <p className="text-sm">该 Agent 无可配置参数。</p>
          ) : (
            (agent.params || []).map(p => (
              <ParamField
                key={p.key}
                param={p}
                value={values[p.key] !== undefined ? values[p.key] : p.default}
                onChange={v => setParamValue(p.key, v)}
              />
            ))
          )}
          {msg && <div className={`alert ${msg.startsWith('✅') ? 'alert-success' : 'alert-error'} mt-1`}>{msg}</div>}
          {(agent.params || []).length > 0 && (
            <button className="btn btn-primary btn-sm mt-1" onClick={handleSave} disabled={saving}>
              {saving ? '保存中...' : '保存配置'}
            </button>
          )}
        </div>
      )}
    </div>
  )
}

function ParamField({ param, value, onChange }) {
  const label = (
    <label className="label">
      {param.label}
      <span className="ml-1 text-xs" style={{ color: '#475569' }}>({param.key})</span>
    </label>
  )

  if (param.type === 'select' && param.options?.length) {
    return (
      <div className="form-group">
        {label}
        <select className="select" value={value ?? ''} onChange={e => onChange(e.target.value)}>
          {(param.options || []).map(o => <option key={o} value={o}>{o}</option>)}
        </select>
      </div>
    )
  }

  if (param.type === 'multi_select' && param.options?.length) {
    const current = Array.isArray(value) ? value : []
    return (
      <div className="form-group">
        {label}
        <div className="flex flex-wrap gap-1">
          {(param.options || []).map(o => {
            const checked = current.includes(o)
            return (
              <label key={o} className="badge" style={{
                cursor: 'pointer',
                background: checked ? '#1e3a5f' : '#0f172a',
                border: `1px solid ${checked ? '#3b82f6' : '#334155'}`,
                color: checked ? '#93c5fd' : '#94a3b8',
              }}>
                <input type="checkbox" style={{ marginRight: 4 }}
                  checked={checked}
                  onChange={() => onChange(checked ? current.filter(x => x !== o) : [...current, o])} />
                {o}
              </label>
            )
          })}
        </div>
      </div>
    )
  }

  return (
    <div className="form-group">
      {label}
      <input
        className="input"
        value={typeof value === 'string' || typeof value === 'number' ? value : JSON.stringify(value)}
        onChange={e => {
          const raw = e.target.value
          if (typeof param.default === 'number') onChange(Number(raw) || 0)
          else onChange(raw)
        }}
      />
    </div>
  )
}
