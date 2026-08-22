import { useState, useEffect, useCallback, useRef } from 'react'
import { useParams, Link } from 'react-router-dom'
import {
  getWorkflowJob, controlWorkflow, decideWorkflow, replicateStyle, wsUrl,
} from '../api'

const NODE_ICONS = {
  tool: '🔧', agent: '🤖', condition: '🔀', human: '👤',
  group_chat: '💬', end: '🏁', subworkflow: '📦',
}

const STEP_STYLE = {
  pending: { bg: 'rgba(30, 41, 59, .4)', border: '#334155', label: '待执行', dot: '#64748b' },
  running: { bg: 'rgba(30, 58, 95, .45)', border: '#3b82f6', label: '运行中', dot: '#3b82f6' },
  succeeded: { bg: 'rgba(22, 51, 31, .45)', border: '#22c55e', label: '成功', dot: '#22c55e' },
  failed: { bg: 'rgba(63, 29, 29, .45)', border: '#ef4444', label: '失败', dot: '#ef4444' },
  skipped: { bg: 'rgba(30, 41, 59, .4)', border: '#475569', label: '已跳过', dot: '#64748b' },
  waiting_human: { bg: 'rgba(63, 47, 29, .45)', border: '#f59e0b', label: '待人工', dot: '#f59e0b' },
}

const JOB_BADGES = {
  created: 'badge-muted', queued: 'badge-muted', running: 'badge-info',
  paused: 'badge-warn', waiting_human: 'badge-warn',
  completed: 'badge-ok', failed: 'badge-err', cancelled: 'badge-muted',
}

export default function WorkflowJob() {
  const { id } = useParams()
  const [job, setJob] = useState(null)
  const [selected, setSelected] = useState(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [connected, setConnected] = useState(false)
  const [replicating, setReplicating] = useState(false)
  const wsRef = useRef(null)
  const fileRef = useRef(null)

  // M3 一键风格复刻：上传参考图 → 拆解 → 融合提示词重跑
  async function handleReplicate(e) {
    const file = e.target.files?.[0]
    e.target.value = ''  // 允许重复选择同一文件
    if (!file || replicating) return
    setReplicating(true)
    try {
      const result = await replicateStyle(id, file)
      alert(`✅ 风格拆解完成（标签: ${(result.style?.style_tags || []).join('、')}），已从提示词节点重跑`)
      await refresh()
    } catch (err) {
      alert('风格复刻失败: ' + (err.message || '未知错误'))
    }
    setReplicating(false)
  }

  const refresh = useCallback(async () => {
    try {
      const data = await getWorkflowJob(id)
      setJob(data)
      setError('')
    } catch (err) {
      setError(err.message || '加载作业失败')
    }
  }, [id])

  // 审计修复：路由参数变化时重置状态，避免新 URL 短暂渲染上一作业的数据
  useEffect(() => {
    setJob(null)
    setSelected(null)
    setError('')
  }, [id])

  // WebSocket 实时事件 + 3s 轮询兜底
  useEffect(() => {
    refresh()
    let ws
    try {
      ws = new WebSocket(wsUrl(`/ws/workflows/jobs/${id}`))
      ws.onopen = () => setConnected(true)
      ws.onclose = () => setConnected(false)
      ws.onmessage = () => refresh()
      ws.onerror = () => setConnected(false)
    } catch {}
    const t = setInterval(refresh, 3000)
    return () => { clearInterval(t); if (ws) ws.close() }
  }, [refresh])

  async function handleControl(action, stepNode = '') {
    setBusy(true)
    try {
      await controlWorkflow(id, action, stepNode)
      await refresh()
    } catch (err) {
      alert(err.message || '控制失败')
    }
    setBusy(false)
  }

  async function handleDecision(action) {
    setBusy(true)
    try {
      await decideWorkflow(id, action)
      await refresh()
    } catch (err) {
      alert(err.message || '决策失败')
    }
    setBusy(false)
  }

  if (error && !job) {
    return (
      <div className="card card-error mt-2">
        <h3 className="mb-1">加载失败</h3>
        <p className="text-sm">{error}</p>
        <Link to="/workflows" className="btn btn-primary btn-sm mt-2">返回工作流</Link>
      </div>
    )
  }
  if (!job) {
    return (
      <div className="card text-center" style={{ minHeight: 200, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
        <div><div className="spinner" /><p className="text-sm mt-2">加载作业...</p></div>
      </div>
    )
  }

  const steps = job.steps || []
  const selectedStep = selected ? steps.find(s => s.node === selected) : null
  const terminal = ['completed', 'failed', 'cancelled'].includes(job.status)

  return (
    <div>
      {/* 头部 */}
      <div className="flex items-center justify-between mb-2" style={{ flexWrap: 'wrap', gap: 8 }}>
        <div>
          <Link to="/workflows" className="text-sm">← 工作流</Link>
          <h1 className="page-title" style={{ display: 'inline', marginLeft: 12, fontSize: 18 }}>
            {job.template_name} <span className="mono" style={{ fontSize: 13 }}>{id?.slice(0, 12)}…</span>
          </h1>
          <span className={`badge ml-1 ${JOB_BADGES[job.status] || 'badge-muted'}`}>{job.status}</span>
          <span className="badge badge-muted ml-1">{job.mode === 'manual' ? '手动挡' : '自动挡'}</span>
          <span className="text-sm ml-1">成本 <span className="strong">${(job.cost_so_far || 0).toFixed(4)}</span></span>
          <span className="text-sm ml-1">{connected ? '🟢 实时' : '🟡 轮询'}</span>
        </div>
        <div className="flex gap-1" style={{ flexWrap: 'wrap' }}>
          {/* M3 一键风格复刻 */}
          <input ref={fileRef} type="file" accept="image/*" hidden
            onChange={handleReplicate} />
          <button className="btn btn-primary btn-sm" disabled={replicating || busy}
            onClick={() => fileRef.current?.click()}>
            {replicating ? '拆解风格中...' : '🎨 一键风格复刻'}
          </button>
          {job.status === 'paused' && (
            <button className="btn btn-primary btn-sm" disabled={busy} onClick={() => handleControl('run_next')}>⏭ 运行到下一步</button>
          )}
          {job.status === 'running' && (
            <button className="btn btn-warning btn-sm" disabled={busy} onClick={() => handleControl('pause')}>⏸ 暂停</button>
          )}
          {job.status === 'paused' && (
            <button className="btn btn-success btn-sm" disabled={busy} onClick={() => handleControl('resume')}>▶ 继续（自动挡）</button>
          )}
          {!terminal && (
            <button className="btn btn-danger btn-sm" disabled={busy} onClick={() => handleControl('cancel')}>取消</button>
          )}
          <button className="btn btn-ghost btn-sm" onClick={refresh}>刷新</button>
        </div>
      </div>

      {/* 人工审批 */}
      {job.status === 'waiting_human' && (
        <div className="card mb-2" style={{ borderColor: '#f59e0b' }}>
          <h3 className="mb-1">⚠ 等待人工审批</h3>
          <p className="text-sm mb-2">节点「{steps.find(s => s.status === 'waiting_human')?.node}」需要你的判断。</p>
          <div className="flex gap-1">
            <button className="btn btn-success btn-sm" disabled={busy} onClick={() => handleDecision('approve')}>✅ 通过</button>
            <button className="btn btn-warning btn-sm" disabled={busy} onClick={() => handleDecision('retry')}>🔄 重试</button>
            <button className="btn btn-danger btn-sm" disabled={busy} onClick={() => handleDecision('reject')}>❌ 拒绝</button>
          </div>
        </div>
      )}

      <div className="grid-2" style={{ alignItems: 'start' }}>
        {/* 画布：节点条 */}
        <div className="card">
          <h3 className="mb-2">执行画布</h3>
          <div className="node-flow">
            {steps.map((s, i) => {
              const style = STEP_STYLE[s.status] || STEP_STYLE.pending
              const isSelected = selected === s.node
              return (
                <div key={s.step_id} className="node-wrap">
                  <button
                    className={`node-card ${isSelected ? 'selected' : ''} step-${s.status}`}
                    style={{ background: style.bg, borderColor: isSelected ? '#93c5fd' : style.border }}
                    onClick={() => setSelected(isSelected ? null : s.node)}
                  >
                    <div className="flex items-center gap-1" style={{ flexWrap: 'wrap' }}>
                      <span>{NODE_ICONS[s.type] || '⬜'}</span>
                      <span className="strong" style={{ fontSize: 13 }}>{s.node}</span>
                    </div>
                    <div className="text-xs" style={{ color: style.dot, marginTop: 4 }}>
                      ● {style.label}{s.attempt > 0 ? ` · 第 ${s.attempt} 次` : ''}
                    </div>
                    {s.elapsed_ms > 0 && <div className="text-xs mt-1" style={{ color: '#64748b' }}>{Math.round(s.elapsed_ms)}ms · ${(s.cost_usd || 0).toFixed(4)}</div>}
                  </button>
                  {i < steps.length - 1 && <div className="node-arrow">→</div>}
                </div>
              )
            })}
          </div>
          <p className="text-xs mt-2 text-muted">点击节点查看输入/输出详情，并可重跑该步骤。</p>
        </div>

        {/* 节点详情 */}
        <div className="card">
          <h3 className="mb-1">{selectedStep ? `节点详情：${selectedStep.node}` : '节点详情'}</h3>
          {!selectedStep ? (
            <div className="empty-state"><p className="text-sm">点击左侧画布中的节点查看详情</p></div>
          ) : (
            <div>
              <div className="flex flex-wrap gap-1 mb-2">
                <span className="badge badge-info">{selectedStep.type}</span>
                <span className="badge badge-muted">状态: {selectedStep.status}</span>
                {selectedStep.elapsed_ms > 0 && <span className="badge badge-muted">{Math.round(selectedStep.elapsed_ms)}ms</span>}
              </div>
              {selectedStep.error && <div className="alert alert-error">❌ {selectedStep.error}</div>}
              {Object.keys(selectedStep.outputs || {}).length > 0 && (
                <div className="mb-1">
                  <p className="text-xs text-muted mb-1">输出</p>
                  <pre className="text-xs" style={{ background: '#0f172a', padding: 10, borderRadius: 6, overflowX: 'auto', color: '#94a3b8', maxHeight: 260, overflowY: 'auto' }}>
                    {JSON.stringify(selectedStep.outputs, null, 2)}
                  </pre>
                </div>
              )}
              <div className="flex gap-1 mt-2">
                <button className="btn btn-warning btn-sm" disabled={busy}
                  onClick={() => { if (confirm(`重跑节点「${selectedStep.node}」及其后续步骤？`)) handleControl('retry_step', selectedStep.node) }}>
                  🔁 重跑此步
                </button>
                {job.status === 'paused' && (
                  <button className="btn btn-ghost btn-sm" disabled={busy}
                    onClick={() => handleControl('skip_step', selectedStep.node)}>
                    ⏭️ 跳过此步
                  </button>
                )}
              </div>
            </div>
          )}
        </div>
      </div>

      {/* 事件流 */}
      <div className="card mt-2">
        <h3 className="mb-1">事件流（最近 {job.events?.length || 0} 条）</h3>
        <div className="flex flex-col gap-1" style={{ maxHeight: 220, overflowY: 'auto' }}>
          {(job.events || []).slice().reverse().map((ev, i) => (
            <div key={i} className="text-sm" style={{ display: 'flex', gap: 8 }}>
              <span className="text-xs mono" style={{ color: '#475569', width: 90, flexShrink: 0 }}>
                {new Date(ev.created_at).toLocaleTimeString()}
              </span>
              <span className="badge badge-muted">{ev.event}</span>
              <span className="text-xs text-muted" style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                {JSON.stringify(ev.payload || {}).slice(0, 120)}
              </span>
            </div>
          ))}
          {(job.events || []).length === 0 && <p className="text-sm text-muted">暂无事件</p>}
        </div>
      </div>
    </div>
  )
}
