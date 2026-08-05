import { RadarChart, PolarGrid, PolarAngleAxis, PolarRadiusAxis, Radar, ResponsiveContainer } from 'recharts'

const LABELS = {
  texture: '质感', lighting: '光影', composition: '构图',
  product_fidelity: '商品还原', platform_fit: '平台适配'
}

export default function ReviewChart({ review }) {
  if (!review?.dimension_scores) return null

  const data = Object.entries(review.dimension_scores).map(([key, val]) => ({
    dimension: LABELS[key] || key,
    score: val || 0,
    fullMark: 100,
  }))

  return (
    <div className="card">
      <h3 className="mb-1">
        审查评分
        <span className={`badge ml-1 ${review.overall_score >= 75 ? 'badge-ok' : review.overall_score >= 60 ? 'badge-warn' : 'badge-err'}`}>
          {review.overall_score}/100
        </span>
        <span className={`badge ml-1 ${review.verdict === 'pass' ? 'badge-ok' : 'badge-err'}`}>
          {review.verdict}
        </span>
      </h3>
      <ResponsiveContainer width="100%" height={240}>
        <RadarChart data={data}>
          <PolarGrid stroke="#334155" />
          <PolarAngleAxis dataKey="dimension" tick={{ fill: '#94a3b8', fontSize: 12 }} />
          <PolarRadiusAxis angle={90} domain={[0, 100]} tick={false} />
          <Radar dataKey="score" fill="#3b82f6" fillOpacity={0.4} stroke="#60a5fa" strokeWidth={2} />
        </RadarChart>
      </ResponsiveContainer>
      {review.top_praises?.length > 0 && (
        <p className="text-sm">👍 {review.top_praises.slice(0, 2).join(', ')}</p>
      )}
      {review.top_issues?.length > 0 && (
        <p className="text-sm">⚠ {review.top_issues.slice(0, 2).join(', ')}</p>
      )}
    </div>
  )
}
