import { describe, it, expect } from 'vitest'
import { apiKeyFailure, apiKeyFailureText } from './apiKeyRules'

describe('apiKeyFailure（字段级校验，仿 DSH）', () => {
  it('空串不是失败：留空 = 保持已存密钥不变', () => {
    expect(apiKeyFailure('')).toBeUndefined()
  })

  it('正常密钥可通过（含 - _ . 等可打印 ASCII）', () => {
    expect(apiKeyFailure('sk-abcDEF123_-.' )).toBeUndefined()
    expect(apiKeyFailure('  sk-padded-key  ')).toBeUndefined()   // 首尾空白会被 trim
  })

  it('纯空白是失败（不静默丢弃用户输入）', () => {
    expect(apiKeyFailure('   ')).toBe('blank')
    expect(apiKeyFailure('\t\n')).toBe('blank')
  })

  it('非可打印 ASCII 是失败（中文/空格/控制字符）', () => {
    expect(apiKeyFailure('sk-中文密钥')).toBe('illegal')
    expect(apiKeyFailure('sk-a b')).toBe('illegal')
    expect(apiKeyFailure('sk-\u0001abc')).toBe('illegal')
  })

  it('粘贴 NAME=value 环境变量行是失败', () => {
    expect(apiKeyFailure('OPENAI_API_KEY=sk-abc')).toBe('illegal')
    expect(apiKeyFailure('DEEPSEEK_API_KEY=sk-x')).toBe('illegal')
  })

  it('带引号包裹的粘贴是失败', () => {
    expect(apiKeyFailure('"sk-abc"')).toBe('illegal')
    expect(apiKeyFailure("'sk-abc'")).toBe('illegal')
  })

  it('失败文案可按失败键取到', () => {
    expect(apiKeyFailureText('blank')).toContain('空白')
    expect(apiKeyFailureText('illegal')).toContain('可打印 ASCII')
    expect(apiKeyFailureText(undefined)).toBe('')
  })
})
