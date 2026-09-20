import { useEffect, useRef, useState, useMemo } from 'react'
import { useWebSocket } from '../useWebSocket'
import { msgPreview, displayContent, detailFields, hiddenKeys, rawContentText } from '../chatFormat'

export { msgPreview } from '../chatFormat'

const SENDER_ICONS = {
  '中心决策者': '🎯',
  '商品分析员': '🔍',
  '品类专项分析员': '📊',
  '提示词生成员': '✍️',
  '生图员': '🖼️',
  '图像后处理员': '🔧',
  '审查员': '⭐',
  '合规审查员': '🛡️',
  '系统': '📡',
  '人工审查': '👤',
  '记忆库': '🧠',
  'A/B 测试': '🔬',
}

export function senderIcon(name) {
  return SENDER_ICONS[name] || '🤖'
}

// `msgPreview` 已迁到 `../chatFormat`（含"绝不回落 JSON"的新兜底），
// 这里保留 re-export：既有调用方（页面/测试）继续 `from './ChatPanel'` 拿得到。

/** 逐张提示词的结构化渲染（第N张｜角色｜想要/必须/禁止）—— 不再丢一坨 JSON 给用户 */
export function PromptPlanView({ plan }) {
  if (!Array.isArray(plan) || plan.length === 0) return null
  return (
    <div className="prompt-plan" data-testid="prompt-plan">
      {plan.map((slot) => (
        <div key={slot.slot_id || slot.number} className="prompt-plan-item">
          <div className="strong" style={{ fontSize: 12 }}>
            第{slot.number}张｜{slot.role || slot.slot_id}（{slot.slot_id}）
            {slot.usage === 'detail' ? '｜详情图' : '｜主图'}
            {slot.kind === 'info' ? '｜信息图·文字本地排版' : '｜纯摄影'}
            {slot.aesthetic_score != null && (
              <span className="badge badge-info" style={{ marginLeft: 6 }}>
                审美 {slot.aesthetic_score}
              </span>
            )}
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

/** 提示词体检 / 审美审核结论（群聊里直接可读） */
export function PromptReviewView({ lint, review }) {
  const findings = lint?.findings || []
  if (!lint && !review) return null
  return (
    <div data-testid="prompt-review">
      {lint && (
        <div>
          <div className="strong" style={{ fontSize: 12 }}>
            🧪 提示词体检：{lint.errors?.length ? `❌ ${lint.errors.length} 项硬伤` : '✅ 通过'}
            {lint.warnings?.length ? `，${lint.warnings.length} 项建议` : ''}
          </div>
          {findings.slice(0, 8).map((item, i) => (
            <div key={i} className="text-xs">
              {item.level === 'error' ? '❌' : '⚠️'} {item.number ? `第${item.number}张 ` : ''}
              {item.message}
            </div>
          ))}
        </div>
      )}
      {review && (
        <div>
          <div className="strong" style={{ fontSize: 12 }}>
            🎨 提示词审美审核：{review.verdict}
            {review.threshold != null ? `（阈值 ${review.threshold}）` : ''}
          </div>
          {review.scores && Object.keys(review.scores).length > 0 && (
            <div className="text-xs">
              逐张审美分：{Object.entries(review.scores).map(([k, v]) => `${k} ${v}`).join('｜')}
            </div>
          )}
          {review.revised_slots?.length > 0 && (
            <div className="text-xs">已改写：{review.revised_slots.join('、')}</div>
          )}
          {review.refine_rejected?.length > 0 && (
            <div className="text-xs text-err">
              被体检拦下的改写：{review.refine_rejected.map((r) => r.slot_id).join('、')}
            </div>
          )}
        </div>
      )}
    </div>
  )
}

const FILTERS = [
  { key: 'all', label: '全部' },
  { key: 'coordinator', label: '决策者' },
  { key: 'agent', label: 'Agent' },
  { key: 'system', label: '系统' },
]

export default function ChatPanel({ sessionId, onNewMessage, status }) {
  const { messages, connected, error, send } = useWebSocket(sessionId)
  const bottomRef = useRef(null)
  const [filter, setFilter] = useState('all')
  const [input, setInput] = useState('')
  const [sending, setSending] = useState(false)

  // 会话处于运行态且已有消息 → 显示「正在输入」（Agent 正在思考/生成）
  const typing = status === 'running' && messages.length > 0

  // M3 群聊插话：WS 发送指令（失败回退 REST）
  async function handleInterject() {
    const content = input.trim()
    if (!content || sending) return
    setSending(true)
    try {
      const ok = send({ type: 'chat', content })
      if (!ok) {
        const { interjectSession } = await import('../api')
        await interjectSession(sessionId, content)
      }
      setInput('')
    } catch (err) {
      alert('插话失败: ' + (err.message || '未知错误'))
    }
    setSending(false)
  }

  useEffect(() => {
    if (messages.length > 0 && onNewMessage) onNewMessage()
  }, [messages.length])

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, filter])

  const grouped = useMemo(() => {
    const shown = filter === 'all' ? messages : messages.filter(m => m.role === filter)
    const turns = []
    for (const m of shown) {
      const last = turns[turns.length - 1]
      if (last && last.turn === m.turn) last.items.push(m)
      else turns.push({ turn: m.turn, items: [m] })
    }
    return turns
  }, [messages, filter])

  if (error) {
    return (
      <div className="card card-error">
        <h3>群聊连接失败 🔴</h3>
        <p className="text-sm">{error}</p>
        <p className="text-xs mt-1">请确认后端服务正在运行 (http://localhost:8000)</p>
      </div>
    )
  }

  if (!connected && messages.length === 0) {
    return (
      <div className="card">
        <h3 className="mb-1">群聊直播</h3>
        <div className="flex items-center justify-center" style={{ minHeight: 120 }}>
          <div className="text-center">
            <div className="spinner" />
            <p className="text-sm mt-2">正在连接群聊...</p>
          </div>
        </div>
      </div>
    )
  }

  return (
    <div className="card">
      <div className="flex items-center justify-between mb-1" style={{ flexWrap: 'wrap', gap: 8 }}>
        <h3 style={{ marginBottom: 0 }}>群聊直播 {connected ? '🟢' : '🟡'}</h3>
        <div className="flex gap-1">
          {FILTERS.map(f => (
            <button
              key={f.key}
              className={`btn btn-sm ${filter === f.key ? 'btn-primary' : 'btn-ghost'}`}
              onClick={() => setFilter(f.key)}
            >
              {f.label}
            </button>
          ))}
        </div>
      </div>

      <div className="chat-box">
        {grouped.length === 0 && (
          <div className="empty-state">
            <p className="text-lg mb-1">🤖</p>
            <p className="text-sm">等待群聊开始...</p>
            <p className="text-xs mt-1">上传商品图片后，中心决策者将自动协调 Agent 群聊</p>
          </div>
        )}
        {grouped.map(g => (
          <div key={`t${g.turn}-${g.items[0]?.id}`}>
            <div className="turn-divider">— 第 {g.turn} 轮 —</div>
            {g.items.map(msg => <ChatMessage key={msg.id} msg={msg} />)}
          </div>
        ))}
        {typing && <TypingIndicator />}
        <div ref={bottomRef} />
      </div>

      {/* M3 群聊插话：指挥正在运行的群聊 */}
      <div className="chat-composer">
        <input
          className="chat-input"
          placeholder="插话指挥群聊，如：换个更简约的风格 / 加上送礼场景"
          value={input}
          onChange={e => setInput(e.target.value)}
          onKeyDown={e => { if (e.key === 'Enter') handleInterject() }}
        />
        <button
          className="chat-send"
          onClick={handleInterject}
          disabled={sending || !input.trim()}
          title="发送插话"
          aria-label="发送插话"
        >
          📨
        </button>
      </div>
      <p className="text-xs mt-1 text-muted">插话会进入群聊消息流，Coordinator 下一轮决策将参考你的指令（真实 LLM 模式生效）。</p>
    </div>
  )
}

function TypingIndicator() {
  return (
    <div className="chat-row typing-row">
      <span className="chat-avatar" aria-hidden>⚡</span>
      <div className="chat-bubble typing-bubble" role="status" aria-label="Agent 正在输入">
        <span className="typing-dots"><i /><i /><i /></span>
        <span className="typing-text">正在输入…</span>
      </div>
    </div>
  )
}

function ChatMessage({ msg }) {
  const [expanded, setExpanded] = useState(false)
  const content = msg.content || {}
  const preview = msgPreview(content)
  const shown = displayContent(content)
  const masked = hiddenKeys(content)
  const full = rawContentText(content)
  const text = typeof shown.message === 'string' ? shown.message : ''
  // 展开后正文只给"人话"：文字内容 → 结构化卡片 → 人话字段；
  // 原始字段（含签名 URL / 图片数据）降到默认折叠的「技术详情」——
  // 需要排障时仍拿得到，但不再"展开就糊一脸"
  const detail = { ...detailFields(content) }
  delete detail.message
  delete detail.base64_data
  delete detail.image_url

  return (
    <div className={`chat-row ${msg.role}`}>
      <span className="chat-avatar" aria-hidden>{senderIcon(msg.sender)}</span>
      <div className="chat-bubble">
        <div className="chat-bubble-head">
          <span className="sender">{msg.sender}</span>
          {msg.action === 'invite' && <span className="badge badge-info">邀请</span>}
          {msg.action === 'done' && <span className="badge badge-ok">完成</span>}
          {msg.action === 'error' && <span className="badge badge-err">错误</span>}
          <span className="turn-tag">{msg.timestamp ? new Date(msg.timestamp).toLocaleTimeString() : ''}</span>
        </div>
        <div className={`pre ${expanded ? '' : 'dim'}`}>
          {expanded ? (
            <>
              {/* 正文先给人话：`message` 优先，其次与折叠态一致的预览句
                  （展开后不能反而"更不像人话"——用户反馈的原话） */}
              {(text || preview) && <div className="strong">{text || preview}</div>}
              {content.prompt_plan?.length > 0 && <PromptPlanView plan={content.prompt_plan} />}
              {(content.prompt_lint || content.prompt_review) && (
                <PromptReviewView lint={content.prompt_lint} review={content.prompt_review} />
              )}
              {Object.keys(detail).length > 0 && (
                <details>
                  <summary className="text-xs" style={{ cursor: 'pointer' }}>查看详情字段</summary>
                  <pre className="text-xs" style={{ whiteSpace: 'pre-wrap' }}>{rawContentText(detail)}</pre>
                </details>
              )}
              <details>
                <summary className="text-xs" style={{ cursor: 'pointer' }}>技术详情（原始字段）</summary>
                {masked.length > 0 && (
                  <p className="text-xs text-muted">
                    已隐藏：{masked.join('、')}（仍可在此查看，请勿外传截图）
                  </p>
                )}
                <pre className="text-xs" style={{ whiteSpace: 'pre-wrap' }}>{full}</pre>
              </details>
            </>
          ) : preview}
        </div>
        <button className="expand-btn" onClick={() => setExpanded(!expanded)}>
          {expanded ? '▲ 收起' : '▼ 展开详情（文字内容与结构化卡片）'}
        </button>
      </div>
    </div>
  )
}
