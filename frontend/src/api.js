const BASE = '/api'

async function request(path, options = {}) {
  const res = await fetch(`${BASE}${path}`, options)
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

  const res = await fetch(`${BASE}/sessions`, { method: 'POST', body: form })
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

export async function getAgents() {
  return request('/agents')
}

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

export async function instantiateWorkflow(template, formData) {
  const res = await fetch(`${BASE}/workflows/templates/${template}/instantiate`, {
    method: 'POST',
    body: formData,
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

export async function createBatch(body) {
  return request('/workflows/batches', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
}

export async function createBatchCsv(template, csvFile, mode = 'auto') {
  const fd = new FormData()
  fd.append('template_name', template)
  fd.append('mode', mode)
  fd.append('file', csvFile)
  const res = await fetch(`${BASE}/workflows/batches`, { method: 'POST', body: fd })
  if (!res.ok) {
    const body = await res.json().catch(() => ({}))
    throw new Error(body.detail || `创建失败 (${res.status})`)
  }
  return res.json()
}

export async function getBatches() {
  return request('/workflows/batches')
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
  return `${proto}//${location.host}${path}`
}
