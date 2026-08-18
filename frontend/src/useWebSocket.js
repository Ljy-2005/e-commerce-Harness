import { useState, useEffect, useRef, useCallback } from 'react'
import { wsUrl } from './api'

export function useWebSocket(sessionId) {
  const [messages, setMessages] = useState([])
  const [connected, setConnected] = useState(false)
  const [error, setError] = useState('')
  const wsRef = useRef(null)

  useEffect(() => {
    if (!sessionId) return

    let ws = null
    let closed = false
    let retryTimer = null

    // 审计修复：断线自动重连（3s 后重试，卸载/切换会话时停止）
    const connect = () => {
      const url = wsUrl(`/ws/sessions/${sessionId}`)
      try {
        ws = new WebSocket(url)
      } catch (err) {
        setError(`WebSocket 连接失败: ${err.message}`)
        return
      }
      wsRef.current = ws

      ws.onopen = () => { setConnected(true); setError('') }
      ws.onmessage = (e) => {
        try {
          const msg = JSON.parse(e.data)
          setMessages(prev => {
            if (prev.some(m => m.id === msg.id)) return prev
            return [...prev, msg]
          })
        } catch {}
      }
      ws.onerror = () => {
        setConnected(false)
        setError('WebSocket 连接中断，正在重连…')
      }
      ws.onclose = () => {
        setConnected(false)
        if (!closed) retryTimer = setTimeout(connect, 3000)
      }
    }

    connect()
    return () => {
      closed = true
      if (retryTimer) clearTimeout(retryTimer)
      if (ws) ws.close()
      wsRef.current = null
    }
  }, [sessionId])

  const clear = useCallback(() => setMessages([]), [])

  // M3 群聊插话：向服务端发送指令（服务端会广播回来）
  const send = useCallback((obj) => {
    const ws = wsRef.current
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify(obj))
      return true
    }
    return false
  }, [])

  return { messages, connected, error, clear, send }
}
