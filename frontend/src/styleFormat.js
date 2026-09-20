/**
 * 风格词库的纯展示逻辑（无副作用，便于单测）。
 *
 * 金额口径**不在本文件**：一律走 `cost.js`（全站唯一口径，
 * 见其文件头："未标定 → 不显示金额，绝不当 0"）。本文件只做摘要/用量行/文件校验。
 */
import { styleUsageCostText, styleUsageCostTitle } from './cost'

/** "通用 · 全平台 · 锚点"这类适用范围摘要 */
export function appliesSummary(applies, asAnchor = false) {
  // 契约里 applies_to 是对象；后端若给可读字符串（老数据/降级）就直接展示，不崩
  if (typeof applies === 'string') {
    return [applies || '通用', asAnchor ? '锚点' : ''].filter(Boolean).join(' · ')
  }
  const a = applies || {}
  const kinds = (a.kinds || []).filter(Boolean)
  const platforms = (a.platforms || []).filter(Boolean)
  const categories = (a.categories || []).filter(Boolean)
  const slots = (a.slots || []).filter(Boolean)

  const parts = [kinds.length ? kinds.join('、') : '通用']
  parts.push(categories.length ? `品类：${categories.join('、')}` : '全品类')
  parts.push(platforms.length ? platforms.join('、') : '全平台')
  if (slots.length) parts.push(`槽位：${slots.join('、')}`)
  if (asAnchor) parts.push('锚点')
  return parts.join(' · ')
}

/** 用量行："1 次视觉调用 · 4 张图 · 12.3s · 模型名" */
export function usageLine(usage) {
  if (!usage) return ''
  const parts = [`${usage.calls ?? 1} 次视觉调用`]
  if (usage.images != null) parts.push(`${usage.images} 张图`)
  if (usage.elapsed_ms != null) parts.push(`${(Number(usage.elapsed_ms) / 1000).toFixed(1)}s`)
  if (usage.model) parts.push(usage.model)
  if (usage.attempts > 1) parts.push(`第 ${usage.attempts} 次尝试`)
  return parts.join(' · ')
}

/** 金额文案：金额可能未标定 → 不显示数字（口径来自 cost.js） */
export function costText(usage) {
  return styleUsageCostText(usage || {})
}

/** 金额悬停说明：为什么不显示金额 / 数字从哪来 */
export function costTitle(usage) {
  return styleUsageCostTitle(usage || {})
}

/**
 * 逐文件校验：张数上限 + 每张 ≤ MB。
 *
 * 口径**不在前端写死**：后端 `/style-library` 的 `stats.limits` 给出
 * `max_photos` / `max_photo_mb` / `max_total_mb`（此前前端写"≤6 张、每张 ≤20MB"，
 * 而那两处都与后端真实值不一致 —— 用户问"为什么只能 6 张"时才发现）。
 * 这里的常量只是**接口不可用时的兜底**。
 */
export const MAX_STYLE_FILES = 20
export const MAX_STYLE_FILE_MB = 10
export const MAX_STYLE_TOTAL_MB = 100

export function validateStyleFiles(incoming, existingCount = 0, options = {}) {
  const maxFiles = Number(options.maxFiles) > 0 ? Number(options.maxFiles) : MAX_STYLE_FILES
  const maxFileMb = Number(options.maxFileMb) > 0 ? Number(options.maxFileMb) : MAX_STYLE_FILE_MB
  const maxTotalMb = Number(options.maxTotalMb) > 0 ? Number(options.maxTotalMb) : MAX_STYLE_TOTAL_MB
  const files = Array.from(incoming || [])
  const accepted = []
  const errors = []
  let count = existingCount
  let total = Number(options.existingBytes) || 0
  for (const file of files) {
    if (!(file.type || '').startsWith('image/')) {
      errors.push(`${file.name || '文件'}：不是图片格式`)
      continue
    }
    if (file.size > maxFileMb * 1024 * 1024) {
      errors.push(`${file.name || '文件'}：超过 ${maxFileMb}MB`)
      continue
    }
    if (count >= maxFiles) {
      errors.push(`${file.name || '文件'}：最多 ${maxFiles} 张，已忽略`)
      continue
    }
    if (total + file.size > maxTotalMb * 1024 * 1024) {
      errors.push(`${file.name || '文件'}：这一批总量会超过 ${maxTotalMb}MB，已忽略`)
      continue
    }
    accepted.push(file)
    count += 1
    total += file.size
  }
  return { accepted, errors }
}

// ── 套图结构（逐张角色）──
// 用户 2026-09-19："我给的是一套图片……还有套图的制作习惯"。角色名由**槽位目录**派生
// （`/api/platforms` 的 slot_roles 并集），界面只让用户从清单里选 id，不自由写词。

/** 槽位清单（去重，保留后端给的名字）；首项是"（无法对应）" */
export function slotOptions(platforms = []) {
  const seen = new Map()
  for (const platform of platforms || []) {
    for (const role of platform?.slot_roles || []) {
      const slotId = String(role?.slot_id || '').trim()
      if (slotId && !seen.has(slotId)) {
        seen.set(slotId, { slot_id: slotId, label: role.label || slotId })
      }
    }
  }
  return [{ slot_id: '', label: '（无法对应）' }, ...Array.from(seen.values())]
}

/** 详情（photos + shot_roles）→ 逐张行；长度取两者较大值，缺的留空 */
export function roleRows(photos = [], roles = []) {
  const byNumber = new Map((roles || []).map(role => [Number(role?.number) || 0, role || {}]))
  const count = Math.max((photos || []).length, (roles || []).length)
  return Array.from({ length: count }, (_, index) => {
    const role = byNumber.get(index + 1) || {}
    return {
      number: index + 1,
      photo: (photos || [])[index] || '',
      slot: role.slot || '',
      treatment: role.treatment || '',
    }
  })
}

/**
 * 逐张行 → 后端 shot_roles。**行号即照片序号**：中间行留空时后面的行号不回填
 * （"第3张是成分配方图"不能因为第2张没填就变成第2张 —— 那会让提示词按错的照片写画面）。
 */
export function rowsToRoles(rows = []) {
  return (rows || [])
    .map((row, index) => ({
      number: index + 1,
      slot: String(row?.slot || '').trim(),
      treatment: String(row?.treatment || '').trim(),
    }))
    .filter(row => row.slot || row.treatment)
}
