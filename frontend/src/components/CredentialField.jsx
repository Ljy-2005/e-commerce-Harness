import { useState } from 'react'
import { apiKeyFailure, apiKeyFailureText } from '../apiKeyRules'

/**
 * 单个凭据槽（一个环境变量）的编辑器 —— 从原 ProviderKeyRow 抽出。
 *
 * 沿用 DSH 的字段语义：
 * - 输入框总是为空打开：留空 = 保持已存密钥不变（只写，从不回显）；
 * - 字段级校验在输入时即给出，非法输入不提交；
 * - 保存失败保持展示并给出 Host 诊断；成功走 role="status" 无障碍消息；
 * - 环境变量供给的凭据 → 只读锁定（改了也不生效，显式说明原因）。
 */
export default function CredentialField({ item, onSave, onClear }) {
  const [draft, setDraft] = useState('')
  const [revealed, setRevealed] = useState(false)
  const [busy, setBusy] = useState(false)
  const [feedback, setFeedback] = useState(null)   // { ok: bool, text: string }
  const [confirmClear, setConfirmClear] = useState(false)

  const locked = item.source === 'env'
  const failure = apiKeyFailure(draft)
  const canApply = !locked && !failure && draft.trim().length > 0 && !busy

  async function apply() {
    if (!canApply) return
    setBusy(true)
    setFeedback(null)
    try {
      await onSave(item.env, draft.trim())
      setDraft('')
      setRevealed(false)
      setFeedback({ ok: true, text: '已保存并立即生效（Provider 注册表已重载）' })
    } catch (err) {
      setFeedback({ ok: false, text: err?.message || '保存失败' })
    }
    setBusy(false)
  }

  async function clear() {
    setBusy(true)
    setFeedback(null)
    try {
      await onClear(item.env)
      setDraft('')
      setConfirmClear(false)
      setFeedback({ ok: true, text: '已清除该密钥，该凭据回到未配置状态' })
    } catch (err) {
      setFeedback({ ok: false, text: err?.message || '清除失败' })
    }
    setBusy(false)
  }

  return (
    <div className="credential-field">
      <div className="credential-head">
        <span className={`dot ${locked ? 'dot-info' : item.configured ? (item.available ? 'dot-ok' : 'dot-warn') : 'dot-idle'}`}
              aria-hidden="true" />
        <span className="credential-name">{item.name}</span>
        <span className="provider-env">{item.env}</span>
        <span className="provider-spacer" />
        <span className="provider-state">
          {locked ? '环境变量供给 · 只读'
            : item.configured ? (item.available ? '已配置 · 生效中' : '已配置 · 未激活')
              : '未配置'}
        </span>
      </div>

      {locked ? (
        <div className="lock-note">
          🔒 该密钥由环境变量 <code className="text-xs">{item.env}</code> 提供：环境变量优先级最高，
          经设置页保存不会生效。请修改环境变量后重启服务（或清除该变量后在此处配置）。
        </div>
      ) : (
        <>
          <div className="key-row">
            <div className="key-field">
              <input
                className="input"
                type={revealed ? 'text' : 'password'}
                autoComplete="off"
                spellCheck="false"
                aria-label={`${item.name} 的 API 密钥`}
                placeholder={item.configured ? '已配置（输入新值覆盖，留空保持不变）' : '粘贴 API Key'}
                value={draft}
                onChange={e => { setDraft(e.target.value); setFeedback(null) }}
              />
              <button
                type="button"
                className="key-reveal"
                aria-label={revealed ? '隐藏密钥' : '显示密钥'}
                onClick={() => setRevealed(!revealed)}
              >
                {revealed ? '隐藏' : '显示'}
              </button>
            </div>
            <div className="key-actions">
              <button type="button" className="btn btn-primary btn-sm" disabled={!canApply} onClick={apply}>
                {busy ? '应用中...' : '应用'}
              </button>
              {item.configured && !confirmClear && (
                <button type="button" className="btn btn-danger btn-sm" disabled={busy}
                        onClick={() => setConfirmClear(true)}>
                  清除
                </button>
              )}
              {item.configured && confirmClear && (
                <>
                  <span className="text-xs">确认清除 <b>{item.name}</b>？</span>
                  <button type="button" className="btn btn-danger btn-sm" disabled={busy} onClick={clear}>
                    确认清除
                  </button>
                  <button type="button" className="btn btn-ghost btn-sm" disabled={busy}
                          onClick={() => setConfirmClear(false)}>
                    取消
                  </button>
                </>
              )}
            </div>
          </div>

          {failure ? (
            <div className="key-error" role="alert">{apiKeyFailureText(failure)}</div>
          ) : (
            <div className="key-hint">
              只写保存到 <code className="text-xs">config/secrets.yaml</code>（已 gitignore），
              保存后立即重建 Provider 注册表生效；页面从不回显已存密钥。
            </div>
          )}

          {feedback && (
            <div className={`key-status ${feedback.ok ? 'ok' : 'err'}`} role="status" aria-live="polite">
              {feedback.ok ? '✅ ' : '❌ '}{feedback.text}
            </div>
          )}
        </>
      )}
    </div>
  )
}
