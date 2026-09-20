/**
 * 群聊消息的**纯展示逻辑**（无副作用，便于单测）。
 *
 * 用户反馈（2026-09-20）："会话里 agent 的对话里面显示的会很直白，会给出代码原文，
 * 但实际上我们就只要文字的内容显示，这样也能减少隐私的暴露。"
 *
 * 取证：`ChatPanel.jsx` 的折叠预览以 `JSON.stringify(content).slice(0,120)` 兜底，
 * 「展开完整内容」更是把整条 content 全量 JSON 打出来 —— 生图员的产物里有
 * `image_url`（方舟 TOS 签名地址，查询串带 `X-Tos-Credential` / `X-Tos-Signature`）、
 * `base64_data`（整段图像数据）、`generation_params`（内部参数），
 * 会话详情页的「完整提示词 / 完整分析结果」同款。
 *
 * 本文件只解决"**显示什么**"，不动后端消息信封：协调者/审查员读的仍是原始
 * `content`，脱敏只发生在展示层 —— 所以这次改动没有流程风险。
 *
 * 三条纪律：
 * 1. **人话优先**：后端消息大多自带 `content.message`（给用户看的那句话），
 *    它必须优先于任何结构化字段；没有它才退到"字段清单摘要"，**绝不退到 JSON**；
 * 2. **脱敏但如实**：隐藏了什么必须能说出来（`hiddenKeys`），不静默丢失信息；
 * 3. **原文仍可查**：技术详情默认折叠、需要排障时能打开。
 */

/** 隐藏值时给用户看的占位（说明"这里本来有东西，只是不显示"） */
export const REDACTED_IMAGE = '［已省略图片数据］'
export const REDACTED_URL = '［图片地址（已隐藏签名）］'
export const REDACTED_FILE = '［本地图片文件］'
/** 输入框里展示提示词原文时的上限（避免一条消息撑爆气泡） */
export const MAX_PROMPT_PREVIEW_CHARS = 4000

/** 认得出"这是整段图像数据"（base64 或 data URI） */
function looksLikeImageBlob(value) {
  const text = String(value || '')
  if (text.startsWith('data:image/')) return true
  if (text.length < 64) return false
  return /^[A-Za-z0-9+/=\r\n]+$/.test(text)
}

/** 签名 URL 的判定：本身不长，但查询串里带着凭据/签名 */
function looksLikeSignedUrl(value) {
  const text = String(value || '')
  if (!/^https?:\/\//i.test(text)) return false
  return /x-tos-signature|x-tos-credential|signature=|x-amz-signature|accesskeyid|signedheaders/i
    .test(text)
}

function isUrl(value) {
  return /^https?:\/\//i.test(String(value || ''))
}

/** 本地落盘路径只留文件名：`default/会话/pinduoduo_…_1.jpg` → `pinduoduo_…_1.jpg` */
export function safeSavedPath(value) {
  const text = String(value || '').trim()
  if (!text) return ''
  return text.split(/[\\/]/).filter(Boolean).pop() || text
}

/** 字段名 → 中文标签（显示给人看，不显示键名） */
const FIELD_LABELS = {
  prompt_plan: '逐张提示词',
  prompt_lint: '提示词体检',
  prompt_review: '提示词审美审核',
  quality_report: '本地体检',
  set_plan_coverage: '套图覆盖度',
  set_plan_summary: '套图编排摘要',
  style_refs: '风格档案',
  images: '生成图片',
  image_notes: '生图说明',
  review: '审查结果',
  compliance: '合规结果',
  identity_unconfirmed: '商品身份未确认',
  product_identity: '商品身份卡',
  blocked_reason: '阻断原因',
  error_history: '失败历史',
  cost_unknown_images: '未标定价格的张数',
  generation_params: '生图参数',
  reference_count: '参考图数量',
  prompt_chars: '提示词字数',
  base_image_url: '底图地址',
  slot_copy: '排版文案',
  brand_palette: '品牌色板',
  visible_text: '包装上的可见文字',
  analysis: '分析结果',
  facts: '商品事实',
  memory_context: '记忆上下文',
  context_transition_summary: '上下文摘要',
}

export function fieldLabel(key) {
  return FIELD_LABELS[key] || ''
}

/** 内部键（技术细节，不该出现在给用户的正文里） */
export const INTERNAL_KEYS = new Set([
  'generation_params', 'reference_count', 'reference_bytes', 'ignored_params',
  'prompt_chars', 'image_count', 'text_font', 'text_items', 'text_status',
  'cost_unknown', 'cost_unknown_images', 'elapsed_ms', 'window',
  'visible_text', 'base_image_url', 'compose', 'slot_copy',
])

/**
 * 消息预览（折叠态那一行）。
 *
 * 顺序刻意如此：**具体的结构化卡片 → 通用的 message → 字段清单摘要**。
 * `message` 排在结构化判断之后，是为了不抢走"体检 ❌ 2 项硬伤"这种更精确的说法。
 */
export function msgPreview(content) {
  if (!content) return ''
  if (typeof content !== 'object') return String(content)
  // 风格词库：本次采用了哪些风格词条/原型（artifacts.prompts.style_refs 的群聊回放）
  if (content.style_refs) return content.style_refs.message || '🎨 采用风格档案'
  if (content.error) return `❌ ${content.error}`
  if (content.category) return `品类: ${content.category}`
  if (content.overall_score) return `评分: ${content.overall_score}/100 | ${content.verdict}`
  if (content.passed !== undefined) return `合规: ${content.passed ? '通过' : '不通过'}`
  if (content.decision) return `决策: ${content.decision}`
  if (content.feedback) return `反馈: ${content.feedback?.slice(0, 80)}`
  if (content.hitl) return '⏸ 需要人工审查'
  if (content.context_action) return `上下文: ${content.context_action}`
  if (content.memory_recall) return `🧠 ${String(content.memory_recall).slice(0, 100)}`
  if (content.winner) return `🏆 最优: ${content.winner} (${content.winner_score}/100)`
  if (content.interjection) return `📣 用户插话: ${content.interjection}`
  if (content.agent_name) {
    return `邀请: ${content.agent_name} — ${content.task_brief?.slice(0, 60) || ''}`
  }
  if (content.action === 'done') return '✅ 任务完成'
  // 逐张提示词 / 提示词体检 / 审美审核（用户："要能指定每一张的提示词"）
  if (content.prompt_plan?.length) return `📝 本套逐张提示词（${content.prompt_plan.length} 张）`
  if (content.prompt_lint) {
    const errors = content.prompt_lint.errors?.length || 0
    return errors ? `🧪 提示词体检：❌ ${errors} 项硬伤` : '🧪 提示词体检：✅ 通过'
  }
  if (content.prompt_review) return content.prompt_review.message || '🎨 提示词审核'
  if (content.revised_slots !== undefined) {
    const n = content.revised_slots?.length || 0
    return `🎨 提示词审核：已改写 ${n} 张${content.message ? `｜${String(content.message).slice(0, 60)}` : ''}`
  }
  // 生图员输出为 Mock 占位图时明确提示（未配置生图模型）
  if (content.images && Array.isArray(content.images)
    && content.images.every(i => (i.model_used || '').startsWith('mock') || (i.image_url || '').startsWith('data:image/svg+xml'))) {
    return '⚠ 输出为占位图：未配置生图模型（DeepSeek 不支持生图，需 DALL-E / 即梦 / FLUX Key）'
  }
  // 通用文字字段：后端消息大多自带 `message`（体检播报等）——
  // 它必须优先于"字段清单摘要"，否则又是"该显示的文字被结构顶掉"
  if (content.message) return String(content.message).slice(0, 160)
  if (content.summary) return String(content.summary).slice(0, 160)
  // 最后兜底：字段清单摘要（**不是 JSON**）
  return summaryLine(content)
}

/** 结构化内容的字段清单摘要（替代原先的 JSON.stringify 兜底） */
export function summaryLine(content) {
  const keys = Object.keys(content || {}).filter(k => !k.startsWith('_'))
  if (keys.length === 0) return ''
  const named = keys.slice(0, 4).map(k => fieldLabel(k) || k)
  return `📋 ${named.join('、')}${keys.length > 4 ? ` 等 ${keys.length} 项` : ''}`
}

/**
 * 产出"给人看"的内容副本：隐藏图片数据/签名地址，落地路径只留文件名。
 *
 * 返回 **新对象**，原 `content` 不被修改（前端仍要用它渲染图片）。
 * 排障需要原文时用 `rawContent`。
 */
export function displayContent(content, { promptCharLimit = MAX_PROMPT_PREVIEW_CHARS } = {}) {
  if (content == null) return content
  if (typeof content !== 'object') return content
  if (Array.isArray(content)) return content.map(item => displayContent(item, { promptCharLimit }))
  const out = {}
  for (const [key, value] of Object.entries(content)) {
    if (key === 'base64_data') {
      out[key] = looksLikeImageBlob(value) ? REDACTED_IMAGE : value
    } else if (key === 'image_url' || key.endsWith('_url')) {
      if (looksLikeImageBlob(value)) out[key] = REDACTED_IMAGE
      else if (looksLikeSignedUrl(value)) out[key] = REDACTED_URL
      else out[key] = value
    } else if (key === 'saved_path') {
      out[key] = safeSavedPath(value)
    } else if (key === 'id' && looksLikeImageBlob(value)) {
      out[key] = REDACTED_IMAGE
    } else if (key === 'prompt_text' || key === 'prompt') {
      out[key] = truncateText(value, promptCharLimit)
    } else if (looksLikeImageBlob(value) && !isUrl(value)) {
      // 兜底：任何字段里塞了整段图像数据都收掉（长度阈值避免误伤正常短字段）
      out[key] = REDACTED_IMAGE
    } else {
      out[key] = displayContent(value, { promptCharLimit })
    }
  }
  return out
}

function truncateText(value, limit) {
  const text = String(value ?? '')
  if (!limit || text.length <= limit) return text
  return `${text.slice(0, limit)}…（原文共 ${text.length} 字，完整内容见技术详情）`
}

/**
 * 本次显示**隐藏了哪些字段** —— 如实告知，避免"少暴露"被误读成"藏起来"。
 * 返回中文短语数组（空数组 = 没隐藏任何东西）。
 */
export function hiddenKeys(content) {
  const found = new Set()
  const visit = (value) => {
    if (value == null) return
    if (Array.isArray(value)) { value.forEach(visit); return }
    if (typeof value !== 'object') return
    for (const [key, item] of Object.entries(value)) {
      if (key === 'base64_data') found.add('图片数据')
      else if (key.endsWith('_url') && looksLikeSignedUrl(item)) found.add('图片签名地址')
      else if (key === 'image_url' && looksLikeSignedUrl(item)) found.add('图片签名地址')
      else if (key === 'saved_path') found.add('本地文件路径')
      else if (isPlainObject(item) || Array.isArray(item)) visit(item)
      else if (looksLikeImageBlob(item) && !isUrl(item)) found.add('图片数据')
    }
  }
  visit(content)
  return [...found]
}

function isPlainObject(value) {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value)
}

/** 统一的技术详情文本（各页面共用，保证口径一致） */
export function rawContentText(content) {
  try {
    return JSON.stringify(displayContent(content, { promptCharLimit: 0 }), null, 2)
  } catch {
    return String(content)
  }
}

/**
 * 「详情字段」口径：脱敏后的内容**再摘掉内部实现键**。
 *
 * 与 `displayContent` 的分工：后者只做隐私脱敏（签名/base64/路径），
 * 前者进一步去掉"看了没用"的内部参数（`generation_params` 里的 `reference_bytes` 等）。
 * 原始字段仍可在「技术详情」里查到，不是隐藏。
 */
export function detailFields(content) {
  const shown = displayContent(content, { promptCharLimit: 0 })
  if (!isPlainObject(shown)) return shown
  const out = {}
  for (const [key, value] of Object.entries(shown)) {
    if (INTERNAL_KEYS.has(key)) continue
    out[key] = isPlainObject(value)
      ? Object.fromEntries(Object.entries(value).filter(([k]) => !INTERNAL_KEYS.has(k)))
      : value
  }
  return out
}
