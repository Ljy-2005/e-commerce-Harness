import { useState, useEffect, useRef, useCallback } from 'react'

export function useWebSocket(sessionId) {
  const [messages, setMessages] = useState([])
  const [connected, setConnected] = useState(false)
  const wsRef = useRef(null)

  useEffect(() => {
    if (!sessionId) return

    const url = `ws://localhost:8000/ws/sessions/${sessionId}`
    const ws = new WebSocket(url)
    wsRef.current = ws

    ws.onopen = () => setConnected(true)
    ws.onclose = () => setConnected(false)
    ws.onmessage = (e) => {
      try {
        const msg = JSON.parse(e.data)
        setMessages(prev => {
          // 去重
          if (prev.some(m => m.id === msg.id)) return prev
          return [...prev, msg]
        })
      } catch {}
    }
    ws.onerror = () => setConnected(false)

    return () => ws.close()
  }, [sessionId])

  const clear = useCallback(() => setMessages([]), [])

  return { messages, connected, clear }
}
