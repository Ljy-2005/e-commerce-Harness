/**
 * 浏览器侧 API Key 校验（仿 DSH `settings-models/apiKey` 的字段级判定）
 *
 * 语义：
 * - 空串不是失败：卡片打开时输入框总是空的，空 = 保持已存密钥不变；
 * - 纯空白是失败（不能把用户键入的内容静默丢弃）；
 * - 非法字符是失败：仅允许可打印 ASCII（`[\x21-\x7E]`，即 HTTP 头可携带字符集）；
 * - 粘贴整行 `NAME=value`（环境变量行）或带引号包裹的值 → 同"格式失败"处理
 *   （对用户而言下一步动作一样：看一眼密钥重新粘贴）。
 */

export const KEY_FAILURE_TEXT = {
  blank: '密钥只包含空白字符，不会被提交；留空表示保持现状',
  illegal:
    '密钥含不可用于请求头的字符（仅允许可打印 ASCII）；' +
    '若粘贴了 NAME=value 或带引号的值，请只粘贴值本身',
}

const PRINTABLE_ASCII = /^[\x21-\x7E]+$/
const ENV_LINE = /^[A-Z][A-Z0-9_]{2,}=/
const WRAPPED_QUOTES = /^(["']).*\1$/

/**
 * 判定输入框当前值能否提交。
 * @param {string} draft 输入框原文（未 trim）
 * @returns {'blank'|'illegal'|undefined} 失败原因键；undefined 表示可提交
 */
export function apiKeyFailure(draft) {
  if (!draft) return undefined
  const trimmed = draft.trim()
  if (!trimmed) return 'blank'
  if (!PRINTABLE_ASCII.test(trimmed)) return 'illegal'
  if (ENV_LINE.test(trimmed)) return 'illegal'
  if (WRAPPED_QUOTES.test(trimmed)) return 'illegal'
  return undefined
}

/** 失败键 → 展示文案 */
export function apiKeyFailureText(key) {
  return key ? KEY_FAILURE_TEXT[key] : ''
}
