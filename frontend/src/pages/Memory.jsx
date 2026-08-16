import { useState, useEffect, useCallback } from 'react'
import { getMemoryStats, getMemoryRecall } from '../api'

export default function Memory() {
  const [stats, setStats] = useState(null)
  const [category, setCategory] = useState('')
  const [limit, setLimit] = useState(5)
  const [entries, setEntries] = useState([])
  const [recallError, setRecallError] = useState('')
  const [recalling, setRecalling] = useState(false)
  const [error, setError] = useState('')

  const refreshStats = useCallback(async () => {
    try {
      setStats(await getMemoryStats())
      setError('')
    } catch (err) {
      setError(err.message || '加载记忆库统计失败')
    }
  }, [])

  useEffect(() => { refreshStats() }, [refreshStats])

  async function handleRecall() {
    setRecalling(true)
    setRecallError('')
    try {
      const data = await getMemoryRecall(category, limit)
      setEntries(data?.entries || [])
    } catch (err) {
      setRecallError(err.message || '召回失败')
    }
    setRecalling(false)
  }

  return (
    <div>
      <h1 className="page-title">记忆库</h1>
      <p className="page-sub">成功会话的经验沉淀（分析模式 / 提示词 / 审查结论），供后续同类任务召回参考</p>

      {error && <div className="alert alert-error">{error}</div>}

      <div className="grid-4 mb-2">
        <div className="card stat-card">
          <div className="stat-value">{stats?.total_entries ?? 0}</div>
          <div className="stat-label">记忆条数</div>
        </div>
        <div className="card stat-card">
          <div className="stat-value">{stats?.avg_score ?? 0}</div>
          <div className="stat-label">平均评分</div>
        </div>
        <div className="card stat-card">
          <div className="stat-value">{stats?.best_score ?? 0}</div>
          <div className="stat-label">最高评分</div>
        </div>
        <div className="card stat-card">
          <div className="stat-value">{Object.keys(stats?.by_category || {}).length}</div>
          <div className="stat-label">覆盖品类</div>
        </div>
      </div>

      <div className="grid-2" style={{ alignItems: 'start' }}>
        {/* 品类分布 */}
        <div className="card">
          <h3 className="mb-1">品类分布</h3>
          {!stats?.by_category || Object.keys(stats.by_category).length === 0 ? (
            <div className="empty-state"><p className="text-sm">暂无记忆数据。会话成功完成后自动沉淀。</p></div>
          ) : (
            <div className="table-wrap">
              <table className="table">
                <thead><tr><th>品类</th><th>条数</th></tr></thead>
                <tbody>
                  {Object.entries(stats.by_category).sort((a, b) => b[1] - a[1]).map(([cat, n]) => (
                    <tr key={cat}><td className="strong">{cat}</td><td className="text-sm">{n}</td></tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>

        {/* 召回 */}
        <div className="card">
          <h3 className="mb-1">经验召回</h3>
          <div className="grid-2">
            <div className="form-group">
              <label className="label">品类</label>
              <input className="input" placeholder="如：保健品" value={category}
                onChange={e => setCategory(e.target.value)} />
            </div>
            <div className="form-group">
              <label className="label">条数</label>
              <input className="input" type="number" min="1" max="20" value={limit}
                onChange={e => setLimit(parseInt(e.target.value) || 5)} />
            </div>
          </div>
          <button className="btn btn-primary btn-sm" onClick={handleRecall} disabled={recalling}>
            {recalling ? '召回中...' : '🧠 召回经验'}
          </button>
          {recallError && <div className="alert alert-error mt-1">{recallError}</div>}
          <div className="mt-2 flex flex-col gap-1">
            {entries.map((e, i) => (
              <div key={i} className="card" style={{ padding: 12, background: '#0f172a' }}>
                <div className="flex items-center justify-between">
                  <span className="strong">{e.category || '通用'}</span>
                  <span className="badge badge-ok">评分 {e.score}/100</span>
                </div>
                {e.features?.length > 0 && <p className="text-sm mt-1">卖点: {e.features.slice(0, 5).join('、')}</p>}
                {e.prompts?.main && <p className="text-xs mt-1" style={{ color: '#94a3b8' }}>提示词: {e.prompts.main.slice(0, 100)}…</p>}
                {e.top_praises?.length > 0 && <p className="text-xs mt-1">👍 {e.top_praises.slice(0, 2).join('，')}</p>}
                <p className="text-xs mt-1 mono" style={{ color: '#475569' }}>来源会话: {e.session_id?.slice(0, 12)}…</p>
              </div>
            ))}
            {entries.length === 0 && !recalling && (
              <p className="text-sm text-muted mt-1">输入品类后点击召回。</p>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}
