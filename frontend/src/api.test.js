import { describe, it, expect, vi, beforeEach } from 'vitest'
import {
  getStoredApiKey, setStoredApiKey, authHeaders, wsUrl,
  getSessions, saveApiKeys, saveTenantKey, getSettings,
} from './api'

function mockFetchOnce(status, body) {
  const res = {
    ok: status >= 200 && status < 300,
    status,
    json: vi.fn().mockResolvedValue(body),
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
