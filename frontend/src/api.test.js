import { describe, it, expect, vi, beforeEach } from 'vitest'
import {
  getStoredApiKey, setStoredApiKey, authHeaders, wsUrl,
  getSessions, saveApiKeys, saveTenantKey, getSettings,
  getSession, submitDecision, exportTemplate,
  getStyleLibrary, createStyleEntry, updateStyleEntry, deleteStyleEntry,
  reanalyzeStyleEntry, previewStyleLibrary,
} from './api'

function mockFetchOnce(status, body) {
  const res = {
    ok: status >= 200 && status < 300,
    status,
    json: vi.fn().mockResolvedValue(body),
    text: vi.fn().mockResolvedValue(typeof body === 'string' ? body : JSON.stringify(body ?? {})),
  }
  global.fetch = vi.fn().mockResolvedValue(res)
  return res
}

beforeEach(() => {
  localStorage.clear()
  delete global.fetch
})

describe('localStorage API Key', () => {
  it('get/set 往返', () => {
    expect(getStoredApiKey()).toBe('')
    setStoredApiKey('sk-123')
    expect(getStoredApiKey()).toBe('sk-123')
    expect(localStorage.getItem('ecomm_api_key')).toBe('sk-123')
  })

  it('空值清除', () => {
    setStoredApiKey('sk-123')
    setStoredApiKey('')
    expect(getStoredApiKey()).toBe('')
    expect(localStorage.getItem('ecomm_api_key')).toBeNull()
  })
})

describe('authHeaders', () => {
  it('无 Key 时不附加', () => {
    expect(authHeaders({ a: 1 })).toEqual({ a: 1 })
  })

  it('有 Key 时附加 X-API-Key', () => {
    setStoredApiKey('sk-abc')
    expect(authHeaders()).toEqual({ 'X-API-Key': 'sk-abc' })
    expect(authHeaders({ b: 2 })).toEqual({ b: 2, 'X-API-Key': 'sk-abc' })
  })
})

describe('wsUrl', () => {
  it('无 Key 时无查询参数，ws 协议', () => {
    const url = wsUrl('/ws/sessions/s1')
    expect(url.startsWith('ws://')).toBe(true)
    expect(url).toContain('/ws/sessions/s1')
    expect(url).not.toContain('api_key')
  })

  it('有 Key 时带 api_key 参数', () => {
    setStoredApiKey('sk-key1')
    const url = wsUrl('/ws/sessions/s1')
    expect(url).toContain('api_key=sk-key1')
  })
})

describe('request 行为（经导出函数验证）', () => {
  it('成功响应返回 JSON', async () => {
    mockFetchOnce(200, { sessions: [] })
    const data = await getSessions()
    expect(data).toEqual({ sessions: [] })
    expect(global.fetch).toHaveBeenCalledWith('/api/sessions', expect.anything())
  })

  it('错误响应抛出 detail', async () => {
    mockFetchOnce(400, { detail: '未知租户' })
    await expect(getSessions()).rejects.toThrow('未知租户')
  })

  it('错误响应无 detail 时回退 error 字段', async () => {
    mockFetchOnce(401, { error: 'Invalid API Key' })
    await expect(getSettings()).rejects.toThrow('Invalid API Key')
  })

  it('网络异常向上抛', async () => {
    global.fetch = vi.fn().mockRejectedValue(new Error('NetworkError'))
    await expect(getSessions()).rejects.toThrow('NetworkError')
  })
})

describe('saveApiKeys / saveTenantKey 请求体', () => {
  it('saveApiKeys 提交 {"api_keys": {...}}', async () => {
    mockFetchOnce(200, {})
    await saveApiKeys({ OPENAI_API_KEY: 'sk-x' })
    const [url, opts] = global.fetch.mock.calls[0]
    expect(url).toBe('/api/settings/api-keys')
    expect(JSON.parse(opts.body)).toEqual({ api_keys: { OPENAI_API_KEY: 'sk-x' } })
    expect(opts.headers['Content-Type']).toBe('application/json')
  })

  it('saveTenantKey 提交 tenant_id + api_key', async () => {
    mockFetchOnce(200, {})
    await saveTenantKey('t1', 'key-abcdefgh12345678')
    const [url, opts] = global.fetch.mock.calls[0]
    expect(url).toBe('/api/settings/tenant-keys')
    expect(JSON.parse(opts.body)).toEqual({ tenant_id: 't1', api_key: 'key-abcdefgh12345678' })
  })
})

describe('鉴权头覆盖全部请求路径（第二轮审计修复）', () => {
  // 回归背景：这三处曾是裸 fetch，配 ECOMM_API_KEY 后会话详情页永久 401、HITL 决策与模板导出必失败
  it('getSession 携带 X-API-Key（且 404 → null）', async () => {
    setStoredApiKey('sk-abc')
    mockFetchOnce(200, { session_id: 's1' })
    await getSession('s1')
    const [url, opts] = global.fetch.mock.calls[0]
    expect(url).toBe('/api/sessions/s1')
    expect(opts.headers['X-API-Key']).toBe('sk-abc')

    mockFetchOnce(404, {})
    expect(await getSession('nope')).toBeNull()
  })

  it('submitDecision 携带 X-API-Key', async () => {
    setStoredApiKey('sk-abc')
    mockFetchOnce(200, { status: 'ok' })
    await submitDecision('s1', 'approve')
    const [url, opts] = global.fetch.mock.calls[0]
    expect(url).toBe('/api/sessions/s1/decision')
    expect(opts.method).toBe('POST')
    expect(opts.headers['X-API-Key']).toBe('sk-abc')
  })

  it('exportTemplate 携带 X-API-Key 并返回文本', async () => {
    setStoredApiKey('sk-abc')
    mockFetchOnce(200, 'name: 白底主图套装')
    const text = await exportTemplate('white_bg_suite')
    const [url, opts] = global.fetch.mock.calls[0]
    expect(url).toBe('/api/workflows/templates/white_bg_suite/export')
    expect(opts.headers['X-API-Key']).toBe('sk-abc')
    expect(text).toContain('白底主图套装')
  })
})

// 风格词库契约（后端 /style-library，字段与拼接方式已冻结）
describe('风格词库接口', () => {
  it('getStyleLibrary → GET /style-library', async () => {
    mockFetchOnce(200, { builtin: [], mine: [], stats: {} })
    await getStyleLibrary()
    expect(global.fetch.mock.calls[0][0]).toBe('/api/style-library')
  })

  it('createStyleEntry → multipart：files[] + name + applies_to(JSON 字符串) + as_anchor', async () => {
    mockFetchOnce(200, { entry: { id: 's1' }, estimate: { images: 1, calls: 1, amount: null } })
    const file = new File(['x'], 'a.jpg', { type: 'image/jpeg' })
    await createStyleEntry({
      files: [file], name: '冷调实验室风',
      appliesTo: { categories: ['保健品'], platforms: [], slots: [] }, asAnchor: true,
    })

    const [url, opts] = global.fetch.mock.calls[0]
    expect(url).toBe('/api/style-library')
    expect(opts.method).toBe('POST')
    expect(opts.body).toBeInstanceOf(FormData)
    expect(opts.body.getAll('files')).toHaveLength(1)
    expect(opts.body.get('name')).toBe('冷调实验室风')
    expect(opts.body.get('as_anchor')).toBe('true')
    expect(JSON.parse(opts.body.get('applies_to')))
      .toEqual({ categories: ['保健品'], platforms: [], slots: [] })
  })

  it('createStyleEntry 把后端可读错误原样抛出（409 未配置视觉模型）', async () => {
    mockFetchOnce(409, { detail: '未配置视觉模型：请先在设置页填写 VISION Provider 的 Key' })
    await expect(createStyleEntry({ files: [], name: 'x' }))
      .rejects.toThrow('未配置视觉模型')
  })

  it('reanalyzeStyleEntry → POST 带 hint（可为空串）', async () => {
    mockFetchOnce(200, { entry: { id: 's1' } })
    await reanalyzeStyleEntry('s1', '更冷一点')
    const [url, opts] = global.fetch.mock.calls[0]
    expect(url).toBe('/api/style-library/s1/reanalyze')
    expect(opts.method).toBe('POST')
    expect(JSON.parse(opts.body)).toEqual({ hint: '更冷一点' })

    mockFetchOnce(200, { entry: { id: 's1' } })
    await reanalyzeStyleEntry('s1')
    expect(JSON.parse(global.fetch.mock.calls[0][1].body)).toEqual({ hint: '' })
  })

  it('updateStyleEntry → PATCH 指定字段', async () => {
    mockFetchOnce(200, { entry: { id: 's1' }, removed: [], similar: [] })
    await updateStyleEntry('s1', { enabled: false })
    expect(global.fetch.mock.calls[0][0]).toBe('/api/style-library/s1')
    expect(global.fetch.mock.calls[0][1].method).toBe('PATCH')
  })

  it('deleteStyleEntry → DELETE 并回传 adopted', async () => {
    mockFetchOnce(200, { deleted: 's1', adopted: 3, message: '已删除' })
    expect(await deleteStyleEntry('s1')).toEqual({ deleted: 's1', adopted: 3, message: '已删除' })
    expect(global.fetch.mock.calls[0][1].method).toBe('DELETE')
  })

  it('previewStyleLibrary → 查询串按平台/品类/槽位/词条拼装，空值不带上', async () => {
    mockFetchOnce(200, { block: '## 适用风格档案' })
    await previewStyleLibrary({ platform: 'taobao', category: '保健品', slot: 'main_white', entryId: 's1' })
    expect(global.fetch.mock.calls[0][0])
      .toBe('/api/style-library/preview?platform=taobao&category=%E4%BF%9D%E5%81%A5%E5%93%81&slot=main_white&entry_id=s1')

    mockFetchOnce(200, { block: '' })
    await previewStyleLibrary()
    expect(global.fetch.mock.calls[0][0]).toBe('/api/style-library/preview')
  })

  it('风格词库请求同样携带 X-API-Key（鉴权后不 401）', async () => {
    setStoredApiKey('sk-abc')
    mockFetchOnce(200, { mine: [] })
    await getStyleLibrary()
    expect(global.fetch.mock.calls[0][1].headers['X-API-Key']).toBe('sk-abc')

    setStoredApiKey('sk-abc')
    mockFetchOnce(200, { entry: { id: 's1' } })
    await createStyleEntry({ files: [], name: 'x' })
    expect(global.fetch.mock.calls[0][1].headers['X-API-Key']).toBe('sk-abc')
  })
})
