import { useState, useEffect, useCallback } from 'react'
import { Link } from 'react-router-dom'
import {
  BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, Cell,
} from 'recharts'
import {
  getWorkflowTemplates, getBatches, getBatch, getBatchReport,
  createBatch, createBatchCsv, controlBatch,
} from '../api'

const BATCH_BADGES = {
  created: 'badge-muted', running: 'badge-info', paused: 'badge-warn',
  completed: 'badge-ok', partial: 'badge-warn', failed: 'badge-err', cancelled: 'badge-muted',
}
const BATCH_LABELS = {
  created: '已创建', running: '运行中', paused: '已暂停',
  completed: '全部完成', partial: '部分失败', failed: '失败', cancelled: '已取消',
}
const ITEM_LABELS = { pending: '待执行', running: '执行中', succeeded: '成功', failed: '死信' }

const DEMO_ITEMS = [
  // 审计修复：JSON 演示数据补 product_images 占位符（与 CSV 路径 Y3N2 一致，
  // 否则模板必填校验使演示批次全部进入死信；真实图片请传 base64）
  { product_info: '护肝片 60 粒装', platform: 'taobao', category_hint: '保健品', product_images: ['Y3N2'] },
  { product_info: '维生素C咀嚼片', platform: 'taobao', category_hint: '保健品', product_images: ['Y3N2'] },
  { product_info: '玻尿酸保湿面霜', platform: 'taobao', category_hint: '化妆品', product_images: ['Y3N2'] },
  { product_info: '无糖坚果礼盒', platform: 'jd', category_hint: '食品', product_images: ['Y3N2'] },
]

const CHART_COLORS = ['#3b82f6', '#22c55e', '#f59e0b', '#8b5cf6', '#ec4899', '#14b8a6']

function fmtMs(ms) {
  if (!ms || ms <= 0) return '—'
  if (ms < 1000) return `${Math.round(ms)}ms`
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)}s`
  return `${(ms / 60_000).toFixed(1)}min`
}

function fmtUsd(v) {
  return `$${(v || 0).toFixed(4)}`
}

function ReportPanel({ report, error, onRefresh }) {
  if (error) return <div className="alert alert-error">{error}</div>
  if (!report) return <p className="text-sm text-muted">报表加载中…</p>

  const ov = report.overview
  const tplData = report.by_template.map(t => ({
    ...t, pct: Math.round(t.success_rate * 1000) / 10,
  }))
  const histData = report.duration_histogram
  const reasons = report.failure_reasons
  const statusEntries = Object.entries(report.batch_status || {})
  const maxReason = Math.max(1, ...reasons.map(r => r.count))

  return (
    <div className="flex flex-col gap-2">
      <div className="flex items-center justify-between">
        <h3 className="mb-0">批量数据报表（当前租户）</h3>
        <button className="btn btn-ghost btn-sm" onClick={onRefresh}>刷新报表</button>
      </div>

      {/* 总览卡片 */}
      <div className="grid-4">
        {[
          ['批次总数', ov.batch_count],
          ['商品条目', ov.item_count],
          ['成功率', `${Math.round(ov.success_rate * 1000) / 10}%`],
          ['总成本', fmtUsd(ov.total_cost_usd)],
        ].map(([label, value]) => (
          <div key={label} className="card stat-card" style={{ padding: 14 }}>
            <div className="stat-value">{value}</div>
            <div className="stat-label">{label}</div>
          </div>
        ))}
      </div>

      <div className="grid-2" style={{ alignItems: 'start' }}>
        {/* 模板成功率 */}
        <div className="card">
          <h4 className="mb-1">各模板成功率</h4>
          {tplData.length === 0 ? (
            <div className="empty-state"><p className="text-sm">暂无数据</p></div>
          ) : (
            <ResponsiveContainer width="100%" height={220}>
              <BarChart data={tplData} margin={{ top: 8, right: 8, left: -18, bottom: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" />
                <XAxis dataKey="template_name" tick={{ fontSize: 11, fill: '#94a3b8' }} />
                <YAxis domain={[0, 100]} tick={{ fontSize: 11, fill: '#94a3b8' }}
                  tickFormatter={v => `${v}%`} />
                <Tooltip contentStyle={{ background: '#0f172a', border: '1px solid #334155', borderRadius: 8 }}
                  formatter={v => [`${v}%`, '成功率']} />
                <Bar dataKey="pct" radius={[4, 4, 0, 0]}>
                  {tplData.map((t, i) => (
                    <Cell key={t.template_name} fill={CHART_COLORS[i % CHART_COLORS.length]} />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          )}
          <div className="flex flex-col gap-1 mt-1">
            {tplData.map(t => (
              <div key={t.template_name} className="flex justify-between text-xs">
                <span>{t.template_name}</span>
                <span style={{ color: '#94a3b8' }}>
                  {t.succeeded}/{t.succeeded + t.failed} 成功 · 均耗时 {fmtMs(t.avg_duration_ms)} · {fmtUsd(t.total_cost_usd)}
                </span>
              </div>
            ))}
          </div>
        </div>

        {/* 耗时分布 */}
        <div className="card">
          <h4 className="mb-1">条目耗时分布</h4>
          {histData.reduce((s, h) => s + h.count, 0) === 0 ? (
            <div className="empty-state"><p className="text-sm">暂无数据（无已创建作业的条目）</p></div>
          ) : (
            <ResponsiveContainer width="100%" height={220}>
              <BarChart data={histData} margin={{ top: 8, right: 8, left: -18, bottom: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" />
                <XAxis dataKey="bucket" tick={{ fontSize: 11, fill: '#94a3b8' }} />
                <YAxis allowDecimals={false} tick={{ fontSize: 11, fill: '#94a3b8' }} />
                <Tooltip contentStyle={{ background: '#0f172a', border: '1px solid #334155', borderRadius: 8 }}
                  formatter={v => [v, '条目数']} />
                <Bar dataKey="count" fill="#3b82f6" radius={[4, 4, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          )}
          <div className="flex flex-wrap gap-1 mt-1 text-xs" style={{ color: '#64748b' }}>
            <span>平均耗时：<b style={{ color: '#e2e8f0' }}>{fmtMs(ov.avg_item_duration_ms)}</b></span>
            <span>· 进行中/待执行：{ov.pending_or_running}</span>
            {statusEntries.length > 0 && (
              <span className="flex gap-1">
                {statusEntries.map(([st, n]) => (
                  <span key={st} className={`badge ${BATCH_BADGES[st] || 'badge-muted'}`}>
                    {BATCH_LABELS[st] || st} ×{n}
                  </span>
                ))}
              </span>
            )}
          </div>
        </div>
      </div>

      {/* 失败原因 Top-5 */}
      <div className="card">
        <h4 className="mb-1">失败原因 Top-5</h4>
        {reasons.length === 0 ? (
          <p className="text-sm text-muted">暂无失败条目 🎉</p>
        ) : (
          <div className="flex flex-col gap-1">
            {reasons.map(r => (
              <div key={r.reason} className="flex items-center gap-2">
                <span className="text-xs" style={{ width: 220, color: '#fca5a5', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={r.reason}>
                  {r.reason}
                </span>
                <div style={{ flex: 1, height: 8, background: '#1e293b', borderRadius: 4, overflow: 'hidden' }}>
                  <div style={{ width: `${(r.count / maxReason) * 100}%`, height: '100%', background: '#ef4444' }} />
                </div>
                <span className="text-xs mono" style={{ color: '#fca5a5' }}>{r.count}</span>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}

export default function Batches() {
  const [templates, setTemplates] = useState([])
  const [batches, setBatches] = useState([])
  const [error, setError] = useState('')
  const [template, setTemplate] = useState('scene_suite')
  const [mode, setMode] = useState('auto')
  const [concurrency, setConcurrency] = useState(3)
  const [itemsJson, setItemsJson] = useState(JSON.stringify(DEMO_ITEMS, null, 2))
  const [csvFile, setCsvFile] = useState(null)
  const [inputMethod, setInputMethod] = useState('demo')  // demo | json | csv
  const [submitting, setSubmitting] = useState(false)
  const [submitError, setSubmitError] = useState('')
  const [detail, setDetail] = useState(null)
  const [busy, setBusy] = useState(false)
  const [view, setView] = useState('tasks')  // tasks | report（M5）
  const [report, setReport] = useState(null)
  const [reportError, setReportError] = useState('')

  const refresh = useCallback(async () => {
    try {
      const [t, b] = await Promise.all([getWorkflowTemplates(), getBatches()])
      setTemplates(t?.templates || [])
      setBatches(b?.batches || [])
      setError('')
    } catch (err) {
      setError(err.message || '加载批量任务失败')
    }
  }, [])

  const loadReport = useCallback(async () => {
    try {
      setReport(await getBatchReport())
      setReportError('')
    } catch (err) {
      setReportError(err.message || '加载报表失败')
    }
  }, [])

  useEffect(() => { refresh() }, [refresh])
  useEffect(() => { if (view === 'report') loadReport() }, [view, loadReport])

  async function loadDetail(batchId) {
    try {
      setDetail(await getBatch(batchId))
    } catch (err) {
      alert(err.message || '加载详情失败')
    }
  }

  async function handleCreate() {
    setSubmitting(true)
    setSubmitError('')
    try {
      let result
      if (inputMethod === 'csv') {
        if (!csvFile) throw new Error('请选择 CSV 文件')
        result = await createBatchCsv(template, csvFile, mode, concurrency)
      } else {
        const items = inputMethod === 'demo' ? DEMO_ITEMS : JSON.parse(itemsJson)
        result = await createBatch({ template_name: template, mode, max_concurrency: concurrency, items })
      }
      // 审计修复：submit 返回的 batch 不含 items → 创建后拉取完整详情
      setDetail(result)
      try {
        setDetail(await getBatch(result.batch_id))
      } catch { /* 详情拉取失败则保留 submit 返回值 */ }
      refresh()
    } catch (err) {
      setSubmitError(err.message || '创建失败')
    }
    setSubmitting(false)
  }

  async function handleControl(batchId, action) {
    setBusy(true)
    try {
      await controlBatch(batchId, action)
      refresh()
      if (detail?.batch_id === batchId) loadDetail(batchId)
    } catch (err) {
      alert(err.message || '控制失败')
    }
    setBusy(false)
  }

  const activeBatches = batches.filter(b => ['running', 'paused'].includes(b.status))

  return (
    <div>
      <h1 className="page-title">批量任务</h1>
      <p className="page-sub">按模板批量处理商品列表：并发窗口 + 失败隔离 + 死信重跑（CSV 或 JSON 输入）</p>

      <div className="flex gap-1 mb-2">
        {[['tasks', '📋 任务列表'], ['report', '📈 数据报表']].map(([k, label]) => (
          <button key={k} type="button"
            className={`btn btn-sm ${view === k ? 'btn-primary' : 'btn-ghost'}`}
            onClick={() => setView(k)}>{label}</button>
        ))}
      </div>

      {view === 'report' ? (
        <ReportPanel report={report} error={reportError} onRefresh={loadReport} />
      ) : (
        <>
          {error && <div className="alert alert-error">{error}</div>}
          {activeBatches.length > 0 && (
            <div className="alert alert-info">
              {activeBatches.length} 个批次运行中/暂停 —— 下方列表可控制
            </div>
          )}

          <div className="grid-2" style={{ alignItems: 'start' }}>
            {/* 创建 */}
            <div className="card">
              <h3 className="mb-1">创建批量任务</h3>
              <div className="grid-2">
                <div className="form-group">
                  <label className="label">模板</label>
                  <select className="select" value={template} onChange={e => setTemplate(e.target.value)}>
                    {templates.map(t => <option key={t.template_name} value={t.template_name}>{t.icon} {t.name}</option>)}
                  </select>
                </div>
                <div className="form-group">
                  <label className="label">并发窗口（1-10）</label>
                  <input className="input" type="number" min="1" max="10" value={concurrency}
                    onChange={e => setConcurrency(parseInt(e.target.value) || 3)} />
                </div>
              </div>
              <div className="form-group">
                <label className="label">商品输入方式</label>
                <div className="flex gap-1">
                  {[['demo', '演示数据'], ['json', 'JSON'], ['csv', 'CSV 文件']].map(([k, label]) => (
                    <button key={k} type="button"
                      className={`btn btn-sm ${inputMethod === k ? 'btn-primary' : 'btn-ghost'}`}
                      onClick={() => setInputMethod(k)}>{label}</button>
                  ))}
                </div>
              </div>

              {inputMethod === 'json' && (
                <div className="form-group">
                  <label className="label">items JSON（每项 = 模板输入）</label>
                  <textarea className="textarea" style={{ minHeight: 160, fontFamily: 'Consolas, monospace', fontSize: 12 }}
                    value={itemsJson} onChange={e => setItemsJson(e.target.value)} />
                </div>
              )}
              {inputMethod === 'csv' && (
                <div className="form-group">
                  <label className="label">CSV 文件（列: product_info,platform,category_hint,scene,collaboration_mode）</label>
                  <input className="input" type="file" accept=".csv,text/csv"
                    onChange={e => setCsvFile(e.target.files?.[0] || null)} />
                  <p className="text-xs mt-1 text-muted">支持 utf-8 / gbk 编码。CSV 无图片列（Mock 模式注入占位图）；真实图片请用 JSON 传 base64 或图片 URL。</p>
                </div>
              )}
              {inputMethod === 'demo' && (
                <p className="text-sm mb-2">使用 4 个演示商品（保健品/化妆品/食品）快速体验批量流水线。</p>
              )}

              {submitError && <div className="alert alert-error">{submitError}</div>}
              <button className="btn btn-primary" onClick={handleCreate} disabled={submitting}>
                {submitting ? '创建中...' : '🚀 创建批量任务'}
              </button>
            </div>

            {/* 列表 */}
            <div className="card">
              <div className="flex items-center justify-between mb-1">
                <h3 style={{ marginBottom: 0 }}>批次列表（{batches.length}）</h3>
                <button className="btn btn-ghost btn-sm" onClick={refresh}>刷新</button>
              </div>
              {batches.length === 0 ? (
                <div className="empty-state"><p className="text-sm">暂无批量任务。左侧创建。</p></div>
              ) : (
                <div className="flex flex-col gap-1">
                  {batches.map(b => {
                    const pct = b.total > 0 ? Math.round((b.done / b.total) * 100) : 0
                    return (
                      <div key={b.batch_id} className="card" style={{ padding: 12, background: '#0f172a' }}>
                        <div className="flex items-center justify-between" style={{ flexWrap: 'wrap', gap: 4 }}>
                          <div>
                            <button className="expand-btn" style={{ background: 'none', border: 'none', color: '#93c5fd', cursor: 'pointer', fontSize: 13, padding: 0 }}
                              onClick={() => loadDetail(b.batch_id)}>
                              {b.template_name} · <span className="mono">{b.batch_id.slice(0, 8)}…</span>
                            </button>
                            <span className={`badge ml-1 ${BATCH_BADGES[b.status] || 'badge-muted'}`}>{BATCH_LABELS[b.status] || b.status}</span>
                          </div>
                          <div className="flex gap-1">
                            {b.status === 'running' && (
                              <button className="btn btn-warning btn-sm" disabled={busy} onClick={() => handleControl(b.batch_id, 'pause')}>⏸</button>
                            )}
                            {b.status === 'paused' && (
                              <button className="btn btn-success btn-sm" disabled={busy} onClick={() => handleControl(b.batch_id, 'resume')}>▶</button>
                            )}
                            {(b.status === 'running' || b.status === 'paused') && (
                              <button className="btn btn-danger btn-sm" disabled={busy} onClick={() => handleControl(b.batch_id, 'cancel')}>取消</button>
                            )}
                            {b.status === 'partial' && (
                              <button className="btn btn-warning btn-sm" disabled={busy} onClick={() => handleControl(b.batch_id, 'retry_failed')}>🔁 重跑死信</button>
                            )}
                          </div>
                        </div>
                        <div className="mt-1" style={{ height: 6, background: '#1e293b', borderRadius: 3, overflow: 'hidden' }}>
                          <div style={{ width: `${pct}%`, height: '100%', background: b.failed > 0 ? '#f59e0b' : '#22c55e', transition: 'width .3s' }} />
                        </div>
                        <div className="flex justify-between mt-1 text-xs" style={{ color: '#64748b' }}>
                          <span>完成 {b.done}/{b.total}</span>
                          <span>失败 {b.failed} · 并发 {b.max_concurrency}</span>
                        </div>
                      </div>
                    )
                  })}
                </div>
              )}
            </div>
          </div>

          {/* 详情 */}
          {detail && (
            <div className="card mt-2">
              <div className="flex items-center justify-between mb-1">
                <h3 style={{ marginBottom: 0 }}>批次详情 <span className="mono text-sm">{detail.batch_id}</span></h3>
                <button className="btn btn-ghost btn-sm" onClick={() => setDetail(null)}>收起</button>
              </div>
              <div className="table-wrap">
                <table className="table">
                  <thead>
                    <tr><th>#</th><th>商品</th><th>平台</th><th>状态</th><th>尝试</th><th>作业</th><th>错误</th></tr>
                  </thead>
                  <tbody>
                    {(detail.items || []).map(it => (
                      <tr key={it.seq}>
                        <td className="text-sm">{it.seq + 1}</td>
                        <td className="text-sm">{it.inputs?.product_info || '—'}</td>
                        <td className="text-sm">{it.inputs?.platform || '—'}</td>
                        <td>
                          <span className={`badge ${it.status === 'succeeded' ? 'badge-ok' : it.status === 'failed' ? 'badge-err' : it.status === 'running' ? 'badge-info' : 'badge-muted'}`}>
                            {ITEM_LABELS[it.status] || it.status}
                          </span>
                        </td>
                        <td className="text-sm">{it.attempts}</td>
                        <td>
                          {it.job_id
                            ? <Link to={`/workflows/${it.job_id}`} className="mono text-sm">{it.job_id.slice(0, 10)}…</Link>
                            : <span className="text-xs">—</span>}
                        </td>
                        <td className="text-xs" style={{ color: '#fca5a5', maxWidth: 260, overflow: 'hidden', textOverflow: 'ellipsis' }}>
                          {it.error || ''}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}
        </>
      )}
    </div>
  )
}
