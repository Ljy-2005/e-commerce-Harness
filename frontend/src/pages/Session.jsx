import { useState, useEffect, useCallback } from 'react'
import { useParams, Link } from 'react-router-dom'
import { getSession, deleteSession, downloadSessionImage, exportSessionImages,
         saveSessionFacts, interjectSession, setSessionStyle } from '../api'
import { formatCost, costTitle, sessionCostMeta } from '../cost'
import { displayContent, hiddenKeys, rawContentText, fieldLabel, INTERNAL_KEYS } from '../chatFormat'
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

/** error_history 条目类型 → 中文标签（B3-25） */
const ERROR_KIND = {
  agent: 'Agent 报错',
  abort: '任务中止',
  human_reject: '人工拒绝',
}

export default function Session() {
  const { id } = useParams()
  const [session, setSession] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [tab, setTab] = useState('outputs')
  const [deleting, setDeleting] = useState(false)
  const [styleBusy, setStyleBusy] = useState(false)

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

  /** 换风格（一轮会话一套风格词：会话一旦定下就锁住，要换得显式操作） */
  const handleSwitchStyle = useCallback(async () => {
    const current = session?.artifacts?.prompts?.style_refs?.active_entry || null
    const tip = current
      ? `本轮已固定用「${current.name}」。换风格需填另一套风格词的 id（留空 = 回到词库里启用那一套）：`
      : '填一套风格词的 id（留空 = 回到词库里启用那一套）：'
    const entryId = window.prompt(tip, current?.id || '')
    if (entryId === null) return
    setStyleBusy(true)
    try {
      const res = await setSessionStyle(id, entryId.trim())
      setError('')
      window.alert(res?.message || '已切换风格；请重新出提示词/出图以应用')
      await refresh()
    } catch (err) {
      setError(err.message || '切换风格失败')
    }
    setStyleBusy(false)
  }, [id, session, refresh])

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
          <span className="text-sm ml-1">成本: <span className="strong" title={costTitle(sessionCostMeta(session))}>{formatCost(session.cost_so_far, sessionCostMeta(session))}</span></span>
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

      {/* 失败原因（B3-25）：error_history 由引擎写入；附审计页深链便于看上游原文 */}
      {(session.status === 'failed' || (session.error_history || []).length > 0) && (
        <div className="card card-error mb-2">
          <h3 className="mb-1">❌ 会话失败原因</h3>
          {(session.error_history || []).length === 0 ? (
            <p className="text-sm">
              未记录具体原因（可能是启动时从 checkpoint 恢复的未完成会话）。
            </p>
          ) : (
            <ul className="error-history">
              {(session.error_history || []).slice(-5).reverse().map((e, i) => (
                <li key={i} className="text-sm">
                  <span className="chip">{ERROR_KIND[e.kind] || e.kind || '错误'}</span>{' '}
                  <span className="strong">{e.agent}</span>：
                  <span className="mono" style={{ wordBreak: 'break-all' }}>{e.error}</span>
                </li>
              ))}
            </ul>
          )}
          <Link className="text-sm" to={`/audit?session=${id}`}>查看该会话的审计日志 →</Link>
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
              {tab === 'outputs' && (
                <OutputsTab artifacts={artifacts} onSwitchStyle={handleSwitchStyle}
                            styleBusy={styleBusy} />
              )}
              {tab === 'images' && <ImagesTab images={artifacts.images || []} sessionId={id} />}
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

function OutputsTab({ artifacts, onSwitchStyle, styleBusy = false }) {
  if (!Object.keys(artifacts).length) {
    return <div className="empty-state"><p className="text-sm">Agent 尚未产出任何结果</p></div>
  }
  return (
    <div className="flex flex-col gap-2">
      {artifacts.product_identity && <IdentityCard identity={artifacts.product_identity} />}
      {artifacts.analysis && <AnalysisCard analysis={artifacts.analysis} />}
      {(artifacts.prompt_lint || artifacts.prompt_review || artifacts.prompts?.style_refs) && (
        <PromptAuditCard lint={artifacts.prompt_lint} review={artifacts.prompt_review}
                         styleRefs={artifacts.prompts?.style_refs}
                         onSwitchStyle={onSwitchStyle} styleBusy={styleBusy} />
      )}
      {artifacts.set_plan && <SetPlanCard plan={artifacts.set_plan} coverage={artifacts.set_plan_coverage} />}
      {artifacts.prompts && <PromptsCard prompts={artifacts.prompts} />}
      {artifacts.quality_report && <QualityCard report={artifacts.quality_report} />}
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

// 提示词体检 + 审美审核（用户："让提示词更接近大众商品图审美"）
// 用户能看到"哪一张被改写了、为什么、还剩几条意见"，而不是一坨 JSON
function PromptAuditCard({ lint, review, styleRefs, onSwitchStyle, styleBusy = false }) {
  const findings = lint?.findings || []
  const scores = review?.scores || {}
  const revised = review?.revised_slots || []
  const rejected = review?.refine_rejected || []
  return (
    <div className="card">
      <h3 className="mb-1">
        🧪 提示词审核
        <span className={`badge ml-1 ${lint?.errors?.length ? 'badge-err' : 'badge-ok'}`}>
          {lint?.errors?.length ? `${lint.errors.length} 项硬伤` : '体检通过'}
        </span>
        {review?.threshold != null && (
          <span className="badge badge-info ml-1">审美阈值 {review.threshold}</span>
        )}
      </h3>
      {/* 🎨 采用风格词条/原型（artifacts.prompts.style_refs；没有这个字段就整行不显示） */}
      {styleRefs && (
        <div className="text-sm mb-1" data-testid="session-style-refs">
          🎨 采用风格词条/原型：
          <span className="strong">{styleRefs.message || '🎨 采用风格档案'}</span>
          {/* 会话锁：风格在这一轮里是固定的（改词库的启用项不影响进行中的会话） */}
          {styleRefs.active_entry && (
            <span className="chip ml-1" data-testid="session-style-locked">
              {styleRefs.locked_entry_id ? '本轮已固定' : '本次会话'}
            </span>
          )}
          {styleRefs.locked_entry_id && onSwitchStyle && (
            <button className="btn btn-ghost btn-sm ml-1" disabled={styleBusy}
              onClick={onSwitchStyle}>换风格</button>
          )}
        </div>
      )}
      {/* 参考套图的结构覆盖差（哪几张对得上本平台角色、哪几张没有对应角色） */}
      {(styleRefs?.coverage || []).length > 0 && (
        <div className="text-xs text-muted mb-1" data-testid="session-style-coverage">
          {styleRefs.coverage.map(item => (
            `参考套图「${item.name}」：${item.ref_count} 个角色，对得上 ${item.matched.length} 张` +
            (item.missing_in_ref.length
              ? `；本平台还有 ${item.missing_in_ref.length} 张没有对应角色（按槽位契约写）` : '')
          )).join('｜')}
        </div>
      )}
      {Object.keys(scores).length > 0 && (
        <div className="text-sm mb-1">
          逐张审美分：
          {Object.entries(scores).map(([slot, score]) => (
            <span key={slot} className="badge badge-info ml-1">{slot} {score}</span>
          ))}
        </div>
      )}
      {revised.length > 0 && (
        <div className="text-sm mb-1">
          🎨 已按审美改写：<span className="strong">{revised.join('、')}</span>
        </div>
      )}
      {rejected.length > 0 && (
        <div className="text-xs text-err mb-1">
          以下改写被体检拦下（保留原稿）：{rejected.map((item) => item.slot_id).join('、')}
        </div>
      )}
      {findings.length > 0 && (
        <details>
          <summary className="text-sm" style={{ cursor: 'pointer' }}>
            查看 {findings.length} 条体检结论
          </summary>
          <div className="text-xs">
            {findings.map((item, index) => (
              <div key={index}>
                {item.level === 'error' ? '❌' : '⚠️'}
                {item.number ? ` 第${item.number}张` : ''} {item.message}
              </div>
            ))}
          </div>
        </details>
      )}
      {review?.message && <p className="text-xs mt-1">{review.message}</p>}
      {!lint && review && <p className="text-xs">提示词审核：{review.status || '未执行'}</p>}
      {!lint && !review && styleRefs && (
        <p className="text-xs">本次未产出提示词体检/审美审核结论，仅回放风格档案采用情况。</p>
      )}
    </div>
  )
}

// 商品身份卡（用户反馈："分析员根本没识别到品牌，商品名也没强调或提醒"）
// —— 全链路的事实基准，放在产出物最前面，未确认时醒目告警
function IdentityCard({ identity }) {
  const confirmed = identity.status === 'confirmed'
  return (
    <div className={`card ${confirmed ? '' : 'card-warn'}`}>
      <h3 className="mb-1">
        {confirmed ? '🏷️ 商品身份' : '⚠️ 商品身份未确认'}
        <span className={`badge ml-1 ${confirmed ? 'badge-ok' : 'badge-err'}`}>
          {confirmed ? '已确认' : '未确认'}
        </span>
        {identity.source === 'mock' && <span className="badge badge-warn ml-1">演示数据</span>}
      </h3>
      {confirmed ? (
        <p className="text-sm mb-1">
          品牌 <span className="strong">{identity.brand}</span>
          {' ｜ '}品名 <span className="strong">{identity.product_name}</span>
          {identity.spec && <span>{' ｜ '}规格 {identity.spec}</span>}
          {identity.certifications?.length > 0 && (
            <span>{' ｜ '}认证 {identity.certifications.join('、')}</span>
          )}
        </p>
      ) : (
        <p className="text-sm mb-1">
          未能确认 {(identity.missing || ['品牌', '商品名']).join('、')}
          —— 画面中不会生成任何品牌/包装文字（文字区域做干净虚化，交设计师后期贴图）。
        </p>
      )}
      {identity.evidence && <p className="text-xs mb-1">识别依据：{identity.evidence}</p>}
      {identity.visible_text?.lines?.length > 0 && (
        <p className="text-xs mb-1">
          包装可见文字：{identity.visible_text.lines.map(l => l.text).join(' ／ ')}
        </p>
      )}
    </div>
  )
}

// 套图编排与完成度（用户要求："要一套可直接上传的套图，而不是一张图的多个候选"）
function SetPlanCard({ plan, coverage }) {
  const slots = plan.slots || []
  return (
    <div className="card">
      <h3 className="mb-1">
        🧩 套图编排
        <span className="badge badge-info ml-1">{plan.platform_label || plan.platform || '平台'}</span>
        <span className="badge badge-info ml-1">{slots.length} 张</span>
        {coverage && (
          <span className={`badge ml-1 ${coverage.complete ? 'badge-ok' : 'badge-warn'}`}>
            {coverage.complete ? '已成套' : `缺 ${coverage.produced}/${coverage.expected}`}
          </span>
        )}
      </h3>
      <div className="flex flex-wrap gap-1">
        {slots.map(slot => {
          const blocked = (coverage?.blocked_slots || []).includes(slot.slot_id)
          const done = !blocked && (!coverage || (coverage.done_slots || []).includes(slot.slot_id))
          return (
            <span key={slot.slot_id}
                  className={`badge ${done ? 'badge-ok' : blocked ? 'badge-err' : 'badge-warn'}`}>
              {done ? '✅' : blocked ? '⛔' : '⬜'} {slot.slot_id}（{slot.role}）
              {slot.kind === 'info' ? '·图文' : ''}
            </span>
          )
        })}
      </div>
      {(coverage?.blocked_slots || []).length > 0 && (
        <p className="text-xs mt-1 text-err">
          缺素材、已拦下未编造：{(coverage.blocked_slots || []).join('、')}
          —— 例如成分图需要包装背面/成分表照片；补齐素材后再出这几张
        </p>
      )}
      {coverage && !coverage.complete && (
        <p className="text-xs mt-1">
          缺槽位：{(coverage.missing_slots || []).join('、')} —— 补齐后才是一套可直接上传的图
        </p>
      )}
      {plan.notes?.length > 0 && <p className="text-xs mt-1">{plan.notes.join('；')}</p>}
    </div>
  )
}

// 本地体检（客观数值：背景白度 / 水印区 / 商品身份相似度）
function QualityCard({ report }) {
  const ok = report.white_bg_ok && report.watermark_free &&
    !(report.identity_lost || []).length && !(report.near_copy || []).length
  return (
    <div className="card">
      <h3 className="mb-1">
        🔬 本地体检
        <span className={`badge ml-1 ${ok ? 'badge-ok' : 'badge-warn'}`}>
          {ok ? '通过' : `${(report.issues || []).length} 个问题`}
        </span>
      </h3>
      <p className="text-sm mb-1">
        背景纯白：<span className={report.white_bg_ok ? 'strong' : 'text-err'}>
          {report.white_bg_ok ? '是' : '否'}</span>
        {' ｜ '}无水印：<span className={report.watermark_free ? 'strong' : 'text-err'}>
          {report.watermark_free ? '是' : '否'}</span>
      </p>
      {(report.identity_lost || []).length > 0 && (
        <p className="text-sm text-err mb-1">
          商品身份相似度过低：{report.identity_lost.join('、')}（疑似模型在凭文字想象商品）
        </p>
      )}
      {(report.near_copy || []).length > 0 && (
        <p className="text-sm text-err mb-1">
          疑似直接复制原图：{report.near_copy.join('、')}（没有按提示词重绘）
        </p>
      )}
      {(report.issues || []).map((issue, i) => (
        <p key={i} className="text-xs mb-1">· {issue}</p>
      ))}
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
      <DetailsJson label="分析详情" data={analysis} />
    </div>
  )
}

function PromptsCard({ prompts }) {
  const main = prompts.main_image || {}
  const scenes = prompts.scene_images || []
  const socials = prompts.social_images || []
  // 逐张提示词（"要能核对第几张是怎么写的"）——**人话列表**，不是 JSON
  const plan = prompts.prompt_plan || prompts.set_plan?.slots || []
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
      {plan.length > 0 ? (
        <>
          <p className="text-sm">本套逐张提示词（{plan.length} 张）</p>
          <PromptPlanList plan={plan} />
        </>
      ) : (
        <p className="text-sm">场景图 {scenes.length} 张 · 社交图 {socials.length} 张</p>
      )}
      <div className="flex flex-wrap gap-1 mt-1">
        {scenes.map((s, i) => <span key={i} className="badge badge-info">{s.scene_type || `场景${i + 1}`}</span>)}
        {socials.map((s, i) => <span key={i} className="badge badge-info">{s.scene_type || `社交${i + 1}`}</span>)}
      </div>
      <DetailsJson label="其余提示词内容" data={prompts} />
    </div>
  )
}

/** 逐张提示词的**可读列表**（第N张｜角色｜想让人看懂什么｜画面描述折叠） */
function PromptPlanList({ plan }) {
  return (
    <div className="mt-1">
      {plan.map((slot, i) => (
        <div key={slot.slot_id || i} className="mb-1">
          <div className="text-xs strong">
            第{slot.number ?? i + 1}张｜{slot.role || slot.slot_id || '未命名'}
            {slot.slot_id ? `（${slot.slot_id}）` : ''}
            {slot.kind === 'info' ? '｜信息图·文字本地排版' : slot.kind === 'photo' ? '｜纯摄影' : ''}
            {slot.revised_by_reviewer && <span className="badge badge-warn ml-1">已按审美改写</span>}
          </div>
          {slot.intent && <div className="text-xs">想要：{slot.intent}</div>}
          {slot.prompt && (
            <details>
              <summary className="text-xs" style={{ cursor: 'pointer' }}>查看画面描述</summary>
              <div className="text-xs" style={{ whiteSpace: 'pre-wrap' }}>{slot.prompt}</div>
            </details>
          )}
        </div>
      ))}
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

/**
 * 详情字段：**给人看**，不是把 JSON 摊开。
 *
 * 用户反馈（2026-09-20）："会话里 agent 的对话里面显示的会很直白，会给出代码原文，
 * 实际上我们只要文字的内容显示。" —— 此前这里是 `JSON.stringify(data, null, 2)`，
 * 会带出 `visible_text`（整份包装文字提取）、`brand_palette.evidence`、`saved_path`
 * 这类内部产物。现在：内部字段不显示、签名地址与图片数据脱敏、键名映射成中文标签。
 */
const HIDDEN_DETAIL_KEYS = new Set([
  ...INTERNAL_KEYS, 'visible_text', 'brand_palette', 'evidence', 'source',
  'derived_from', 'missing', 'sample_url', 'thumb', 'file', 'bytes',
])

function detailLabel(key) {
  return fieldLabel(key) || key
}

function DetailValue({ name, value }) {
  if (value == null || value === '') return null
  if (Array.isArray(value)) {
    if (value.length === 0) return null
    const simple = value.every(v => typeof v !== 'object' || v === null)
    return (
      <div className="text-xs mb-1">
        <span className="text-muted">{name}：</span>
        {simple
          ? value.map(String).join('、')
          : value.map((item, i) => <DetailFields key={i} data={item} />)}
      </div>
    )
  }
  if (typeof value === 'object') return <DetailFields data={value} name={name} />
  return (
    <div className="text-xs mb-1">
      <span className="text-muted">{name}：</span>
      <span style={{ whiteSpace: 'pre-wrap' }}>{String(value)}</span>
    </div>
  )
}

function DetailFields({ data, name }) {
  if (!data || typeof data !== 'object') return null
  const rows = Object.entries(data).filter(([k, v]) => !HIDDEN_DETAIL_KEYS.has(k) && v != null && v !== '')
  if (rows.length === 0) return null
  return (
    <div className="mt-1">
      {name && <div className="text-xs strong">{name}</div>}
      {rows.map(([k, v]) => <DetailValue key={k} name={detailLabel(k)} value={v} />)}
    </div>
  )
}

function DetailsJson({ label, data }) {
  const [open, setOpen] = useState(false)
  const shown = rawContentText(data)
  const masked = hiddenKeys(data)
  return (
    <div className="mt-1">
      <button className="expand-btn" onClick={() => setOpen(!open)} style={{ background: 'none', border: 'none', color: '#60a5fa', cursor: 'pointer', fontSize: 12 }}>
        {open ? '▲ 收起' : '▼ 查看'} {label}
      </button>
      {open && (
        <>
          <div style={{ background: '#0f172a', padding: 10, borderRadius: 6, marginTop: 4 }}>
            <DetailFields data={displayContent(data, { promptCharLimit: 0 })} />
          </div>
          <details className="mt-1">
            <summary className="text-xs" style={{ cursor: 'pointer' }}>技术详情（原始字段）</summary>
            {masked.length > 0 && (
              <p className="text-xs text-muted">
                已隐藏：{masked.join('、')}（仍可在此查看，请勿外传截图）
              </p>
            )}
            <pre className="text-xs mt-1" style={{ background: '#0f172a', padding: 10, borderRadius: 6, overflowX: 'auto', color: '#94a3b8' }}>
              {shown}
            </pre>
          </details>
        </>
      )}
    </div>
  )
}

// ── 图片 ──

function isPlaceholder(img) {
  return (img.model_used || '').startsWith('mock') || (img.image_url || '').startsWith('data:image/svg+xml')
}

/** 槽位 → 需要补什么素材（补充事实表单用） */
const FACT_FIELD = {
  main_ingredients: { key: 'ingredients', label: '成分（每行一条）' },
  main_usage: { key: 'usage', label: '食用方法/用法用量（每行一条）' },
  main_spec: { key: 'spec', label: '规格/净含量' },
  main_cert: { key: 'certifications', label: '认证（每行一条）' },
  main_compare: { key: 'compare', label: '对比内容（每行一条）' },
  main_benefits: { key: 'features', label: '卖点/特征（每行一条）' },
  main_selling_point: { key: 'selling_points', label: '卖点（每行一条）' },
  main_audience: { key: 'target_audience', label: '适用人群' },
}

function ImagesTab({ images, sessionId }) {
  const [busy, setBusy] = useState('')
  const [msg, setMsg] = useState('')
  const [factDraft, setFactDraft] = useState({})
  const [factMsg, setFactMsg] = useState('')
  const [factErr, setFactErr] = useState('')
  if (!images || images.length === 0) {
    return <div className="empty-state"><p className="text-sm">暂无生成图片（生图员产出后显示）</p></div>
  }
  const allPlaceholders = images.every(isPlaceholder)
  const savedCount = images.filter(img => img.saved_path).length
  const hasData = img => Boolean(img.image_url || img.base64_data)
  const missingCount = images.filter(img => !hasData(img) && !isPlaceholder(img)).length
  const blocked = images.filter(img => img.text_status === 'blocked')

  /** 把"缺素材"的槽位补上事实 → 通知协调者重新出图（复用插话通道） */
  async function submitFacts() {
    setFactErr('')
    setFactMsg('')
    const facts = {}
    for (const [key, raw] of Object.entries(factDraft)) {
      const items = String(raw || '').split('\n').map(line => line.trim()).filter(Boolean)
      if (items.length) facts[key] = items
    }
    if (!Object.keys(facts).length) {
      setFactErr('请至少填写一项')
      return
    }
    setBusy('facts')
    try {
      await saveSessionFacts(sessionId, facts)
      const labels = Object.keys(facts).join('、')
      await interjectSession(sessionId, `已补充事实（${labels}），请重新邀请生图员补齐之前缺素材的信息图`)
      setFactMsg('✅ 已补充并通知协调者重新出图（这几张会带上你填的文字）')
      setFactDraft({})
    } catch (err) {
      setFactErr(err.message || '补充失败')
    }
    setBusy('')
  }

  function saveBlob(blob, filename) {
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = filename
    a.click()
    URL.revokeObjectURL(url)
  }

  async function downloadOne(index) {
    setBusy(`one-${index}`)
    setMsg('')
    try {
      const blob = await downloadSessionImage(sessionId, index)
      const img = images[index - 1] || {}
      saveBlob(blob, (img.saved_path || '').split('/').pop() || `image_${index}.png`)
    } catch (err) {
      setMsg('下载失败: ' + (err.message || '未知错误'))
    }
    setBusy('')
  }

  async function exportAll() {
    setBusy('zip')
    setMsg('')
    try {
      saveBlob(await exportSessionImages(sessionId), `${sessionId.slice(0, 12)}_images.zip`)
      setMsg('✅ 已导出 ZIP')
    } catch (err) {
      setMsg('导出失败: ' + (err.message || '未知错误'))
    }
    setBusy('')
  }

  return (
    <div>
      {allPlaceholders && (
        <div className="alert alert-warn">
          ⚠ 当前展示的是<b>占位图</b>：未配置生图模型（DeepSeek 只能看图/写字，不能生成图片）。
          在设置页配置 DALL-E / 即梦 Seedream / FLUX 任一 Key 后，生图员将生成真实商品图。
        </div>
      )}
      {missingCount > 0 && (
        <div className="alert alert-error">
          ⚠ 有 {missingCount} 张<b>没有图像数据</b>（生图失败或未落盘）——
          请到「群聊」看生图员的报错，或到「审计」看上游原文。失败的图不会再被当作成功产出。
        </div>
      )}
      {/* 信息图缺素材 → 就地补充事实（不编造：填了才会画在那几张图上） */}
      {blocked.length > 0 && (
        <div className="card mb-1">
          <h3 className="mb-1">
            ✍️ 补充素材
            <span className="badge badge-err ml-1">{blocked.length} 张缺素材</span>
          </h3>
          <p className="text-sm mb-2">
            这些信息图**缺事实依据**、已拦下未生成（系统不会替商品编造成分/功效）。
            你填的内容会作为图上文字，然后通知协调者重新出图：
          </p>
          <div className="flex flex-col gap-2">
            {blocked.map(img => {
              const field = FACT_FIELD[img.slot_id] || { key: img.slot_id, label: '内容（每行一条）' }
              return (
                <div key={img.slot_id} className="form-group">
                  <label className="label" htmlFor={`fact-${img.slot_id}`}>
                    {img.slot_id} —— {field.label}
                  </label>
                  <textarea id={`fact-${img.slot_id}`} className="input" rows={2}
                            aria-label={`补充 ${field.key}`}
                            placeholder={img.text_reason || '每行一条，例如：每日 2 粒，飯後服用'}
                            value={factDraft[field.key] || ''}
                            onChange={e => setFactDraft(p => ({ ...p, [field.key]: e.target.value }))} />
                </div>
              )
            })}
          </div>
          <div className="flex items-center gap-2" style={{ flexWrap: 'wrap' }}>
            <button type="button" className="btn btn-primary btn-sm" aria-label="保存素材并重新出图"
                    disabled={busy === 'facts'} onClick={submitFacts}>
              {busy === 'facts' ? '提交中...' : '保存并重新出图'}
            </button>
            <span className="text-xs text-muted">提交后会写入本会话，并请协调者重新邀请生图员</span>
          </div>
          {factErr && <div className="key-error" role="alert">{factErr}</div>}
          {factMsg && <div className="key-status ok" role="status">{factMsg}</div>}
        </div>
      )}
      <div className="flex items-center gap-2 mb-1" style={{ flexWrap: 'wrap' }}>
        <button type="button" className="btn btn-ghost btn-sm" disabled={busy === 'zip'}
                onClick={exportAll}>
          {busy === 'zip' ? '打包中...' : '⬇️ 导出全部（ZIP）'}
        </button>
        <span className="text-xs">
          生成时已自动落盘{savedCount ? `（${savedCount} 张）` : ''}；
          输出目录可在<b>设置页 → 输出目录</b>修改
        </span>
        {msg && <span className="text-sm" role="status">{msg}</span>}
      </div>
      <div className="image-grid">
        {images.map((img, i) => {
          const src = img.image_url || (img.base64_data ? `data:image/png;base64,${img.base64_data}` : '')
          const placeholder = isPlaceholder(img)
          const usable = hasData(img) || placeholder
          return (
            <div key={i} className={`image-cell ${placeholder ? 'image-cell-placeholder' : ''}`}>
              {src ? <img src={src} alt={img.prompt_name || `生成图 ${i + 1}`} loading="lazy" /> : (
                <div className="flex items-center justify-center" style={{ height: 160 }}>
                  <span className="text-xs">无图片数据（生图失败）</span>
                </div>
              )}
              <div className="img-caption">
                {img.prompt_name && <div className="strong" style={{ fontSize: 12 }}>{img.prompt_name}</div>}
                {img.slot_id && (
                  <div>
                    {img.prompt_number > 0 && (
                      <span className="badge badge-info">第{img.prompt_number}张</span>
                    )}
                    <span className="badge badge-info">槽位 {img.slot_id}</span>
                    {img.revised_by_reviewer && (
                      <span className="badge badge-warn">已按审美改写</span>
                    )}
                  </div>
                )}
                {/* 逐张提示词：用户要能核对"第几张是怎么写的"（改前提示词正文根本不展示） */}
                {img.prompt_text && (
                  <details>
                    <summary className="text-xs" style={{ cursor: 'pointer' }}>查看提示词</summary>
                    <div className="text-xs" style={{ whiteSpace: 'pre-wrap' }}>{img.prompt_text}</div>
                    {img.prompt_sections?.must?.length > 0 && (
                      <div className="text-xs">必须：{img.prompt_sections.must.join('；')}</div>
                    )}
                    {img.prompt_sections?.keep_clear && (
                      <div className="text-xs">留白区：{img.prompt_sections.keep_clear}</div>
                    )}
                    {img.prompt_notes?.length > 0 && (
                      <div className="text-xs text-err">
                        {img.prompt_notes.map((note, k) => <div key={k}>· {note}</div>)}
                      </div>
                    )}
                  </details>
                )}
                {/* 信息图：文字由本地排版绘制；缺事实依据的槽位在这里说明要补什么素材 */}
                {img.text_status === 'composed' && (
                  <div>
                    <span className="badge badge-ok">图文已排版</span>
                    {img.compose?.layout && <span className="text-xs"> {img.compose.layout}</span>}
                  </div>
                )}
                {img.text_status === 'blocked' && (
                  <div>
                    <span className="badge badge-err">缺素材·未生成</span>
                    <div className="text-xs text-err">{img.text_reason}</div>
                  </div>
                )}
                {/* 图上实际画了什么字 —— 便于人工核对（文案只来自已确认事实） */}
                {img.slot_copy?.items?.length > 0 && (
                  <div className="text-xs" style={{ color: '#94a3b8' }}>
                    图内文字：{img.slot_copy.title}
                    {img.slot_copy.items.map((item, k) => <div key={k}>· {item}</div>)}
                    {img.slot_copy.footer && <div>{img.slot_copy.footer}</div>}
                  </div>
                )}
                {placeholder
                  ? <div><span className="badge badge-warn">占位图</span></div>
                  : (!usable && <div><span className="badge badge-err">生成失败</span></div>)}
                {usable && img.model_used && !placeholder && <div>模型: {img.model_used}</div>}
                {img.processing_status && <div>状态: {img.processing_status}</div>}
                {/* 本地体检（客观数值）：背景白度 / 水印 / 与参考图的身份相似度 */}
                {img.quality?.available && (
                  <div className="flex flex-wrap gap-1">
                    <span className={`badge ${img.quality.is_white_bg ? 'badge-ok' : 'badge-warn'}`}>
                      白底 {img.quality.is_white_bg ? '✓' : `✗ ${Math.round(img.quality.edge_mean)}`}
                    </span>
                    <span className={`badge ${img.quality.watermark_suspected ? 'badge-warn' : 'badge-ok'}`}>
                      {img.quality.watermark_suspected ? '疑似水印' : '无水印'}
                    </span>
                    {img.quality.reference_compare?.available && (
                      <span className={`badge ${img.quality.reference_compare.identity_lost ? 'badge-err' : 'badge-ok'}`}>
                        身份 {img.quality.reference_compare.identity_similarity}
                      </span>
                    )}
                  </div>
                )}
                {img.generation_params?.reference_count > 0 && (
                  <div className="text-xs">
                    文+图：参考图 × {img.generation_params.reference_count}
                    {img.generation_params.text_strategy && `｜文字 ${img.generation_params.text_strategy}`}
                  </div>
                )}
                {img.saved_path && (
                  <div className="text-xs mono" title={img.saved_path} style={{ wordBreak: 'break-all' }}>
                    {img.saved_path}
                  </div>
                )}
                <button type="button" className="btn btn-ghost btn-sm"
                        disabled={busy === `one-${i + 1}` || !usable}
                        title={usable ? '' : '这张没有图像数据，无法下载'}
                        onClick={() => downloadOne(i + 1)}>
                  {busy === `one-${i + 1}` ? '下载中...' : '下载'}
                </button>
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
  // 未评分必须如实展示（实测：overall_score=null 曾被拼成"None/100"）
  const hasScore = typeof review.overall_score === 'number' && Number.isFinite(review.overall_score)
  const verdictClass = review.verdict === 'pass' ? 'badge-ok'
    : review.verdict === 'fail' ? 'badge-err' : 'badge-warn'
  return (
    <div className="flex flex-col gap-2">
      <div className="flex items-center gap-2" style={{ flexWrap: 'wrap' }}>
        <span className={`badge ${verdictClass}`}>
          {review.verdict ? `判定 ${review.verdict}` : '判定缺失'}
        </span>
        <span className="text-sm">{hasScore ? `评分 ${review.overall_score}/100` : '未给出分数'}</span>
        {review.needs_human_review && <span className="badge badge-warn">需人工复核</span>}
        {review.review_blocked_reason && (
          <span className="text-xs text-muted">阻断原因: {review.review_blocked_reason}</span>
        )}
      </div>
      {review.error && <div className="alert alert-error text-sm">审查未完成：{review.error}</div>}
      {typeof review.text === 'string' && review.text.trim() && (
        <div className="card">
          <h3 className="mb-1">审查原文</h3>
          <pre className="text-xs" style={{ whiteSpace: 'pre-wrap', margin: 0 }}>{review.text}</pre>
        </div>
      )}
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
                        <div style={{ width: `${Math.min(typeof v === 'number' ? v : 0, 100)}%`, height: '100%', background: v >= 75 ? '#22c55e' : v >= 60 ? '#f59e0b' : '#ef4444' }} />
                      </div>
                    </td>
                    <td className="text-sm" style={{ width: 60, textAlign: 'right' }}>
                      {typeof v === 'number' ? v : '—'}
                    </td>
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
