/**
 * 金额显示的唯一口径（后端 `src/harness/pricing.py` 的前端对应物）。
 *
 * ## 为什么有它（用户质疑，已核实成立）
 *
 * "不同的模型的花费是不同的，其次每个模型的花费又会随着时间被各大模型商来回修改，
 * 这意味着除非每次模型商修改价格的时候都及时更新修改不然会出现很大的误导。"
 *
 * 此前 7 处界面各写一份 `$x.toFixed(4)`：价格未标定（`amount == null`）时
 * `(null || 0).toFixed(4)` 会显示 **$0.0000** —— 把"不知道"显示成"没花钱"，
 * 正是用户说的误导。规则：
 *
 * 1. 用量是事实，永远显示（"未标定（2 张图）"里也要有张数）；
 * 2. 未标定 → 不显示金额（**不显示 0**）；
 * 3. 有金额 → `≈¥0.20`（`≈` 表示估算，带来源/更新日期 tooltip）；
 * 4. 前端**绝不自己估算**金额、绝不做币种换算 —— 数字只能来自后端价格表。
 */

/** 货币符号（与后端 `pricing.currency_symbol` 同一套口径） */
export function currencySymbol(currency) {
  return String(currency || '').toUpperCase() === 'CNY' ? '¥' : '$'
}

/** 金额小数位：<0.01 用 4 位（几厘的钱也要看得见），否则 2 位；最多 4 位有效小数 */
export function formatAmount(value) {
  const number = Number(value)
  if (!Number.isFinite(number)) return ''
  const digits = Math.abs(number) < 0.01 ? 4 : 2
  return number.toFixed(digits)
}

/**
 * 金额文案。
 *
 * @param {number|null|undefined} amount 后端给的金额；`null` = **该模型价格未标定**
 * @param {object} meta  `{currency, source, updated_at, stale, basis, usage:{images, calls}}`
 * @returns {string} `"≈¥0.20"` / `"≈$0.04"` / `"未标定（1 张图）"` / `"—"`
 */
export function formatCost(amount, meta = {}) {
  // 未走真实网络（Mock / 本地模拟）：$0 是**事实**，不是"价格未标定"
  if (meta.offline) return '$0（不走网络）'
  if (amount === null || amount === undefined || amount === '') {
    const usage = meta.usage || {}
    const images = Number(usage.images)
    if (Number.isFinite(images) && images > 0) return `未标定（${images} 张图）`
    const calls = Number(usage.calls)
    if (Number.isFinite(calls) && calls > 0) return `未标定（${calls} 次调用）`
    return '未标定'
  }
  const number = Number(amount)
  if (!Number.isFinite(number)) return '—'
  return `≈${currencySymbol(meta.currency)}${formatAmount(number)}`
}

/**
 * 金额悬停说明：一行讲清"这个数字哪来的、什么时候更新的、可不可信"。
 *
 * @param {object} meta 同 formatCost
 * @returns {string} 组装好的 `title` 文本（调用方自己挂到 DOM 上）
 */
export function costTitle(meta = {}) {
  const parts = []
  if (meta.offline) {
    return '本次未走真实网络调用（Mock / 本地模拟），$0 是事实而非估算'
  }
  if (meta.amount === null || meta.amount === undefined) {
    parts.push('价格未标定：该 路由/模型 在 config/pricing.yaml 与内置参考价里都没有条目')
    if (meta.basis) parts.push(`用量：${meta.basis}`)
    parts.push('金额请以供应商控制台账单为准，或用设置页「实测标定」写回单价')
    return parts.join('｜')
  }
  if (meta.source) parts.push(`来源：${meta.source}`)
  if (meta.basis) parts.push(`依据：${meta.basis}`)
  if (meta.updated_at) parts.push(`更新：${meta.updated_at}`)
  if (meta.currency) parts.push(`币种：${meta.currency}`)
  if (meta.stale) parts.push('⚠️ 价格可能已过期（>90 天未更新），请核对官方定价')
  if (meta.unknown_calls) {
    parts.push(`${meta.unknown_calls} 次调用价格未标定，金额未计入（用量仍如实记录）`)
  }
  parts.push('估算值（≈），实际以供应商账单为准')
  return parts.join('｜')
}

/**
 * 会话/作业级金额 meta：`cost_so_far` 只含**已标定**部分，
 * `cost_unknown_calls > 0` 时 tooltip 必须说清"另有 N 次未标定"，
 * 否则 `≈¥0.00` 会被读成"这一轮没花钱"。
 */
export function sessionCostMeta(session = {}) {
  const unknown = Number(session.cost_unknown_calls || 0)
  return {
    amount: session.cost_so_far,
    currency: session.cost_currency || 'CNY',
    unknown_calls: unknown > 0 ? unknown : 0,
  }
}

/**
 * 风格词库用量的金额文案（`/style-library` 的 `usage` 形状）。
 *
 * 口径与上面完全一致（复用 formatCost，不另写一份）：未标定绝不显示 0，
 * `usage.maybe_billed` 为真 = 没走网络 → `$0（不走网络）`。
 *
 * @param {object} usage `{calls, images, maybe_billed, cost:{amount,currency,source,basis,updated_at,stale}}`
 */
export function styleUsageCostText(usage = {}) {
  const cost = usage.cost || {}
  return formatCost(cost.amount, {
    offline: Boolean(usage.maybe_billed),
    currency: cost.currency,
    usage: { images: usage.images, calls: usage.calls },
  })
}

/** 风格词库用量的金额悬停说明（含"为什么不显示金额"） */
export function styleUsageCostTitle(usage = {}) {
  const cost = usage.cost || {}
  return costTitle({
    amount: cost.amount,
    offline: Boolean(usage.maybe_billed),
    currency: cost.currency,
    source: cost.source,
    basis: cost.basis,
    updated_at: cost.updated_at,
    stale: cost.stale,
    usage: { images: usage.images, calls: usage.calls },
  })
}
