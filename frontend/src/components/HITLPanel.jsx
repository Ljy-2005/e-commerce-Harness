import { useState } from 'react'
import { submitDecision } from '../api'

export default function HITLPanel({ sessionId, onDecision }) {
  const [submitting, setSubmitting] = useState(false)

  async function handle(action) {
    setSubmitting(true)
    try {
      await submitDecision(sessionId, action)
      onDecision()
    } catch (err) {
      alert('操作失败: ' + err.message)
    }
    setSubmitting(false)
  }

  return (
    <div className="card" style={{borderColor: '#f59e0b'}}>
      <h3 className="mb-1">⚠ 等待人工审查</h3>
      <p className="text-sm mb-2">审查评分较低，需要你做出判断。</p>
      <div className="flex gap-1">
        <button className="btn btn-success" disabled={submitting} onClick={() => handle('approve')}>
          ✅ 通过（接受当前结果）
        </button>
        <button className="btn btn-warning" disabled={submitting} onClick={() => handle('retry')}>
          🔄 重试（重新生成）
        </button>
        <button className="btn btn-danger" disabled={submitting} onClick={() => handle('reject')}>
          ❌ 拒绝（终止任务）
        </button>
      </div>
    </div>
  )
}
