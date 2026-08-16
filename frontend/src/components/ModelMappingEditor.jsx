import { useEffect, useState } from 'react'
import { saveModels } from '../api'

const CAP_LABELS = { vision: '视觉（vision）', text: '文本（text）', image: '生图（image）', local: '本地（local）' }

/**
 * 能力→模型映射编辑器
 * 编辑 config/models.yaml：每种能力的默认模型/备选/兜底 + 每个 Agent 的覆盖配置
 */
export default function ModelMappingEditor({ settings, onSaved }) {
  const modelsConfig = settings?.models_config || {}
  const catalog = settings?.model_catalog || {}
  const agents = settings?.agents || []

  const [caps, setCaps] = useState({})
  const [overrides, setOverrides] = useState([])
  const [saving, setSaving] = useState(false)
  const [msg, setMsg] = useState('')

  // 供 datalist 提示的 "provider/model" 列表
  const suggestions = Object.entries(catalog).flatMap(([p, models]) =>
    (models || []).map(m => `${p}/${m}`)
  )

  useEffect(() => {
    const next = {}
    for (const [cap, cfg] of Object.entries(modelsConfig.capabilities || {})) {
      next[cap] = {
        default: cfg?.default || '',
        alternatives: (cfg?.alternatives || []).join(', '),
        fallback: (cfg?.fallback || []).join(', '),
      }
    }
    setCaps(next)

    const rows = []
    for (const [agent, capMap] of Object.entries(modelsConfig.agent_overrides || {})) {
      for (const [cap, model] of Object.entries(capMap || {})) {
        rows.push({ agent, capability: cap, model })
      }
    }
    setOverrides(rows)
  }, [settings])

  function splitList(s) {
    return (s || '').split(',').map(x => x.trim()).filter(Boolean)
  }

  async function handleSave() {
    setSaving(true)
    setMsg('')
    try {
      const capabilities = {}
      for (const [cap, v] of Object.entries(caps)) {
        capabilities[cap] = {
          default: (v.default || '').trim(),
          alternatives: splitList(v.alternatives),
          fallback: splitList(v.fallback),
        }
      }
      const agent_overrides = {}
      for (const row of overrides) {
        if (!row.agent || !row.capability || !row.model.trim()) continue
        agent_overrides[row.agent] = agent_overrides[row.agent] || {}
        agent_overrides[row.agent][row.capability] = row.model.trim()
      }
      await saveModels({ capabilities, agent_overrides })
      setMsg('✅ 已保存并立即生效（Provider/Agent 已重载）')
      onSaved?.()
    } catch (err) {
      setMsg('❌ ' + (err.message || '保存失败'))
    }
    setSaving(false)
  }

  return (
    <div>
      {/* 能力默认映射 */}
      <div className="table-wrap mb-2">
        <table className="table">
          <thead>
            <tr>
              <th style={{ width: 130 }}>能力</th>
              <th>默认模型</th>
              <th>备选（逗号分隔）</th>
              <th>兜底（逗号分隔）</th>
            </tr>
          </thead>
          <tbody>
            {Object.entries(caps).map(([cap, v]) => (
              <tr key={cap}>
                <td className="strong">{CAP_LABELS[cap] || cap}</td>
                <td>
                  <input className="input mono" list="model-suggestions"
                    value={v.default}
                    onChange={e => setCaps(prev => ({ ...prev, [cap]: { ...prev[cap], default: e.target.value } }))} />
                </td>
                <td>
                  <input className="input mono" list="model-suggestions"
                    placeholder="如 anthropic/claude-sonnet-4, deepseek/deepseek-chat"
                    value={v.alternatives}
                    onChange={e => setCaps(prev => ({ ...prev, [cap]: { ...prev[cap], alternatives: e.target.value } }))} />
                </td>
                <td>
                  <input className="input mono" list="model-suggestions"
                    value={v.fallback}
                    onChange={e => setCaps(prev => ({ ...prev, [cap]: { ...prev[cap], fallback: e.target.value } }))} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* Agent 覆盖 */}
      <label className="label">Agent 覆盖（为特定 Agent 指定模型，优先于能力默认值）</label>
      {overrides.length === 0 && (
        <p className="text-sm mb-1 text-muted">暂无覆盖配置。所有 Agent 使用上方能力默认映射。</p>
      )}
      {overrides.map((row, i) => (
        <div key={i} className="flex gap-1 items-center mb-1">
          <select className="select" style={{ flex: 2 }} value={row.agent}
            onChange={e => setOverrides(prev => prev.map((r, j) => j === i ? { ...r, agent: e.target.value } : r))}>
            <option value="">选择 Agent</option>
            {agents.map(a => <option key={a.name} value={a.name}>{a.name}</option>)}
          </select>
          <select className="select" style={{ flex: 1 }} value={row.capability}
            onChange={e => setOverrides(prev => prev.map((r, j) => j === i ? { ...r, capability: e.target.value } : r))}>
            <option value="vision">vision</option>
            <option value="text">text</option>
            <option value="image">image</option>
            <option value="local">local</option>
          </select>
          <input className="input mono" style={{ flex: 3 }} list="model-suggestions"
            placeholder="provider/model，如 deepseek/deepseek-chat"
            value={row.model}
            onChange={e => setOverrides(prev => prev.map((r, j) => j === i ? { ...r, model: e.target.value } : r))} />
          <button className="btn btn-danger btn-sm"
            onClick={() => setOverrides(prev => prev.filter((_, j) => j !== i))}>✕</button>
        </div>
      ))}
      <button className="btn btn-ghost btn-sm mb-2"
        onClick={() => setOverrides(prev => [...prev, { agent: '', capability: 'text', model: '' }])}>
        + 添加 Agent 覆盖
      </button>

      <datalist id="model-suggestions">
        {suggestions.map(s => <option key={s} value={s} />)}
      </datalist>

      {msg && <div className={`alert ${msg.startsWith('✅') ? 'alert-success' : 'alert-error'} mt-1`}>{msg}</div>}

      <div className="mt-1">
        <button className="btn btn-primary btn-sm" onClick={handleSave} disabled={saving}>
          {saving ? '保存中...' : '保存模型映射'}
        </button>
      </div>
    </div>
  )
}
