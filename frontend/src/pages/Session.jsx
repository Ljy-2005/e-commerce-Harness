import { useState, useEffect, useCallback } from 'react'
import { useParams, Link } from 'react-router-dom'
import { getSession, deleteSession } from '../api'
import ChatPanel from '../components/ChatPanel'
import ReviewChart from '../components/ReviewChart'
import HITLPanel from '../components/HITLPanel'

export default function Session() {
  const { id } = useParams()
  const [session, setSession] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [deleting, setDeleting] = useState(false)

  const refresh = useCallback(async () => {
    try {
      const data = await getSession(id)
      setSession(data)
      setError('')
    } catch (err) {
      setError(err.message || '加载会话失败')
    }
    setLoading(false)
  }, [id])

  useEffect(() => {
    refresh()
    const t = setInterval(refresh, 3000)
    return () => clearInterval(t)
  }, [refresh])

  async function handleDelete() {
    if (!confirm('确定删除此会话？')) return
    setDeleting(true)
    try {
      await deleteSession(id)
      window.location.href = '/'
    } catch (err) {
      alert('删除失败: ' + (err.message || '未知错误'))
      setDeleting(false)
    }
  }

  // ── 错误状态 ──
  if (error && !session) {
    return (
      <div className="card card-error mt-2 text-center">
        <h3 className="mb-1">加载失败</h3>
        <p className="text-sm">{error}</p>
        <button className="btn btn-primary mt-2" onClick={refresh}>重试</button>
        <Link to="/" className="btn btn-primary mt-1" style={{marginLeft: 8}}>返回首页</Link>
      </div>
    )
  }

  // ── 加载中 ──
  if (loading) {
    return (
      <div className="card mt-2 text-center" style={{minHeight: 200, display: 'flex', alignItems: 'center', justifyContent: 'center'}}>
        <div>
          <div className="spinner" />
          <p className="text-sm mt-2">加载会话...</p>
        </div>
      </div>
    )
  }

  // ── 会话不存在 ──
  if (!session) {
    return (
      <div className="card text-center mt-2">
        <p className="text-lg mb-2">📭</p>
        <p>会话不存在或已被删除</p>
        <Link to="/" className="btn btn-primary mt-2">返回首页</Link>
      </div>
    )
  }

  const status = session.status
  const artifacts = session.artifacts || {}

  return (
    <div>
      <div className="flex items-center justify-between mb-2">
        <div>
          <Link to="/" className="text-sm">← 返回</Link>
          <h2 style={{display:'inline', marginLeft:16}}>会话 {id?.slice(0, 12)}</h2>
          <span className={`badge ml-1 ${status === 'completed' ? 'badge-ok' : status === 'waiting_human' ? 'badge-warn' : status === 'failed' ? 'badge-err' : 'badge-info'}`}>{status}</span>
          {session.turn_count > 0 && <span className="text-sm ml-1">轮次: {session.turn_count}</span>}
          {session.cost_so_far > 0 && <span className="text-sm ml-1">费用: ${session.cost_so_far.toFixed(4)}</span>}
        </div>
        <button className="btn btn-danger" onClick={handleDelete} disabled={deleting}>
          {deleting ? '删除中...' : '删除'}
        </button>
      </div>

      <div className="grid-2">
        <div>
          <ChatPanel sessionId={id} onNewMessage={refresh} />
        </div>
        <div className="flex flex-col gap-2">
          {artifacts.review && (
            <ReviewChart review={artifacts.review} />
          )}
          {status === 'waiting_human' && (
            <HITLPanel sessionId={id} onDecision={refresh} />
          )}
          {artifacts.analysis && (
            <div className="card">
              <h3 className="mb-1">商品分析</h3>
              <p><strong>品类:</strong> {artifacts.analysis.category || '未知'}</p>
              {artifacts.analysis.confidence_score > 0 && (
                <p><strong>置信度:</strong> {artifacts.analysis.confidence_score}%</p>
              )}
              {artifacts.analysis.features?.length > 0 && (
                <p className="text-sm">{artifacts.analysis.features.join(', ')}</p>
              )}
            </div>
          )}
          {artifacts.compliance && (
            <div className="card">
              <h3 className="mb-1">
                合规检查
                <span className={`badge ml-1 ${artifacts.compliance.passed ? 'badge-ok' : 'badge-err'}`}>
                  {artifacts.compliance.passed ? '通过' : '不通过'}
                </span>
              </h3>
              <p className="text-sm">风险: {artifacts.compliance.risk_level}</p>
              {artifacts.compliance.warnings?.length > 0 && (
                <p className="text-sm">⚠ {artifacts.compliance.warnings.join(', ')}</p>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
