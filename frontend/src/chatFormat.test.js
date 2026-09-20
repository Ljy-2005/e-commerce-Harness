import { describe, it, expect } from 'vitest'
import {
  msgPreview, displayContent, detailFields, hiddenKeys, safeSavedPath, rawContentText,
  summaryLine, REDACTED_IMAGE, REDACTED_URL,
} from './chatFormat'

/** 方舟 TOS 的真实形状：地址本身不长，但查询串带着凭据与签名 */
const SIGNED_URL = 'https://ark-acg-cn-beijing.tos-cn-beijing.volces.com/doubao/0217.jpeg'
  + '?X-Tos-Algorithm=TOS4-HMAC-SHA256&X-Tos-Credential=AKLTYWJkZTExNjA1ZDUyNDc3OA%2F20260919'
  + '&X-Tos-Signature=71831a8aa3e5162530f3cc3cc5b3f0bf'
const BLOB = 'A'.repeat(300)

describe('displayContent — 只给人看', () => {
  it('base64 图片数据被收掉（原对象不被修改）', () => {
    const content = { slot_id: 'main_white', base64_data: BLOB }
    const shown = displayContent(content)
    expect(shown.base64_data).toBe(REDACTED_IMAGE)
    expect(content.base64_data).toBe(BLOB)          // 原对象仍可用来渲染 <img>
    expect(shown.slot_id).toBe('main_white')        // 其余字段照常
  })

  it('签名地址只留"已隐藏签名"的说明', () => {
    const shown = displayContent({ image_url: SIGNED_URL, base_image_url: SIGNED_URL })
    expect(shown.image_url).toBe(REDACTED_URL)
    expect(shown.base_image_url).toBe(REDACTED_URL)
  })

  it('普通公开地址（没有签名）不动', () => {
    const shown = displayContent({ image_url: 'https://example.com/a.jpg' })
    expect(shown.image_url).toBe('https://example.com/a.jpg')
  })

  it('落盘路径只留文件名（不暴露租户/会话目录）', () => {
    expect(safeSavedPath('default/bb6cc0fa56a54921/pinduoduo_保健品_健康食品_main_white_1.jpg'))
      .toBe('pinduoduo_保健品_健康食品_main_white_1.jpg')
    expect(displayContent({ saved_path: 'a\\b\\c.jpg' }).saved_path).toBe('c.jpg')
  })

  it('嵌套数组/对象同样脱敏', () => {
    const shown = displayContent({ images: [{ image_url: SIGNED_URL }, { base64_data: BLOB }] })
    expect(shown.images[0].image_url).toBe(REDACTED_URL)
    expect(shown.images[1].base64_data).toBe(REDACTED_IMAGE)
  })

  it('提示词正文超长时截断并说明原文长度（技术详情里可看全文）', () => {
    const long = '画'.repeat(5000)
    const shown = displayContent({ prompt_text: long })
    expect(shown.prompt_text.length).toBeLessThan(long.length)
    expect(shown.prompt_text).toContain('原文共 5000 字')
    // 技术详情口径：promptCharLimit: 0 = 不截断
    expect(displayContent({ prompt_text: long }, { promptCharLimit: 0 }).prompt_text).toBe(long)
  })

  it('中文长文本不会被误判成图片数据', () => {
    const text = '这套图要干净明朗，留白给足'.repeat(10)
    expect(displayContent({ message: text }).message).toBe(text)
  })
})

describe('hiddenKeys — 隐藏了什么必须说得出来', () => {
  it('列出被隐藏的类别', () => {
    const keys = hiddenKeys({ base64_data: BLOB, image_url: SIGNED_URL, saved_path: 'a/b.jpg' })
    expect(keys).toContain('图片数据')
    expect(keys).toContain('图片签名地址')
    expect(keys).toContain('本地文件路径')
  })
  it('没有可隐藏内容时返回空数组', () => {
    expect(hiddenKeys({ message: '一切正常' })).toEqual([])
  })
})

describe('msgPreview / summaryLine — 绝不回落 JSON', () => {
  it('message 优先于结构字段', () => {
    expect(msgPreview({ quality_report: { count: 1 }, message: '出图完成，共 10 张' }))
      .toBe('出图完成，共 10 张')
  })
  it('没有 message 时给字段清单摘要', () => {
    const line = summaryLine({ quality_report: 1, set_plan_coverage: 2 })
    expect(line).toContain('本地体检')
    expect(line).toContain('套图覆盖度')
  })
  it('完全未知的字段也不会吐出 JSON', () => {
    const preview = msgPreview({ zzz: 1, yyy: 2 })
    expect(preview).not.toContain('{')
    expect(preview).toContain('zzz')
  })
})

describe('rawContentText — 技术详情口径一致', () => {
  it('仍是可读 JSON，但图片数据已被替换', () => {
    const text = rawContentText({ images: [{ base64_data: BLOB, slot_id: 'main_white' }] })
    expect(text).toContain('main_white')
    expect(text).toContain(REDACTED_IMAGE)
    expect(text).not.toContain(BLOB)
    expect(() => JSON.parse(text)).not.toThrow()
  })
})

describe('detailFields — 详情字段只看有用的', () => {
  it('摘掉内部实现参数（原始产物里仍有），保留能核对的字段', () => {
    const img = {
      slot_id: 'main_white', model_used: 'doubao-seedream-5-0',
      generation_params: { reference_bytes: 2648148, ignored_params: [] },
      saved_path: 'default/s1/a.jpg',
    }
    const fields = detailFields(img)
    expect(fields.slot_id).toBe('main_white')
    expect(fields.model_used).toBe('doubao-seedream-5-0')
    expect(fields.saved_path).toBe('a.jpg')
    expect(fields.generation_params).toBeUndefined()
    // 原始产物侧没被改动
    expect(img.generation_params.reference_bytes).toBe(2648148)
    expect(rawContentText(img)).toContain('reference_bytes')
  })
})
