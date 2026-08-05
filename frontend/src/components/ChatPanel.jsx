import { useEffect, useRef, useState } from 'react'
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

function senderIcon(name) {
  return SENDER_ICONS[name] || '🤖'
}

function msgPreview(content) {
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
  return JSON.stringify(content).slice(0, 120)
}

export default function ChatPanel({ sessionId, onNewMessage }) {
  const { messages, connected, error } = useWebSocket(sessionId)
  const bottomRef = useRef(null)
  const [showWs, setShowWs] = useState(false)

  useEffect(() => {
    if (messages.length > 0 && onNewMessage) onNewMessage()
  }, [messages.length])

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  const displayMessages = showWs ? messages : messages.filter(m => m.role !== 'system' || m.action !== 'respond')

  // ── WebSocket 连接错误 ──
  if (error) {
    return (
      <div className="card card-error">
        <h3>群聊连接失败 🔴</h3>
        <p className="text-sm">{error}</p>
        <p className="text-xs mt-1">请确认后端服务正在运行 (http://localhost:8000)</p>
      </div>
    )
  }

  // ── 连接中 ──
  if (!connected && messages.length === 0) {
    return (
      <div className="card">
        <div className="flex items-center justify-between mb-1">
          <h3>群聊直播</h3>
        </div>
        <div className="chat-box flex items-center justify-center" style={{minHeight: 120}}>
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
      <div className="flex items-center justify-between mb-1">
        <h3>群聊直播 {connected ? '🟢' : '🟡'}</h3>
        <div className="flex gap-1">
          <button className="btn btn-primary" style={{padding:'2px 10px',fontSize:12}}
            onClick={() => setShowWs(!showWs)}>
            {showWs ? '简洁模式' : '全部消息'} ({messages.length})
          </button>
        </div>
      </div>
      <div className="chat-box">
        {displayMessages.map(msg => (
          <div key={msg.id} className={`chat-msg ${msg.role}`}>
            <div className="sender">
              {senderIcon(msg.sender)} {msg.sender}
              {msg.action === 'invite' && ' → 邀请'}
              {msg.action === 'done' && ' → 完成'}
              {msg.action === 'error' && ' → 错误'}
            </div>
            <div className="pre">{msgPreview(msg.content)}</div>
          </div>
        ))}
        <div ref={bottomRef} />
      </div>
      {displayMessages.length === 0 && (
        <div className="empty-state">
          <p className="text-lg mb-1">🤖</p>
          <p className="text-sm">等待群聊开始...</p>
          <p className="text-xs mt-1">上传商品图片后，中心决策者将自动协调 Agent 群聊</p>
        </div>
      )}
    </div>
  )
}
