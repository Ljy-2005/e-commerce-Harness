import { useEffect, useRef, useState, useMemo } from 'react'
import { useWebSocket } from '../useWebSocket'

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

export function msgPreview(content) {
  if (!content) return ''
  if (content.error) return `❌ ${content.error}`
  if (content.category) return `品类: ${content.category}`
  if (content.overall_score) return `评分: ${content.overall_score}/100 | ${content.verdict}`
  if (content.passed !== undefined) return `合规: ${content.passed ? '通过' : '不通过'}`
  if (content.decision) return `决策: ${content.decision}`
  if (content.feedback) return `反馈: ${content.feedback?.slice(0, 80)}`
  if (content.hitl) return '⏸ 需要人工审查'
  if (content.context_action) return `上下文: ${content.context_action}`
  if (content.memory_recall) return `🧠 ${content.memory_recall.slice(0, 100)}`
  if (content.winner) return `🏆 最优: ${content.winner} (${content.winner_score}/100)`
  if (content.interjection) return `📣 用户插话: ${content.interjection}`
  if (content.agent_name) return `邀请: ${content.agent_name} — ${content.task_brief?.slice(0, 60) || ''}`
  if (content.action === 'done') return '✅ 任务完成'
  // 生图员输出为 Mock 占位图时明确提示（未配置生图模型）
  if (content.images && Array.isArray(content.images)
    && content.images.every(i => (i.model_used || '').startsWith('mock') || (i.image_url || '').startsWith('data:image/svg+xml'))) {
    return '⚠ 输出为占位图：未配置生图模型（DeepSeek 不支持生图，需 DALL-E / 即梦 / FLUX Key）'
  }
  return JSON.stringify(content).slice(0, 120)
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
  const full = JSON.stringify(content, null, 2)

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
          {expanded ? full : preview}
        </div>
        <button className="expand-btn" onClick={() => setExpanded(!expanded)}>
          {expanded ? '▲ 收起' : '▼ 展开完整内容'}
        </button>
      </div>
    </div>
  )
}
