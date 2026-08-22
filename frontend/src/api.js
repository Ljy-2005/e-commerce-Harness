const BASE = '/api'

// 前端持有的 API Key（设置页写入 localStorage；审计修复：此前前端从不发
// X-API-Key / WS 不带 ?api_key=，生产配置 ECOMM_API_KEY 后整站 401）
export function getStoredApiKey() {
  return localStorage.getItem('ecomm_api_key') || ''
}

export function setStoredApiKey(key) {
  if (key) localStorage.setItem('ecomm_api_key', key)
  else localStorage.removeItem('ecomm_api_key')
}

function authHeaders(extra = {}) {
  const key = getStoredApiKey()
  if (!key) return extra
  return { ...extra, 'X-API-Key': key }
}

async function request(path, options = {}) {
  const res = await fetch(`${BASE}${path}`, { ...options, headers: authHeaders(options.headers) })
  if (!res.ok) {
    const body = await res.json().catch(() => ({}))
    throw new Error(body.detail || body.error || `Request failed (${res.status})`)
  }
  return res.json()
}

// ── 会话 ──

export async function createSession(files, productInfo = '', platform = 'taobao', category = '', mode = 'serial') {
  const form = new FormData()
  files.forEach(f => form.append('files', f))
  form.append('product_info', productInfo)
  form.append('platform', platform)
  form.append('category_hint', category)
  form.append('mode', mode)

  const res = await fetch(`${BASE}/sessions`, { method: 'POST', body: form, headers: authHeaders() })
  if (!res.ok) {
    const body = await res.json().catch(() => ({}))
    throw new Error(body.detail || `Upload failed (${res.status})`)
  }
  return res.json()
}

export async function getSessions() {
  return request('/sessions')
}

export async function getSession(sessionId) {
  const res = await fetch(`${BASE}/sessions/${sessionId}`)
  if (res.status === 404) return null
  if (!res.ok) throw new Error(`Failed to fetch session (${res.status})`)
  return res.json()
}

export async function deleteSession(sessionId) {
  return request(`/sessions/${sessionId}`, { method: 'DELETE' })
}

export async function submitDecision(sessionId, action) {
  const form = new FormData()
  form.append('action', action)
  const res = await fetch(`${BASE}/sessions/${sessionId}/decision`, { method: 'POST', body: form })
  if (!res.ok) {
    const body = await res.json().catch(() => ({}))
    throw new Error(body.detail || `Decision failed (${res.status})`)
  }
  return res.json()
}

export async function interjectSession(sessionId, content) {
  return request(`/sessions/${sessionId}/interject`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ content }),
  })
}

export async function runABTest(sessionId, config) {
  return request(`/sessions/${sessionId}/ab-test`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(config),
  })
}

// ── 系统状态 ──

export async function getHealth() {
  const res = await fetch('/health')
  if (!res.ok) throw new Error(`Health check failed (${res.status})`)
  return res.json()
}

export async function getAdminStatus() {
  return request('/admin/status')
}

// ── Agent ──

// ── 设置 ──

export async function getSettings() {
  return request('/settings')
}

export async function saveApiKeys(apiKeys) {
  return request('/settings/api-keys', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ api_keys: apiKeys }),
  })
}

export async function saveTenantKey(tenantId, apiKey) {
  return request('/settings/tenant-keys', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ tenant_id: tenantId, api_key: apiKey }),
  })
}

export async function saveAgentParams(name, defaults) {
  return request(`/settings/agents/${encodeURIComponent(name)}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ defaults }),
  })
}

export async function saveModels(modelsConfig) {
  return request('/settings/models', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(modelsConfig),
  })
}

// ── 审计 / 记忆 ──

export async function getAudit({ sessionId = '', agent = '', date = '' } = {}) {
  const params = new URLSearchParams()
  if (sessionId) params.set('session_id', sessionId)
  if (agent) params.set('agent', agent)
  if (date) params.set('date', date)
  const qs = params.toString()
  return request(`/audit${qs ? '?' + qs : ''}`)
}

export async function getMemoryStats() {
  return request('/memory/stats')
}

export async function getMemoryRecall(category = '', limit = 5) {
  const params = new URLSearchParams()
  if (category) params.set('category', category)
  params.set('limit', String(limit))
  return request(`/memory/recall?${params.toString()}`)
}

// ── 工作流 ──

export async function getWorkflowTemplates() {
  return request('/workflows/templates')
}

export async function exportTemplate(name) {
  const res = await fetch(`${BASE}/workflows/templates/${name}/export`)
  if (!res.ok) throw new Error(`导出失败 (${res.status})`)
  return res.text()
}

export async function importTemplate(yaml, templateName, force = false) {
  return request('/workflows/templates/import', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ yaml, template_name: templateName, force }),
  })
}

export async function instantiateWorkflow(template, formData) {
  const res = await fetch(`${BASE}/workflows/templates/${template}/instantiate`, {
    method: 'POST',
    body: formData,
    headers: authHeaders(),
  })
  if (!res.ok) {
    const body = await res.json().catch(() => ({}))
    throw new Error(body.detail || `实例化失败 (${res.status})`)
  }
  return res.json()
}

export async function getWorkflowJobs() {
  return request('/workflows/jobs')
}

export async function getWorkflowJob(jobId) {
  return request(`/workflows/jobs/${jobId}`)
}

export async function controlWorkflow(jobId, action, stepNode = '') {
  return request(`/workflows/jobs/${jobId}/control`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ action, step_node: stepNode }),
  })
}

export async function decideWorkflow(jobId, action) {
  return request(`/workflows/jobs/${jobId}/decision`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ action }),
  })
}

export async function replicateStyle(jobId, file) {
  const fd = new FormData()
  fd.append('files', file)
  const res = await fetch(`${BASE}/workflows/jobs/${jobId}/replicate`, { method: 'POST', body: fd, headers: authHeaders() })
  if (!res.ok) {
    const body = await res.json().catch(() => ({}))
    throw new Error(body.detail || `风格复刻失败 (${res.status})`)
  }
  return res.json()
}

export async function createBatch(body) {
  return request('/workflows/batches', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
}

export async function createBatchCsv(template, csvFile, mode = 'auto', maxConcurrency = 3) {
  const fd = new FormData()
  fd.append('template_name', template)
  fd.append('mode', mode)
  fd.append('max_concurrency', String(maxConcurrency))
  fd.append('file', csvFile)
  const res = await fetch(`${BASE}/workflows/batches`, { method: 'POST', body: fd, headers: authHeaders() })
  if (!res.ok) {
    const body = await res.json().catch(() => ({}))
    throw new Error(body.detail || `创建失败 (${res.status})`)
  }
  return res.json()
}

export async function getBatches() {
  return request('/workflows/batches')
}

export async function getBatchReport() {
  return request('/workflows/batches/report')
}

export async function getBatch(batchId) {
  return request(`/workflows/batches/${batchId}`)
}

export async function controlBatch(batchId, action) {
  return request(`/workflows/batches/${batchId}/control`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ action }),
  })
}

// ── WebSocket ──

export function wsUrl(path) {
  const proto = location.protocol === 'https:' ? 'wss:' : 'ws:'
  const key = getStoredApiKey()
  const q = key ? `?api_key=${encodeURIComponent(key)}` : ''
  return `${proto}//${location.host}${path}${q}`
}
