import { useState } from 'react'
import { submitDecision } from '../api'

export default function HITLPanel({ sessionId, onDecision }) {
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState('')

  async function handle(action) {
    setSubmitting(true)
    setError('')
    try {
      await submitDecision(sessionId, action)
      onDecision()
    } catch (err) {
      setError(err.message || '操作失败，请重试')
    }
    setSubmitting(false)
  }

  return (
    <div className="card" style={{borderColor: '#f59e0b'}}>
      <h3 className="mb-1">⚠ 等待人工审查</h3>
      <p className="text-sm mb-2">审查评分较低，需要你做出判断。</p>

      {/* ── 提交中状态 ── */}
      {submitting && (
        <div className="flex items-center gap-1 mb-2">
          <div className="spinner" />
          <span className="text-sm">正在处理决策...</span>
        </div>
      )}

      {/* ── 错误状态 ── */}
      {error && (
        <div className="card card-error mb-2">
          <p className="text-sm">❌ {error}</p>
        </div>
      )}

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
