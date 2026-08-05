const BASE = '/api'

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

export async function getSession(sessionId) {
  const res = await fetch(`${BASE}/sessions/${sessionId}`)
  if (!res.ok) {
    if (res.status === 404) return null
    throw new Error(`Failed to fetch session (${res.status})`)
  }
  return res.json()
}

export async function getAgents() {
  const res = await fetch(`${BASE}/agents`)
  if (!res.ok) throw new Error(`Failed to fetch agents (${res.status})`)
  return res.json()
}

export async function deleteSession(sessionId) {
  const res = await fetch(`${BASE}/sessions/${sessionId}`, { method: 'DELETE' })
  if (!res.ok) throw new Error(`Failed to delete session (${res.status})`)
  return res.json()
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

export function wsUrl(path) {
  const proto = location.protocol === 'https:' ? 'wss:' : 'ws:'
  return `${proto}//${location.host}${path}`
}
