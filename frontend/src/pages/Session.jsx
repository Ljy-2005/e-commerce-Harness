import { useState, useEffect, useCallback } from 'react'
import { useParams, Link } from 'react-router-dom'
import { getSession, deleteSession } from '../api'
import { StatusBadge } from './Dashboard'
import ChatPanel from '../components/ChatPanel'
import ReviewChart from '../components/ReviewChart'
import HITLPanel from '../components/HITLPanel'
import ABTestPanel from '../components/ABTestPanel'

const TABS = [
  { key: 'outputs', label: '📦 产出物' },
  { key: 'images', label: '🖼️ 生成图片' },
  { key: 'review', label: '⭐ 审查评分' },
  { key: 'ab', label: '🔬 A/B 测试' },
]

export default function Session() {
  const { id } = useParams()
  const [session, setSession] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [tab, setTab] = useState('outputs')
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

  // 审计修复：路由参数变化时重置状态，避免新 URL 短暂渲染上一实体的数据
  useEffect(() => {
    setSession(null)
    setError('')
    setLoading(true)
    setTab('outputs')
  }, [id])

  useEffect(() => {
    refresh()
    const t = setInterval(refresh, 3000)
    return () => clearInterval(t)
  }, [refresh])

  async function handleDelete() {
    if (!confirm('确定删除此会话及全部产出物？')) return
    setDeleting(true)
    try {
      await deleteSession(id)
      window.location.href = '/sessions'
    } catch (err) {
      alert('删除失败: ' + (err.message || '未知错误'))
      setDeleting(false)
    }
  }

  if (error && !session) {
    return (
      <div className="card card-error mt-2 text-center">
        <h3 className="mb-1">加载失败</h3>
        <p className="text-sm">{error}</p>
        <div className="mt-2">
          <button className="btn btn-primary" onClick={refresh}>重试</button>
          <Link to="/sessions" className="btn btn-ghost ml-1">返回会话列表</Link>
        </div>
      </div>
    )
  }

  if (loading) {
    return (
      <div className="card mt-2 text-center" style={{ minHeight: 200, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
        <div><div className="spinner" /><p className="text-sm mt-2">加载会话...</p></div>
      </div>
    )
  }

  if (!session) {
    return (
      <div className="card text-center mt-2">
        <p className="text-lg mb-2">📭</p>
        <p>会话不存在或已被删除</p>
        <Link to="/sessions" className="btn btn-primary mt-2">返回会话列表</Link>
      </div>
    )
  }

  const artifacts = session.artifacts || {}

  return (
    <div>
      {/* 头部 */}
      <div className="flex items-center justify-between mb-2" style={{ flexWrap: 'wrap', gap: 8 }}>
        <div>
          <Link to="/sessions" className="text-sm">← 会话列表</Link>
          <h1 className="page-title" style={{ display: 'inline', marginLeft: 12, fontSize: 18 }}>
            会话 <span className="mono" style={{ fontSize: 14 }}>{id?.slice(0, 12)}…</span>
          </h1>
          <span className="ml-1"><StatusBadge status={session.status} /></span>
          <span className="text-sm ml-1">轮次: <span className="strong">{session.turn_count}</span></span>
          <span className="text-sm ml-1">成本: <span className="strong">${(session.cost_so_far || 0).toFixed(4)}</span></span>
        </div>
        <div className="flex gap-1">
          <button className="btn btn-ghost btn-sm" onClick={refresh}>刷新</button>
          <button className="btn btn-danger btn-sm" onClick={handleDelete} disabled={deleting}>
            {deleting ? '删除中...' : '删除会话'}
          </button>
        </div>
      </div>

      {/* 人工审查提示 */}
      {session.status === 'waiting_human' && (
        <div className="mt-1 mb-2">
          <HITLPanel sessionId={id} onDecision={refresh} />
        </div>
      )}

      <div className="grid-2" style={{ alignItems: 'start' }}>
        {/* 左：群聊直播 */}
        <ChatPanel sessionId={id} status={session.status} onNewMessage={refresh} />

        {/* 右：产出物 */}
        <div className="flex flex-col gap-2">
          <div className="card" style={{ padding: 12 }}>
            <div className="tabs" style={{ marginBottom: 0 }}>
              {TABS.map(t => (
                <button
                  key={t.key}
                  className={`tab ${tab === t.key ? 'active' : ''}`}
                  onClick={() => setTab(t.key)}
                >
                  {t.label}
                </button>
              ))}
            </div>
            <div className="mt-2">
              {tab === 'outputs' && <OutputsTab artifacts={artifacts} />}
              {tab === 'images' && <ImagesTab images={artifacts.images || []} />}
              {tab === 'review' && <ReviewTab artifacts={artifacts} />}
              {tab === 'ab' && <ABTestPanel sessionId={id} onDone={refresh} />}
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}

// ── 产出物 ──

function OutputsTab({ artifacts }) {
  if (!Object.keys(artifacts).length) {
    return <div className="empty-state"><p className="text-sm">Agent 尚未产出任何结果</p></div>
  }
  return (
    <div className="flex flex-col gap-2">
      {artifacts.analysis && <AnalysisCard analysis={artifacts.analysis} />}
      {artifacts.prompts && <PromptsCard prompts={artifacts.prompts} />}
      {artifacts.compliance && <ComplianceCard compliance={artifacts.compliance} />}
      {artifacts.ab_test && (
        <div className="card">
          <h3 className="mb-1">A/B 测试结果</h3>
          <p className="text-sm">
            优胜: <span className="strong">{artifacts.ab_test.winner || '无'}</span>
            {artifacts.ab_test.winner_score > 0 && <span> ({artifacts.ab_test.winner_score}/100)</span>}
          </p>
        </div>
      )}
      {!artifacts.analysis && !artifacts.prompts && !artifacts.compliance && !artifacts.ab_test && (
        <div className="empty-state"><p className="text-sm">产出物结构: {Object.keys(artifacts).join(', ') || '空'}</p></div>
      )}
    </div>
  )
}

function AnalysisCard({ analysis }) {
  return (
    <div className="card">
      <h3 className="mb-1">
        🔍 商品分析
        <span className="badge badge-info ml-1">{analysis.category || '未知品类'}</span>
        {analysis.confidence_score > 0 && (
          <span className={`badge ml-1 ${analysis.confidence_score >= 80 ? 'badge-ok' : 'badge-warn'}`}>
            置信度 {analysis.confidence_score}%
          </span>
        )}
      </h3>
      {analysis.features?.length > 0 && (
        <p className="text-sm mb-1">卖点: {analysis.features.join('、')}</p>
      )}
      {analysis.special_constraints?.length > 0 && (
        <p className="text-sm mb-1">约束: {analysis.special_constraints.join('；')}</p>
      )}
      {analysis.target_audience && Object.keys(analysis.target_audience).length > 0 && (
        <p className="text-sm mb-1">
          人群: {Object.entries(analysis.target_audience).map(([k, v]) => `${k}: ${v}`).join('，')}
        </p>
      )}
      <DetailsJson label="完整分析结果" data={analysis} />
    </div>
  )
}

function PromptsCard({ prompts }) {
  const main = prompts.main_image || {}
  const scenes = prompts.scene_images || []
  const socials = prompts.social_images || []
  return (
    <div className="card">
      <h3 className="mb-1">✍️ 提示词方案</h3>
      {main.prompt && (
        <div className="mb-1">
          <p className="text-xs text-muted">主图提示词</p>
          <p className="text-sm" style={{ color: '#cbd5e1' }}>{main.prompt.slice(0, 120)}{main.prompt.length > 120 ? '…' : ''}</p>
          {main.negative_prompt && <p className="text-xs">负向: {main.negative_prompt.slice(0, 80)}</p>}
        </div>
      )}
      <p className="text-sm">场景图 {scenes.length} 张 · 社交图 {socials.length} 张</p>
      <div className="flex flex-wrap gap-1 mt-1">
        {scenes.map((s, i) => <span key={i} className="badge badge-info">{s.scene_type || `场景${i + 1}`}</span>)}
        {socials.map((s, i) => <span key={i} className="badge badge-info">{s.scene_type || `社交${i + 1}`}</span>)}
      </div>
      <DetailsJson label="完整提示词" data={prompts} />
    </div>
  )
}

function ComplianceCard({ compliance }) {
  return (
    <div className="card">
      <h3 className="mb-1">
        🛡️ 合规检查
        <span className={`badge ml-1 ${compliance.passed ? 'badge-ok' : 'badge-err'}`}>
          {compliance.passed ? '通过' : '不通过'}
        </span>
        <span className={`badge ml-1 ${compliance.risk_level === 'low' ? 'badge-ok' : compliance.risk_level === 'medium' ? 'badge-warn' : 'badge-err'}`}>
          风险: {compliance.risk_level}
        </span>
      </h3>
      {compliance.violations?.length > 0 && (
        <p className="text-sm">❌ 违规: {compliance.violations.join('；')}</p>
      )}
      {compliance.warnings?.length > 0 && (
        <p className="text-sm">⚠ 警告: {compliance.warnings.join('；')}</p>
      )}
      {compliance.suggestions?.length > 0 && (
        <p className="text-sm">💡 建议: {compliance.suggestions.join('；')}</p>
      )}
    </div>
  )
}

function DetailsJson({ label, data }) {
  const [open, setOpen] = useState(false)
  return (
    <div className="mt-1">
      <button className="expand-btn" onClick={() => setOpen(!open)} style={{ background: 'none', border: 'none', color: '#60a5fa', cursor: 'pointer', fontSize: 12 }}>
        {open ? '▲ 收起' : '▼ 查看'} {label}
      </button>
      {open && (
        <pre className="text-xs mt-1" style={{ background: '#0f172a', padding: 10, borderRadius: 6, overflowX: 'auto', color: '#94a3b8' }}>
          {JSON.stringify(data, null, 2)}
        </pre>
      )}
    </div>
  )
}

// ── 图片 ──

function isPlaceholder(img) {
  return (img.model_used || '').startsWith('mock') || (img.image_url || '').startsWith('data:image/svg+xml')
}

function ImagesTab({ images }) {
  if (!images || images.length === 0) {
    return <div className="empty-state"><p className="text-sm">暂无生成图片（生图员产出后显示）</p></div>
  }
  const allPlaceholders = images.every(isPlaceholder)
  return (
    <div>
      {allPlaceholders && (
        <div className="alert alert-warn">
          ⚠ 当前展示的是<b>占位图</b>：未配置生图模型（DeepSeek 只能看图/写字，不能生成图片）。
          在设置页配置 DALL-E / 即梦 Seedream / FLUX 任一 Key 后，生图员将生成真实商品图。
        </div>
      )}
      <div className="image-grid">
        {images.map((img, i) => {
          const src = img.image_url || (img.base64_data ? `data:image/png;base64,${img.base64_data}` : '')
          const placeholder = isPlaceholder(img)
          return (
            <div key={i} className={`image-cell ${placeholder ? 'image-cell-placeholder' : ''}`}>
              {src ? <img src={src} alt={img.prompt_name || `生成图 ${i + 1}`} loading="lazy" /> : (
                <div className="flex items-center justify-center" style={{ height: 160 }}><span className="text-xs">无图片数据</span></div>
              )}
              <div className="img-caption">
                {img.prompt_name && <div className="strong" style={{ fontSize: 12 }}>{img.prompt_name}</div>}
                {placeholder
                  ? <div><span className="badge badge-warn">占位图</span></div>
                  : img.model_used && <div>模型: {img.model_used}</div>}
                {img.processing_status && <div>状态: {img.processing_status}</div>}
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}

// ── 审查 ──

function ReviewTab({ artifacts }) {
  const review = artifacts.review
  if (!review) {
    return <div className="empty-state"><p className="text-sm">暂无审查数据（审查员产出后显示）</p></div>
  }
  return (
    <div className="flex flex-col gap-2">
      <ReviewChart review={review} />
      {review.dimension_scores && Object.keys(review.dimension_scores).length > 0 && (
        <div className="card">
          <h3 className="mb-1">维度明细</h3>
          <div className="table-wrap">
            <table className="table">
              <tbody>
                {Object.entries(review.dimension_scores).map(([k, v]) => (
                  <tr key={k}>
                    <td className="text-sm" style={{ width: 120 }}>{k}</td>
                    <td>
                      <div style={{ height: 6, background: '#0f172a', borderRadius: 3, overflow: 'hidden' }}>
                        <div style={{ width: `${Math.min(v || 0, 100)}%`, height: '100%', background: v >= 75 ? '#22c55e' : v >= 60 ? '#f59e0b' : '#ef4444' }} />
                      </div>
                    </td>
                    <td className="text-sm" style={{ width: 60, textAlign: 'right' }}>{v}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  )
}
