import { useState, useEffect, useCallback } from 'react'
import { useNavigate, Link } from 'react-router-dom'
import { getWorkflowTemplates, getWorkflowJobs, instantiateWorkflow } from '../api'
import { StatusBadge } from './Dashboard'

const STATUS_BADGES = {
  created: 'badge-muted', queued: 'badge-muted', running: 'badge-info',
  paused: 'badge-warn', waiting_human: 'badge-warn',
  completed: 'badge-ok', failed: 'badge-err', cancelled: 'badge-muted',
}
const STATUS_LABELS = {
  created: '已创建', queued: '排队中', running: '运行中', paused: '已暂停',
  waiting_human: '待人工审批', completed: '已完成', failed: '失败', cancelled: '已取消',
}

export default function Workflows() {
  const navigate = useNavigate()
  const [templates, setTemplates] = useState([])
  const [jobs, setJobs] = useState([])
  const [error, setError] = useState('')
  const [active, setActive] = useState(null)      // 展开的模板
  const [form, setForm] = useState({})            // 表单值
  const [filesByKey, setFilesByKey] = useState({})  // {输入key: [File]}
  const [submitting, setSubmitting] = useState(false)
  const [submitError, setSubmitError] = useState('')

  const refresh = useCallback(async () => {
    try {
      const [t, j] = await Promise.all([getWorkflowTemplates(), getWorkflowJobs()])
      setTemplates(t?.templates || [])
      setJobs(j?.jobs || [])
      setError('')
    } catch (err) {
      setError(err.message || '加载工作流失败')
    }
  }, [])

  useEffect(() => { refresh() }, [refresh])

  function openTemplate(t) {
    setActive(t)
    const defaults = {}
    const initial = {}
    for (const input of t.inputs || []) {
      if (input.default !== undefined) defaults[input.key] = input.default
      if (input.type === 'images') initial[input.key] = []
    }
    setForm(defaults)
    setFilesByKey(initial)
    setSubmitError('')
  }

  async function handleInstantiate(e) {
    e.preventDefault()
    setSubmitting(true)
    setSubmitError('')
    try {
      const fd = new FormData()
      for (const [key, list] of Object.entries(filesByKey)) {
        list.forEach(f => fd.append(key, f))
      }
      for (const [k, v] of Object.entries(form)) {
        if (v !== undefined && v !== null && v !== '') fd.append(k, v)
      }
      fd.append('mode', 'auto')
      const result = await instantiateWorkflow(active.template_name, fd)
      navigate(`/workflows/${result.job_id}`)
    } catch (err) {
      setSubmitError(err.message || '实例化失败')
      setSubmitting(false)
    }
  }

  return (
    <div>
      <h1 className="page-title">工作流</h1>
      <p className="page-sub">Skill 库：预制模板（YAML）驱动的商品图生产流水线，实例化后在画布中跟踪每个节点</p>

      {error && <div className="alert alert-error">{error}</div>}

      {/* Skill 库 */}
      <div className="grid-3 mb-3">
        {templates.map(t => (
          <div key={t.template_name} className="card" style={{ display: 'flex', flexDirection: 'column' }}>
            <div className="text-lg">{t.icon}</div>
            <h3 className="mt-1" style={{ marginBottom: 4 }}>{t.name}</h3>
            <p className="text-sm" style={{ flex: 1 }}>{t.description}</p>
            <div className="flex flex-wrap gap-1 mt-1 mb-2">
              <span className="badge badge-muted">{t.category}</span>
              <span className="badge badge-info">{t.node_count} 个节点</span>
              <span className="badge badge-muted">v{t.version}</span>
            </div>
            <button className="btn btn-primary" onClick={() => openTemplate(t)}>实例化 →</button>
          </div>
        ))}
        {templates.length === 0 && !error && (
          <div className="card empty-state" style={{ gridColumn: '1 / -1' }}>
            <p className="text-lg mb-1">🧩</p><p className="text-sm">暂无模板（config/workflows/*.yaml）</p>
          </div>
        )}
      </div>

      {/* 实例化表单 */}
      {active && (
        <form onSubmit={handleInstantiate} className="card mb-3">
          <div className="flex items-center justify-between mb-2">
            <h3 style={{ marginBottom: 0 }}>{active.icon} {active.name} — 实例化</h3>
            <button type="button" className="btn btn-ghost btn-sm" onClick={() => setActive(null)}>收起</button>
          </div>
          <div className="grid-2">
            {(active.inputs || []).map(input => (
              <div key={input.key} className="form-group">
                <label className="label">
                  {input.label} {input.required ? <span style={{ color: '#f87171' }}>*</span> : <span className="text-xs">（可选）</span>}
                </label>
                {input.type === 'images' ? (
                  <div>
                    <div
                      className={`drop-zone ${(filesByKey[input.key] || []).length ? 'active' : ''}`}
                      style={{ padding: 18 }}
                      onClick={() => document.getElementById(`wf-files-${input.key}`).click()}
                      onDrop={e => {
                        e.preventDefault()
                        const dropped = Array.from(e.dataTransfer.files).filter(f => f.type.startsWith('image/')).slice(0, 10)
                        setFilesByKey(prev => ({ ...prev, [input.key]: dropped }))
                      }}
                      onDragOver={e => e.preventDefault()}
                    >
                      {(filesByKey[input.key] || []).length
                        ? <p className="strong">已选择 {(filesByKey[input.key] || []).length} 张图片</p>
                        : <p className="text-sm">拖拽或点击选择图片</p>}
                    </div>
                    <input id={`wf-files-${input.key}`} type="file" multiple accept="image/*" hidden
                      onChange={e => setFilesByKey(prev => ({ ...prev, [input.key]: Array.from(e.target.files).slice(0, 10) }))} />
                  </div>
                ) : input.type === 'select' ? (
                  <select className="select" value={form[input.key] ?? ''}
                    onChange={e => setForm(prev => ({ ...prev, [input.key]: e.target.value }))}>
                    {(input.options || []).map(o => <option key={o} value={o}>{o}</option>)}
                  </select>
                ) : (
                  <input className="input" value={form[input.key] ?? ''}
                    onChange={e => setForm(prev => ({ ...prev, [input.key]: e.target.value }))} />
                )}
              </div>
            ))}
          </div>
          {submitError && <div className="alert alert-error">{submitError}</div>}
          <button type="submit" className="btn btn-primary" disabled={submitting}>
            {submitting ? '创建中...' : '🚀 创建并运行'}
          </button>
        </form>
      )}

      {/* 最近作业 */}
      <div className="card">
        <div className="flex items-center justify-between mb-1">
          <h3 style={{ marginBottom: 0 }}>最近作业（{jobs.length}）</h3>
          <button className="btn btn-ghost btn-sm" onClick={refresh}>刷新</button>
        </div>
        {jobs.length === 0 ? (
          <div className="empty-state"><p className="text-sm">暂无作业。选择上方模板开始。</p></div>
        ) : (
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr><th>作业</th><th>模板</th><th>状态</th><th>模式</th><th>成本</th><th>创建时间</th><th>操作</th></tr>
              </thead>
              <tbody>
                {jobs.map(j => (
                  <tr key={j.job_id}>
                    <td className="mono">{j.job_id.slice(0, 10)}…</td>
                    <td className="strong">{j.template_name}</td>
                    <td><span className={`badge ${STATUS_BADGES[j.status] || 'badge-muted'}`}>{STATUS_LABELS[j.status] || j.status}</span></td>
                    <td className="text-sm">{j.mode === 'manual' ? '手动挡' : '自动挡'}</td>
                    <td className="text-sm">${(j.cost_so_far || 0).toFixed(4)}</td>
                    <td className="text-xs">{new Date(j.created_at).toLocaleString()}</td>
                    <td><Link to={`/workflows/${j.job_id}`} className="btn btn-primary btn-sm">查看画布</Link></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  )
}
