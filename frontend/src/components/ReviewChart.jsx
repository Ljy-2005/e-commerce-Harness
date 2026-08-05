import { RadarChart, PolarGrid, PolarAngleAxis, PolarRadiusAxis, Radar, ResponsiveContainer } from 'recharts'

const LABELS = {
  texture: '质感', lighting: '光影', composition: '构图',
  product_fidelity: '商品还原', platform_fit: '平台适配'
}

export default function ReviewChart({ review, loading }) {
  // ── 加载中 ──
  if (loading) {
    return (
      <div className="card">
        <div className="flex items-center justify-between mb-1">
          <h3>审查评分</h3>
        </div>
        <div className="flex items-center justify-center" style={{height: 240}}>
          <div className="text-center">
            <div className="spinner" />
            <p className="text-sm mt-2">等待审查结果...</p>
          </div>
        </div>
      </div>
    )
  }

  // ── 无数据 ──
  if (!review?.dimension_scores) {
    return (
      <div className="card">
        <h3 className="mb-1">审查评分</h3>
        <div className="empty-state" style={{minHeight: 160}}>
          <p className="text-lg mb-1">📊</p>
          <p className="text-sm">暂无审查数据</p>
          <p className="text-xs mt-1">Agent 群聊完成后，审查员会给出 5 维度评分</p>
        </div>
      </div>
    )
  }

  const data = Object.entries(review.dimension_scores).map(([key, val]) => ({
    dimension: LABELS[key] || key,
    score: val || 0,
    fullMark: 100,
  }))

  const scoreColor = review.overall_score >= 75 ? '#22c55e' : review.overall_score >= 60 ? '#f59e0b' : '#ef4444'

  return (
    <div className="card">
      <h3 className="mb-1">
        审查评分
        <span className={`badge ml-1 ${review.overall_score >= 75 ? 'badge-ok' : review.overall_score >= 60 ? 'badge-warn' : 'badge-err'}`}>
          {review.overall_score}/100
        </span>
        <span className={`badge ml-1 ${review.verdict === 'pass' ? 'badge-ok' : 'badge-err'}`}>
          {review.verdict === 'pass' ? '✅ 通过' : review.verdict === 'retry' ? '🔄 重试' : review.verdict}
        </span>
      </h3>
      <ResponsiveContainer width="100%" height={240}>
        <RadarChart data={data}>
          <PolarGrid stroke="#334155" />
          <PolarAngleAxis dataKey="dimension" tick={{ fill: '#94a3b8', fontSize: 12 }} />
          <PolarRadiusAxis angle={90} domain={[0, 100]} tick={false} />
          <Radar dataKey="score" fill={scoreColor} fillOpacity={0.4} stroke={scoreColor} strokeWidth={2} />
        </RadarChart>
      </ResponsiveContainer>
      {review.top_praises?.length > 0 && (
        <p className="text-sm mt-1">👍 {review.top_praises.slice(0, 2).join(', ')}</p>
      )}
      {review.top_issues?.length > 0 && (
        <p className="text-sm">⚠ {review.top_issues.slice(0, 2).join(', ')}</p>
      )}
      {!review.top_praises?.length && !review.top_issues?.length && (
        <p className="text-xs mt-1" style={{color: '#64748b'}}>暂无详细评价</p>
      )}
    </div>
  )
}
