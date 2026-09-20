/**
 * 金额显示口径测试（`frontend/src/cost.js`）
 *
 * 用户质疑（已核实成立）："不同的模型的花费是不同的，其次每个模型的花费又会随着时间被
 * 各大模型商来回修改，这意味着除非每次模型商修改价格的时候都及时更新修改不然会出现
 * 很大的误导。"
 *
 * 此前 7 处界面各写 `$x.toFixed(4)`：`amount == null`（价格未标定）时显示 **$0.0000**，
 * 等于把"不知道"说成"没花钱"。本文件钉住：有价带 `≈` + 币种；无价显示"未标定（N 张图）"。
 */

import { describe, expect, it } from 'vitest'

import {
  costTitle, currencySymbol, formatAmount, formatCost, sessionCostMeta,
  styleUsageCostText, styleUsageCostTitle,
} from './cost'

describe('formatCost 有金额', () => {
  it('人民币用 ¥，带 ≈ 前缀（估算标记）', () => {
    expect(formatCost(0.2, { currency: 'CNY' })).toBe('≈¥0.20')
  })

  it('美元用 $', () => {
    expect(formatCost(0.04, { currency: 'USD' })).toBe('≈$0.04')
  })

  it('未给币种按 $ 处理（与后端 currency_symbol 同口径）', () => {
    expect(formatCost(1.5, {})).toBe('≈$1.50')
  })

  it('小于 0.01 用 4 位小数（几厘的钱也要看得见）', () => {
    expect(formatCost(0.00014, { currency: 'USD' })).toBe('≈$0.0001')
    expect(formatCost(0.0021, { currency: 'CNY' })).toBe('≈¥0.0021')
  })

  it('大于等于 0.01 用 2 位小数', () => {
    expect(formatCost(0.123456, { currency: 'USD' })).toBe('≈$0.12')
    expect(formatCost(12.5, { currency: 'CNY' })).toBe('≈¥12.50')
  })

  it('0 是**已知金额**（真的没花钱），不是"未标定"', () => {
    // 位数规则对 0 同样生效（|0| < 0.01 → 4 位）；关键是**不显示"未标定"**
    expect(formatCost(0, { currency: 'USD' })).toBe('≈$0.0000')
    expect(formatCost(0, { currency: 'USD' })).not.toBe('未标定')
  })

  it('金额不可解析 → —（不猜）', () => {
    expect(formatCost('abc', {})).toBe('—')
  })
})

describe('formatCost 未标定', () => {
  it('null / undefined → 未标定', () => {
    expect(formatCost(null, {})).toBe('未标定')
    expect(formatCost(undefined, {})).toBe('未标定')
  })

  it('有张数时带上张数（用量是事实，永远显示）', () => {
    expect(formatCost(null, { usage: { images: 1 } })).toBe('未标定（1 张图）')
    expect(formatCost(null, { usage: { images: 4 } })).toBe('未标定（4 张图）')
  })

  it('没有张数时退回调用次数', () => {
    expect(formatCost(null, { usage: { calls: 3 } })).toBe('未标定（3 次调用）')
  })

  it('张数优先于调用次数', () => {
    expect(formatCost(null, { usage: { images: 2, calls: 9 } })).toBe('未标定（2 张图）')
  })

  it('张数为 0 时退回次数（不写"未标定（0 张图）"这种废话）', () => {
    expect(formatCost(null, { usage: { images: 0, calls: 2 } })).toBe('未标定（2 次调用）')
  })

  it('绝不显示 $0.0000 这类假金额', () => {
    const text = formatCost(null, { currency: 'USD', usage: { images: 3 } })
    expect(text).not.toContain('$')
    expect(text).not.toContain('0.0000')
  })
})

describe('formatCost offline（Mock / 不走网络）', () => {
  it('offline → $0（不走网络）：此时 0 是事实，不是"未标定"', () => {
    expect(formatCost(null, { offline: true, usage: { images: 3 } })).toBe('$0（不走网络）')
    expect(costTitle({ offline: true })).toContain('未走真实网络调用')
  })
})

describe('costTitle', () => {
  it('有价：来源 / 依据 / 更新日期 / 币种 / 估算口径', () => {
    const text = costTitle({
      amount: 0.2, currency: 'CNY', source: '内置参考价',
      basis: '1 张图 × ¥0.2/张', updated_at: '2026-09-18',
    })
    expect(text).toContain('来源：内置参考价')
    expect(text).toContain('依据：1 张图 × ¥0.2/张')
    expect(text).toContain('更新：2026-09-18')
    expect(text).toContain('币种：CNY')
    expect(text).toContain('实际以供应商账单为准')
  })

  it('过期（>90 天）时给出提示', () => {
    expect(costTitle({ amount: 0.2, currency: 'CNY', stale: true }))
      .toContain('价格可能已过期')
  })

  it('未标定：说明原因并指路（设置页实测标定）', () => {
    const text = costTitle({ amount: null, basis: '2 张图（价格未标定）' })
    expect(text).toContain('价格未标定')
    expect(text).toContain('用量：2 张图（价格未标定）')
    expect(text).toContain('实测标定')
  })

  it('会话里有未标定调用时要说清（不并进金额）', () => {
    expect(costTitle({ amount: 0.5, currency: 'CNY', unknown_calls: 2 }))
      .toContain('2 次调用价格未标定')
  })
})

describe('辅助函数', () => {
  it('currencySymbol', () => {
    expect(currencySymbol('CNY')).toBe('¥')
    expect(currencySymbol('USD')).toBe('$')
    expect(currencySymbol('')).toBe('$')
  })

  it('formatAmount 位数规则', () => {
    expect(formatAmount(0.001)).toBe('0.0010')
    expect(formatAmount(0.01)).toBe('0.01')
    expect(formatAmount(3.14159)).toBe('3.14')
  })

  it('sessionCostMeta：带上未标定次数，供 tooltip 说明"另有 N 次"', () => {
    const meta = sessionCostMeta({ cost_so_far: 0.12, cost_unknown_calls: 3 })
    expect(meta.amount).toBe(0.12)
    expect(meta.unknown_calls).toBe(3)
    expect(formatCost(meta.amount, meta)).toBe('≈¥0.12')

    const clean = sessionCostMeta({ cost_so_far: 0.12 })
    expect(clean.unknown_calls).toBe(0)
  })
})

describe('styleUsageCostText / styleUsageCostTitle（风格词库用量形状）', () => {
  it('usage.cost.amount=null 且未走网络 → $0（不走网络）', () => {
    expect(styleUsageCostText({ images: 4, calls: 1, maybe_billed: true,
                                cost: { amount: null, currency: 'CNY' } }))
      .toBe('$0（不走网络）')
  })

  it('usage.cost.amount=null 但走了网络 → 未标定（N 张图）', () => {
    expect(styleUsageCostText({ images: 4, calls: 1, maybe_billed: false,
                                cost: { amount: null, currency: 'CNY' } }))
      .toBe('未标定（4 张图）')
    expect(styleUsageCostTitle({ images: 4, cost: { amount: null } }))
      .toContain('价格未标定')
  })

  it('usage.cost.amount 有值 → ≈金额', () => {
    expect(styleUsageCostText({ images: 4, maybe_billed: false,
                                cost: { amount: 0.42, currency: 'CNY' } }))
      .toBe('≈¥0.42')
  })
})
