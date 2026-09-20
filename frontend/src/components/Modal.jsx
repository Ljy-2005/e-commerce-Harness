import { useEffect, useRef } from 'react'

/**
 * 通用悬浮弹窗（项目首个 Modal 组件，风格词库使用）。
 *
 * 行为：
 * - `role="dialog"` + `aria-modal="true"`，标题用 aria-labelledby 关联；
 * - ESC 关闭 / 点遮罩关闭（点卡片内部不关闭）；
 * - 打开时锁背景滚动，关闭时还原；打开瞬间焦点进入卡片（可访问性）；
 * - 点关闭按钮、ESC 都会走 onClose（父组件负责置 null）。
 */
export default function Modal({ title = '', onClose, children, footer = null, width = 560 }) {
  const cardRef = useRef(null)

  useEffect(() => {
    function onKeyDown(e) {
      if (e.key === 'Escape') {
        e.stopPropagation()
        onClose?.()
      }
    }
    document.addEventListener('keydown', onKeyDown)
    const prevOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    cardRef.current?.focus?.()
    return () => {
      document.removeEventListener('keydown', onKeyDown)
      document.body.style.overflow = prevOverflow
    }
  }, [onClose])

  return (
    <div
      className="modal-backdrop"
      data-testid="modal-backdrop"
      onMouseDown={e => { if (e.target === e.currentTarget) onClose?.() }}
    >
      <div
        className="modal-card"
        role="dialog"
        aria-modal="true"
        aria-labelledby="modal-title"
        tabIndex={-1}
        ref={cardRef}
        style={{ maxWidth: width }}
      >
        <div className="modal-head">
          <h3 id="modal-title" className="modal-title">{title}</h3>
          <button className="modal-close" onClick={() => onClose?.()} aria-label="关闭">✕</button>
        </div>
        <div className="modal-body">{children}</div>
        {footer && <div className="modal-foot">{footer}</div>}
      </div>
    </div>
  )
}
