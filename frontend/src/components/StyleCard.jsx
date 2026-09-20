import { costText, costTitle, usageLine, appliesSummary } from '../styleFormat'

/** 网格首位的「＋」卡：空态是唯一一张，有词条时排在最前 */
export function StyleAddCard({ onClick, label = '新建风格词条' }) {
  return (
    <button type="button" className="style-card style-add-card" data-testid="style-add-card" onClick={onClick}>
      <span className="style-add-plus" aria-hidden>＋</span>
      <span className="text-sm">{label}</span>
    </button>
  )
}

/**
 * 一套风格词条卡（启用/分析中/失败/内置 四态）。
 * 只有 `ready` 才展示用量行与操作按钮；分析中给秒级预期，失败给可读原因 + 重试。
 */
export default function StyleCard({ entry, busy = false, onAction }) {
  const builtin = entry.source === 'builtin'
  const status = entry.status || 'ready'
  const usage = entry.usage || null
  const cost = usage ? costText(usage) : ''
  const hasAmount = usage?.cost?.amount != null && !usage.maybe_billed

  return (
    <div className={`style-card ${status === 'failed' ? 'card-error' : ''}`} data-testid={`style-card-${entry.id}`}>
      {status === 'ready' && (
        <div className="style-cover" data-testid={`style-cover-${entry.id}`}>
          {entry.cover
            ? <img src={entry.cover} alt={`${entry.name} 封面`} />
            : <span className="text-xs text-muted">无封面</span>}
        </div>
      )}

      <div className="style-card-head">
        <span className="strong style-card-name">{entry.name}</span>
        {entry.enabled && status === 'ready' && <span className="badge badge-ok">启用</span>}
        {!entry.enabled && status === 'ready' && <span className="badge badge-muted">已停用</span>}
        {builtin && <span className="badge badge-info">内置</span>}
        {entry.as_anchor && <span className="badge badge-warn">锚点</span>}
      </div>

      {status === 'ready' && (
        <>
          <p className="text-xs style-applies">{appliesSummary(entry.applies_to, entry.as_anchor)}</p>
          {entry.summary && <p className="text-xs style-summary">{entry.summary}</p>}
          {entry.style_words && <p className="text-xs text-muted">风格词：{entry.style_words}</p>}

          {/* 套图结构（用户 2026-09-19："我给的是一套图片……还有套图的制作习惯"） */}
          {entry.shot_role_count > 0 && (
            <p className="text-xs" data-testid={`style-sequence-${entry.id}`}>
              <span className="chip">套图结构 {entry.shot_role_count} 张</span>
              {entry.shot_flow && <span className="text-muted"> {entry.shot_flow}</span>}
            </p>
          )}
          {/* 照片补齐了但还没重新分析：如实提示，别让用户以为档案已经跟上 */}
          {entry.photo_count > 0 && usage?.images > 0 && entry.photo_count > usage.images && (
            <p className="text-xs text-muted" data-testid={`style-stale-${entry.id}`}>
              已存 {entry.photo_count} 张／已分析 {usage.images} 张 —— 点「再分析」让档案跟上
            </p>
          )}
          {/* 启用这一套时自动停用了谁（一轮会话一套风格词） */}
          {(entry.auto_disabled || []).length > 0 && (
            <p className="text-xs text-muted" data-testid={`style-auto-disabled-${entry.id}`}>
              启用本套时已自动停用：{entry.auto_disabled.map(name => `「${name}」`).join('、')}
            </p>
          )}

          {/* 用量行：调用次数 / 张数 / 耗时 / 模型 */}
          {usage && (
            <p className="text-xs style-usage" data-testid={`style-usage-${entry.id}`}>{usageLine(usage)}</p>
          )}
          {/* 金额：未走网络显示 $0（不走网络）；未标定显示"未标定"；有金额才带悬停来源说明 */}
          {usage && (
            <p className="text-xs style-cost" title={costTitle(usage)} data-testid={`style-cost-${entry.id}`}>
              {cost}
              {hasAmount && <span className="text-muted">（悬停看来源）</span>}
            </p>
          )}
          <p className="text-xs text-muted">
            被 {entry.adopted || 0} 次会话采用 <span className="chip">参考值</span>
          </p>

          <div className="style-card-actions">
            <button className="btn btn-ghost btn-sm" disabled={busy}
              onClick={() => onAction('preview', entry)}>预览</button>
            {!builtin && (
              <button className="btn btn-ghost btn-sm" disabled={busy}
                onClick={() => onAction('edit', entry)}>编辑</button>
            )}
            <button className="btn btn-ghost btn-sm" disabled={busy}
              onClick={() => onAction('reanalyze', entry)}>再分析</button>
            <button className="btn btn-ghost btn-sm" disabled={busy}
              onClick={() => onAction('toggle', entry)}>{entry.enabled ? '停用' : '启用'}</button>
            {!builtin && (
              <button className="btn btn-danger btn-sm" disabled={busy}
                onClick={() => onAction('delete', entry)}>删除</button>
            )}
          </div>
        </>
      )}

      {status === 'analyzing' && (
        <div className="style-card-progress">
          <div className="spinner" />
          <p className="text-sm mt-1">分析中… 约 20–40 秒</p>
          <p className="text-xs text-muted">照片只用于风格分析，结束后可继续编辑</p>
          <div className="style-card-actions">
            <button className="btn btn-ghost btn-sm" disabled={busy}
              onClick={() => onAction('delete', entry)}>删除</button>
          </div>
        </div>
      )}

      {status === 'failed' && (
        <div className="style-card-progress">
          <p className="text-lg" aria-hidden>⚠️</p>
          <p className="text-sm">风格分析失败</p>
          <p className="text-xs style-error-reason">{entry.error || '未记录具体原因'}</p>
          <div className="style-card-actions">
            <button className="btn btn-primary btn-sm" disabled={busy}
              onClick={() => onAction('reanalyze', entry)}>重试</button>
            {!builtin && (
              <button className="btn btn-ghost btn-sm" disabled={busy}
                onClick={() => onAction('edit', entry)}>编辑</button>
            )}
            <button className="btn btn-ghost btn-sm" disabled={busy}
              onClick={() => onAction('delete', entry)}>删除</button>
          </div>
        </div>
      )}
    </div>
  )
}
