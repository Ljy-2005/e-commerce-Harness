import { useState, useEffect, useCallback, useRef } from 'react'
import { getAudit } from '../api'

export default function Audit() {
  // 输入框状态与已提交查询条件分离（审计修复：此前每次击键都触发请求）
  const [sessionId, setSessionId] = useState('')
  const [agent, setAgent] = useState('')
  const [date, setDate] = useState('')
  const [query, setQuery] = useState({ sessionId: '', agent: '', date: '' })
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const seqRef = useRef(0)  // 过期响应丢弃（竞态防护）

  const refresh = useCallback(async () => {
    const seq = ++seqRef.current
    setLoading(true)
    try {
      const d = await getAudit({ sessionId: query.sessionId, agent: query.agent, date: query.date })
      if (seq !== seqRef.current) return
      setData(d)
      setError('')
    } catch (err) {
      if (seq !== seqRef.current) return
      setError(err.message || '查询审计日志失败')
    }
    if (seq === seqRef.current) setLoading(false)
  }, [query])

  useEffect(() => { refresh() }, [refresh])

  function clearAll() {
    setSessionId('')
    setAgent('')
    setDate('')
    setQuery({ sessionId: '', agent: '', date: '' })
  }

  return (
    <div>
      <h1 className="page-title">审计日志</h1>
      <p className="page-sub">每次 Agent 调用的 provider / 模型 / 耗时 / token / 成本记录（data/audit/*.jsonl）</p>

      {/* 查询条件 */}
      <div className="card mb-2">
        <div className="grid-3" style={{ alignItems: 'end' }}>
          <div className="form-group" style={{ marginBottom: 0 }}>
            <label className="label">会话 ID（可选）</label>
            <input className="input mono" placeholder="留空查询全部" value={sessionId}
              onChange={e => setSessionId(e.target.value.trim())} />
          </div>
          <div className="form-group" style={{ marginBottom: 0 }}>
            <label className="label">Agent（可选）</label>
            <input className="input" placeholder="如：提示词生成员" value={agent}
              onChange={e => setAgent(e.target.value.trim())} />
          </div>
          <div className="form-group" style={{ marginBottom: 0 }}>
            <label className="label">日期（可选，YYYY-MM-DD）</label>
            <input className="input" type="date" value={date}
              onChange={e => setDate(e.target.value)} />
          </div>
        </div>
        <div className="flex gap-1 mt-2">
          <button className="btn btn-primary btn-sm" onClick={() => setQuery({ sessionId, agent, date })} disabled={loading}>
            {loading ? '查询中...' : '查询'}
          </button>
          <button className="btn btn-ghost btn-sm" onClick={clearAll}>
            清空条件
          </button>
        </div>
      </div>

      {error && <div className="alert alert-error">{error}</div>}

      {/* 统计 */}
      {data?.stats && (
        <div className="grid-4 mb-2">
          {Object.entries(data.stats).map(([k, v]) => (
            <div key={k} className="card stat-card">
              <div className="stat-value" style={{ fontSize: 18 }}>{typeof v === 'number' ? v : String(v)}</div>
              <div className="stat-label">{k}</div>
            </div>
          ))}
        </div>
      )}

      {/* 条目表 */}
      <div className="card">
        <div className="flex items-center justify-between mb-1">
          <h3 style={{ marginBottom: 0 }}>日志条目（最近 {data?.total || 0} 条中的最新 50 条）</h3>
          <span className="text-xs">共 {data?.total || 0} 条</span>
        </div>
        {(!data || data.entries.length === 0) ? (
          <div className="empty-state">
            <p className="text-lg mb-1">📜</p>
            <p className="text-sm">暂无审计日志。运行会话后，每次 Agent 调用都会记录在这里。</p>
          </div>
        ) : (
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th>时间</th><th>Agent</th><th>Provider</th><th>模型</th>
                  <th>动作</th><th>耗时</th><th>Tokens</th><th>成本</th><th>状态</th><th>会话</th>
                </tr>
              </thead>
              <tbody>
                {data.entries.slice().reverse().map((e, i) => (
                  <tr key={i}>
                    <td className="text-xs">{new Date(e.timestamp).toLocaleString()}</td>
                    <td className="strong">{e.agent}</td>
                    <td className="text-sm">{e.provider}</td>
                    <td className="text-sm mono">{e.model}</td>
                    <td className="text-sm">{e.action}</td>
                    <td className="text-sm">{e.duration_ms}ms</td>
                    <td className="text-sm">{e.tokens}</td>
                    <td className="text-sm">${(e.cost_usd || 0).toFixed(6)}</td>
                    <td>
                      <span className={`badge ${e.status === 'ok' ? 'badge-ok' : 'badge-err'}`}>{e.status}</span>
                    </td>
                    <td className="text-xs mono">{e.session_id?.slice(0, 10)}…</td>
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
