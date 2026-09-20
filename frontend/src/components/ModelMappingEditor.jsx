import { useEffect, useState } from 'react'
import { saveModels } from '../api'

const CAP_LABELS = { vision: '视觉（vision）', text: '文本（text）', image: '生图（image）', local: '本地（local）' }

// 兜底用的图像模型前缀（正常按路由能力推导；此处覆盖老路由命名）
const IMAGE_MODEL_RE = /^(seedream\/|flux\/|openai\/dall-e)/

/**
 * 能力→模型映射编辑器
 * 编辑 config/models.yaml：每种能力的默认模型/备选/兜底 + 每个 Agent 的覆盖配置
 */
export default function ModelMappingEditor({ settings, onSaved }) {
  const modelsConfig = settings?.models_config || {}
  const catalog = settings?.model_catalog || {}
  const agents = settings?.agents || []
  const routes = settings?.provider_routes || []
  // 后端给出的"覆盖不会生效"清单（能力与 Agent 的 requires 不匹配）
  const overrideIssues = settings?.agent_overrides_issues || []

  const [caps, setCaps] = useState({})
  const [overrides, setOverrides] = useState([])
  const [saving, setSaving] = useState(false)
  const [msg, setMsg] = useState('')

  // 该 Agent 声明需要的能力（requires）——覆盖键必须从这里选，否则后端静默忽略
  const requiresOf = name => (agents.find(a => a.name === name)?.requires) || []

  // 可用 = 注册表里真的有这个服务商（凭据就位）。建议来自"平台目录"（所有内置服务商的
  // 官方模型），与是否配置无关 —— 用户实测困惑："为什么拉取出来的不只有已配置好的模型"。
  // 这里按可用性排序 + 标注 + 对未配置项告警，避免又踩"配了未配置的服务商 → 静默回落 Mock"。
  const availableRoutes = new Set((settings?.providers || []).map(p => p.name))
  const routeOf = spec => String(spec || '').split('/')[0]
  // mock 是永远可用的兜底，不该被算作"未配置"
  const isAvailable = spec => spec === 'mock' || availableRoutes.has(routeOf(spec))
  const routeLabel = route => routes.find(r => r.route === route)?.label || route

  // 供 datalist 提示的 "provider/model" 列表（可用的排前面）
  const allSuggestions = Object.entries(catalog).flatMap(([p, models]) =>
    (models || []).map(m => `${p}/${m}`)
  )
  const suggestions = [
    ...allSuggestions.filter(isAvailable),
    ...allSuggestions.filter(s => !isAvailable(s)),
  ]
  // 按能力过滤建议：图像模型优先用**按能力分组的模型目录**（models_by_capability）判定——
  // 路由级能力不够细（openai 同时有 text 与 image，会把 gpt-4o 误判成生图模型）；
  // 没有分组信息时，只有"纯图像路由"才整体归入生图（宁可不建议，也不要把文本模型塞进生图）
  const imageModelIds = new Set()
  const llmModelIds = new Set()
  for (const r of routes) {
    const byCap = r.models_by_capability || {}
    for (const m of byCap.image || []) imageModelIds.add(`${r.route}/${m}`)
    for (const cap of ['text', 'vision']) {
      for (const m of byCap[cap] || []) llmModelIds.add(`${r.route}/${m}`)
    }
  }
  const isImageModel = spec => {
    if (imageModelIds.has(spec)) return true
    if (llmModelIds.has(spec)) return false
    const caps = routes.find(r => r.route === routeOf(spec))?.capabilities || ''
    if (!caps) return IMAGE_MODEL_RE.test(spec)
    return caps.includes('image') && !caps.includes('text') && !caps.includes('vision')
  }
  const imageSuggestions = suggestions.filter(isImageModel)
  const llmSuggestions = suggestions.filter(s => !isImageModel(s))
  const listIdFor = cap => (cap === 'image' ? 'model-suggestions-image' : 'model-suggestions-llm')

  // 当前映射里指向"未配置服务商"的条目 → 明确告警（这些会回落到 Mock）
  const unconfiguredInMapping = []
  for (const [cap, v] of Object.entries(caps)) {
    for (const spec of [v.default, ...splitList(v.alternatives), ...splitList(v.fallback)]) {
      if (spec && !isAvailable(spec)) {
        unconfiguredInMapping.push({ cap, spec, route: routeOf(spec) })
      }
    }
  }
  const unconfiguredRoutes = [...new Set(
    Object.keys(catalog).filter(r => !availableRoutes.has(r) && r !== 'mock'))]

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
      {/* 可用性说明：建议列表来自"平台目录"，包含未配置服务商的模型，必须讲清楚 */}
      <div className="text-xs mb-2">
        可选模型来自平台目录（含未配置的服务商）。当前<b>可用</b>：
        {(settings?.providers || []).length
          ? (settings.providers || []).map(p => routeLabel(p.name)).join('、')
          : '无（全部回落 Mock）'}
        {unconfiguredRoutes.length > 0 && (
          <> ；未配置：{unconfiguredRoutes.map(routeLabel).join('、')}（选它们的模型会回落到 Mock）</>
        )}
      </div>

      {/* 当前映射里指向未配置服务商的条目 → 直接告警（实测踩过：生图指向未配置的 seedream） */}
      {unconfiguredInMapping.length > 0 && (
        <div className="alert alert-warn mb-2" role="alert">
          ⚠️ 当前映射里有 {unconfiguredInMapping.length} 处指向<b>未配置</b>的服务商，
          运行时会回落到 Mock：
          {unconfiguredInMapping.slice(0, 4).map((x, i) => (
            <span key={i} className="chip chip-warn ml-1">
              {CAP_LABELS[x.cap] || x.cap} → {x.spec}
            </span>
          ))}
          {unconfiguredInMapping.length > 4 && <span className="ml-1">…</span>}
        </div>
      )}

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
                  <input className="input mono" list={listIdFor(cap)}
                    value={v.default}
                    onChange={e => setCaps(prev => ({ ...prev, [cap]: { ...prev[cap], default: e.target.value } }))} />
                </td>
                <td>
                  <input className="input mono" list={listIdFor(cap)}
                    placeholder="如 anthropic/claude-sonnet-4, deepseek/deepseek-v4-flash"
                    value={v.alternatives}
                    onChange={e => setCaps(prev => ({ ...prev, [cap]: { ...prev[cap], alternatives: e.target.value } }))} />
                </td>
                <td>
                  <input className="input mono" list={listIdFor(cap)}
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
      {overrideIssues.length > 0 && (
        <div className="alert alert-warn text-xs mb-1">
          ⚠ 有 {overrideIssues.length} 条覆盖不会生效（能力与该 Agent 的 requires 不匹配）：
          <ul style={{ margin: '0.25rem 0 0 1rem' }}>
            {overrideIssues.map((it, i) => (
              <li key={i}>{it.agent} 配了 <span className="mono">{it.capability}</span>：{it.reason}</li>
            ))}
          </ul>
        </div>
      )}
      {overrides.map((row, i) => {
        const needs = requiresOf(row.agent)
        const capabilityOptions = needs.length
          ? needs
          : ['vision', 'text', 'image', 'local']
        const mismatched = needs.length > 0 && row.capability && !needs.includes(row.capability)
        return (
        <div key={i} className="flex gap-1 items-center mb-1">
          <select className="select" style={{ flex: 2 }} value={row.agent}
            onChange={e => {
              const agent = e.target.value
              const needs = requiresOf(agent)
              // 选了 Agent 就把能力自动纠到它真正需要的那个：覆盖键写错会被后端静默忽略
              const capability = needs.length && !needs.includes(row.capability)
                ? needs[0] : row.capability
              setOverrides(prev => prev.map((r, j) => j === i ? { ...r, agent, capability } : r))
            }}>
            <option value="">选择 Agent</option>
            {agents.map(a => <option key={a.name} value={a.name}>{a.name}</option>)}
          </select>
          <select className="select" style={{ flex: 1 }} value={row.capability}
            onChange={e => setOverrides(prev => prev.map((r, j) => j === i ? { ...r, capability: e.target.value } : r))}>
            {capabilityOptions.map(cap => <option key={cap} value={cap}>{cap}</option>)}
          </select>
          <input className="input mono" style={{ flex: 3 }} list={listIdFor(row.capability)}
            placeholder="provider/model，如 deepseek/deepseek-v4-flash"
            value={row.model}
            onChange={e => setOverrides(prev => prev.map((r, j) => j === i ? { ...r, model: e.target.value } : r))} />
          <button className="btn btn-danger btn-sm"
            onClick={() => setOverrides(prev => prev.filter((_, j) => j !== i))}>✕</button>
          {mismatched && (
            <span className="badge badge-warn" title={`该 Agent 需要 ${needs.join('/')}，此覆盖不会生效`}>
              不生效
            </span>
          )}
        </div>
        )
      })}
      <button className="btn btn-ghost btn-sm mb-2"
        onClick={() => setOverrides(prev => [...prev, { agent: '', capability: 'text', model: '' }])}>
        + 添加 Agent 覆盖
      </button>

      <datalist id="model-suggestions-llm">
        {llmSuggestions.map(s => (
          <option key={s} value={s}
                  label={isAvailable(s) ? '已配置' : `${routeLabel(routeOf(s))} 未配置`} />
        ))}
      </datalist>
      <datalist id="model-suggestions-image">
        {imageSuggestions.map(s => (
          <option key={s} value={s}
                  label={isAvailable(s) ? '已配置' : `${routeLabel(routeOf(s))} 未配置`} />
        ))}
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
