import { useState, useEffect, useRef, useCallback } from 'react'
import { wsUrl } from './api'

export function useWebSocket(sessionId) {
  const [messages, setMessages] = useState([])
  const [connected, setConnected] = useState(false)
  const [error, setError] = useState('')
  const wsRef = useRef(null)

  useEffect(() => {
    if (!sessionId) return

    const url = wsUrl(`/ws/sessions/${sessionId}`)
    let ws
    try {
      ws = new WebSocket(url)
    } catch (err) {
      setError(`WebSocket 连接失败: ${err.message}`)
      return
    }
    wsRef.current = ws

    ws.onopen = () => { setConnected(true); setError('') }
    ws.onclose = () => setConnected(false)
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
      setError('WebSocket 连接中断，请检查后端服务')
    }

    return () => ws.close()
  }, [sessionId])

  const clear = useCallback(() => setMessages([]), [])

  return { messages, connected, error, clear }
}
