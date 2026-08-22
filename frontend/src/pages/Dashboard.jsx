import { useState, useEffect, useCallback } from 'react'
import { Link } from 'react-router-dom'
import { getHealth, getAdminStatus, getSessions } from '../api'

const CIRCUIT_STATE_BADGE = {
  closed: 'badge-ok',
  open: 'badge-err',
  half_open: 'badge-warn',
}
const CIRCUIT_STATE_LABEL = { closed: '闭合', open: '熔断', half_open: '半开' }

export default function Dashboard() {
  const [health, setHealth] = useState(null)
  const [status, setStatus] = useState(null)
  const [sessions, setSessions] = useState([])
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)

  const refresh = useCallback(async () => {
    try {
      const [h, st, ss] = await Promise.all([
        getHealth(), getAdminStatus(), getSessions(),
      ])
      setHealth(h)
      setStatus(st)
      setSessions(ss?.sessions || [])
      setError('')
    } catch (err) {
      setError(err.message || '加载系统状态失败')
    }
    setLoading(false)
  }, [])

  useEffect(() => {
    refresh()
    const t = setInterval(refresh, 5000)
    return () => clearInterval(t)
  }, [refresh])

  if (loading) {
    return (
      <div className="card text-center" style={{ minHeight: 200, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
        <div><div className="spinner" /><p className="text-sm mt-2">加载系统状态...</p></div>
      </div>
    )
  }

  if (error && !status) {
    return (
      <div className="card card-error mt-2">
        <h3 className="mb-1">无法连接后端</h3>
        <p className="text-sm">{error}</p>
        <p className="text-xs mt-1">请确认后端运行在 http://localhost:8000（前端通过代理访问）</p>
        <button className="btn btn-primary mt-2" onClick={refresh}>重试</button>
      </div>
    )
  }

  const running = sessions.filter(s => s.status === 'running' || s.status === 'waiting_human').length
  const completed = sessions.filter(s => s.status === 'completed').length

  return (
    <div>
      <h1 className="page-title">系统仪表盘</h1>
      <p className="page-sub">
        运行状态
        <span className="badge ml-1">{health?.mock_mode ? 'Mock 模式' : '真实 API 模式'}</span>
        <span className={`badge ml-1 ${health?.status === 'healthy' ? 'badge-ok' : 'badge-err'}`}>
          {health?.status === 'healthy' ? '🟢 健康' : '🔴 异常'}
        </span>
      </p>

      {/* 审计修复 L6：首次加载成功后，轮询失败不再静默降级——显示过期提示横幅 */}
      {error && status && (
        <div className="alert alert-warn mb-2">
          ⚠ 状态刷新失败（展示数据可能已过期）：{error}
          <button className="btn btn-ghost btn-sm ml-1" onClick={refresh}>重试</button>
        </div>
      )}

      {/* 统计卡 */}
      <div className="grid-4 mb-2">
        <div className="card stat-card">
          <div className="stat-value">{sessions.length}</div>
          <div className="stat-label">总会话数</div>
        </div>
        <div className="card stat-card">
          <div className="stat-value">{running}</div>
          <div className="stat-label">运行中 / 待人工</div>
        </div>
        <div className="card stat-card">
          <div className="stat-value">{completed}</div>
          <div className="stat-label">已完成</div>
        </div>
        <div className="card stat-card">
          <div className="stat-value">{status?.agents?.length ?? 0}</div>
          <div className="stat-label">已注册 Agent</div>
        </div>
      </div>

      <div className="grid-2">
        {/* Provider 状态 */}
        <div className="card">
          <h3>Provider 状态</h3>
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr><th>Provider</th><th>能力</th><th>熔断器</th><th>限流剩余</th></tr>
              </thead>
              <tbody>
                {(status?.providers || []).map(p => {
                  const cb = status?.harness?.circuits?.[p.name]
                  const rate = status?.harness?.rate_limiter?.[p.name]
                  return (
                    <tr key={p.name}>
                      <td className="strong">{p.name}</td>
                      <td className="text-sm">{(p.capabilities || []).join(' / ')}</td>
                      <td>
                        {cb ? (
                          <span className={`badge ${CIRCUIT_STATE_BADGE[cb.state] || 'badge-muted'}`}>
                            {CIRCUIT_STATE_LABEL[cb.state] || cb.state}
                          </span>
                        ) : <span className="text-xs">—</span>}
                      </td>
                      <td className="text-sm">{rate ?? '—'}</td>
                    </tr>
                  )
                })}
                {(status?.providers || []).length === 0 && (
                  <tr><td colSpan="4" className="text-center text-sm">无可用 Provider</td></tr>
                )}
              </tbody>
            </table>
          </div>
        </div>

        {/* 租户 */}
        <div className="card">
          <h3>租户与最近会话</h3>
          <div className="text-sm mb-2">
            租户总数: <span className="strong">{status?.tenants?.total ?? 0}</span>
            {status?.tenants?.active_sessions && Object.keys(status.tenants.active_sessions).length > 0 && (
              <span> · 活跃会话租户: {Object.keys(status.tenants.active_sessions).map(t => `${t}(${status.tenants.active_sessions[t]})`).join(', ')}</span>
            )}
          </div>
          {sessions.length === 0 ? (
            <div className="empty-state">
              <p className="text-lg mb-1">📭</p>
              <p className="text-sm">暂无会话</p>
              <Link to="/sessions" className="btn btn-primary btn-sm mt-2">创建第一个任务</Link>
            </div>
          ) : (
            <div className="table-wrap">
              <table className="table">
                <thead>
                  <tr><th>会话</th><th>状态</th><th>轮次</th><th>成本</th></tr>
                </thead>
                <tbody>
                  {sessions.slice(0, 8).map(s => (
                    <tr key={s.session_id}>
                      <td>
                        <Link to={`/session/${s.session_id}`} className="mono">
                          {s.session_id.slice(0, 8)}…
                        </Link>
                      </td>
                      <td><StatusBadge status={s.status} /></td>
                      <td className="text-sm">{s.turn_count}</td>
                      <td className="text-sm">${(s.cost_so_far || 0).toFixed(4)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </div>

      {/* 快捷入口 */}
      <div className="grid-4 mt-2">
        <Link to="/sessions" className="card lift text-center" style={{ display: 'block' }}>
          <div className="text-lg">💬</div>
          <div className="strong mt-1">新建任务</div>
          <div className="text-xs mt-1">上传商品图，启动 Agent 群聊</div>
        </Link>
        <Link to="/agents" className="card lift text-center" style={{ display: 'block' }}>
          <div className="text-lg">🤖</div>
          <div className="strong mt-1">Agent 配置</div>
          <div className="text-xs mt-1">调整各 Agent 的分析参数</div>
        </Link>
        <Link to="/settings" className="card lift text-center" style={{ display: 'block' }}>
          <div className="text-lg">⚙️</div>
          <div className="strong mt-1">API Key</div>
          <div className="text-xs mt-1">配置真实模型服务的密钥</div>
        </Link>
        <Link to="/audit" className="card lift text-center" style={{ display: 'block' }}>
          <div className="text-lg">📜</div>
          <div className="strong mt-1">审计日志</div>
          <div className="text-xs mt-1">每次 Agent 调用的成本与耗时</div>
        </Link>
      </div>
    </div>
  )
}

export function StatusBadge({ status }) {
  const map = {
    created: 'badge-muted',
    running: 'badge-info',
    completed: 'badge-ok',
    waiting_human: 'badge-warn',
    failed: 'badge-err',
  }
  const label = {
    created: '已创建',
    running: '运行中',
    completed: '已完成',
    waiting_human: '待人工审查',
    failed: '失败',
  }
  return <span className={`badge ${map[status] || 'badge-muted'}`}>{label[status] || status}</span>
}
