import { describe, it, expect } from 'vitest'
import {
  appliesSummary, usageLine, costText, costTitle, validateStyleFiles,
  MAX_STYLE_FILES, MAX_STYLE_FILE_MB,
  slotOptions, roleRows, rowsToRoles,
} from './styleFormat'

describe('appliesSummary（适用范围摘要）', () => {
  it('空适用范围 → 通用 · 全品类 · 全平台', () => {
    expect(appliesSummary({ kinds: [], slots: [], categories: [], platforms: [] }, false))
      .toBe('通用 · 全品类 · 全平台')
  })

  it('锚点追加"锚点"，命中品类/槽位如实展示', () => {
    expect(appliesSummary({ kinds: [], slots: [], categories: ['保健品'], platforms: [] }, true))
      .toBe('通用 · 品类：保健品 · 全平台 · 锚点')
    expect(appliesSummary({ kinds: ['photo'], slots: ['main_white'], categories: [], platforms: ['pinduoduo'] }, false))
      .toBe('photo · 全品类 · pinduoduo · 槽位：main_white')
  })

  it('字段缺失也不崩（契约里 applies_to 可能只给部分键）', () => {
    expect(appliesSummary(undefined, false)).toBe('通用 · 全品类 · 全平台')
    expect(appliesSummary({}, true)).toBe('通用 · 全品类 · 全平台 · 锚点')
  })

  it('后端降级给字符串时原样展示（不崩、不显示 undefined）', () => {
    expect(appliesSummary('photo · taobao', true)).toBe('photo · taobao · 锚点')
  })
})

describe('usageLine（用量行）', () => {
  it('调用次数 · 张数 · 秒 · 模型', () => {
    expect(usageLine({ calls: 1, images: 4, elapsed_ms: 12300, model: 'deepseek-vl-2' }))
      .toBe('1 次视觉调用 · 4 张图 · 12.3s · deepseek-vl-2')
  })

  it('重试过则标注第几次尝试；缺字段不显示占位', () => {
    expect(usageLine({ calls: 1, images: 2, elapsed_ms: 8000, model: 'm', attempts: 3 }))
      .toBe('1 次视觉调用 · 2 张图 · 8.0s · m · 第 3 次尝试')
    expect(usageLine({})).toBe('1 次视觉调用')
    expect(usageLine(null)).toBe('')
  })
})

describe('costText / costTitle（金额纪律：绝不估算，口径复用 cost.js）', () => {
  it('amount 为 null → 写"未标定"，不显示 0', () => {
    const usage = { maybe_billed: false, images: 4,
                    cost: { amount: null, currency: 'CNY', source: '未标定' } }
    expect(costText(usage)).toBe('未标定（4 张图）')
    expect(costTitle(usage)).toContain('价格未标定')
  })

  it('maybe_billed=true（Mock / 不走网络）→ $0（不走网络）', () => {
    const usage = { maybe_billed: true, images: 4, cost: { amount: null, currency: 'CNY' } }
    expect(costText(usage)).toBe('$0（不走网络）')
    expect(costTitle(usage)).toContain('未走真实网络调用')
  })

  it('有金额 → ≈¥ 两位小数 + 悬停来源说明', () => {
    const usage = {
      maybe_billed: false,
      cost: { amount: 0.42, currency: 'CNY', source: '实测均值', basis: '4 张图',
              updated_at: '2026-08-01', stale: true },
    }
    expect(costText(usage)).toBe('≈¥0.42')
    const title = costTitle(usage)
    expect(title).toContain('来源：实测均值')
    expect(title).toContain('依据：4 张图')
    expect(title).toContain('更新：2026-08-01')
    expect(title).toContain('价格可能已过期')
  })

  it('非 CNY 币种按币种符号显示；空 usage 不崩', () => {
    expect(costText({ cost: { amount: 1.5, currency: 'USD' } })).toBe('≈$1.50')
    expect(costText(null)).toBe('未标定')
  })
})

describe('validateStyleFiles（逐张校验）', () => {
  const img = (name, size = 10) => new File(['x'.repeat(size)], name, { type: 'image/jpeg' })

  it('正常图片全部接受', () => {
    const { accepted, errors } = validateStyleFiles([img('a.jpg'), img('b.jpg')], 0)
    expect(accepted).toHaveLength(2)
    expect(errors).toEqual([])
  })

  it('超过上限的部分逐张报错且不进入待上传列表', () => {
    const files = Array.from({ length: MAX_STYLE_FILES + 2 }, (_, i) => img(`a${i}.jpg`))
    const { accepted, errors } = validateStyleFiles(files, 0)
    expect(accepted).toHaveLength(MAX_STYLE_FILES)
    expect(errors).toEqual([
      `a${MAX_STYLE_FILES}.jpg：最多 ${MAX_STYLE_FILES} 张，已忽略`,
      `a${MAX_STYLE_FILES + 1}.jpg：最多 ${MAX_STYLE_FILES} 张，已忽略`,
    ])
  })

  it('张数上限可由后端 limits 覆盖（前端不硬编码 6）', () => {
    const files = Array.from({ length: 5 }, (_, i) => img(`a${i}.jpg`))
    const { accepted, errors } = validateStyleFiles(files, 0, { maxFiles: 3 })
    expect(accepted).toHaveLength(3)
    expect(errors).toEqual(['a3.jpg：最多 3 张，已忽略', 'a4.jpg：最多 3 张，已忽略'])
  })

  it('已有 5 张时只再接受上限内的部分', () => {
    const { accepted, errors } = validateStyleFiles([img('a.jpg'), img('b.jpg')], 5,
      { maxFiles: 6 })
    expect(accepted).toHaveLength(1)
    expect(errors).toHaveLength(1)
  })

  it('超大文件、总量超限与非图片文件逐张给出可读原因', () => {
    const big = new File(['x'.repeat(MAX_STYLE_FILE_MB * 1024 * 1024 + 1)], 'big.jpg',
      { type: 'image/jpeg' })
    const txt = new File(['z'], 'note.txt', { type: 'text/plain' })
    const { accepted, errors } = validateStyleFiles([big, txt, img('ok.jpg')], 0)
    expect(accepted.map(f => f.name)).toEqual(['ok.jpg'])
    expect(errors).toEqual([`big.jpg：超过 ${MAX_STYLE_FILE_MB}MB`, 'note.txt：不是图片格式'])
  })

  it('单个文件没超但整批超总量 → 后续文件被拦下（后端峰值 ≈400MB 的闸门）', () => {
    const oneMb = 1024 * 1024
    const { accepted, errors } = validateStyleFiles(
      [img('a.jpg', oneMb), img('b.jpg', oneMb)], 0,
      { maxTotalMb: 1 })
    expect(accepted.map(f => f.name)).toEqual(['a.jpg'])
    expect(errors).toEqual(['b.jpg：这一批总量会超过 1MB，已忽略'])
  })
})

describe('套图结构（逐张角色行）', () => {
  it('slotOptions 去重平台槽位并带"无法对应"空项', () => {
    const options = slotOptions([
      { slot_roles: [{ slot_id: 'main_white', label: '纯商品图' },
                     { slot_id: 'main_ingredients', label: '成分配方图' }] },
      { slot_roles: [{ slot_id: 'main_white', label: '纯商品图' },
                     { slot_id: 'note_cover', label: '笔记封面' }] },
    ])
    expect(options[0]).toEqual({ slot_id: '', label: '（无法对应）' })
    expect(options.map(o => o.slot_id)).toEqual(['', 'main_white', 'main_ingredients', 'note_cover'])
  })

  it('roleRows 按序号对齐照片，缺的留空', () => {
    const rows = roleRows(['p1', 'p2', 'p3'],
      [{ number: 2, slot: 'main_ingredients', treatment: '留白在上' }])
    expect(rows.map(r => r.number)).toEqual([1, 2, 3])
    expect(rows[1]).toMatchObject({ slot: 'main_ingredients', treatment: '留白在上', photo: 'p2' })
    expect(rows[0]).toMatchObject({ slot: '', treatment: '' })
  })

  it('rowsToRoles 去空行但**保留行号**（行号=照片序号，回填会让映射指错照片）', () => {
    const roles = rowsToRoles([
      { slot: 'main_white', treatment: '纯白无缝' },
      { slot: '', treatment: '' },
      { slot: 'main_audience', treatment: '上方留标题区' },
    ])
    expect(roles).toEqual([
      { number: 1, slot: 'main_white', treatment: '纯白无缝' },
      { number: 3, slot: 'main_audience', treatment: '上方留标题区' },
    ])
  })
})
