const BASE = '/api'

export async function createSession(files, productInfo = '', platform = 'taobao', category = '') {
  const form = new FormData()
  files.forEach(f => form.append('files', f))
  form.append('product_info', productInfo)
  form.append('platform', platform)
  form.append('category_hint', category)

  const res = await fetch(`${BASE}/sessions`, { method: 'POST', body: form })
  if (!res.ok) throw new Error((await res.json()).detail || 'Upload failed')
  return res.json()
}

export async function getSession(sessionId) {
  const res = await fetch(`${BASE}/sessions/${sessionId}`)
  if (!res.ok) return null
  return res.json()
}

export async function getAgents() {
  const res = await fetch(`${BASE}/agents`)
  return res.json()
}

export async function deleteSession(sessionId) {
  await fetch(`${BASE}/sessions/${sessionId}`, { method: 'DELETE' })
}

export async function submitDecision(sessionId, action) {
  const form = new FormData()
  form.append('action', action)
  const res = await fetch(`${BASE}/sessions/${sessionId}/decision`, { method: 'POST', body: form })
  return res.json()
}

export function wsUrl(sessionId) {
  const proto = location.protocol === 'https:' ? 'wss:' : 'ws:'
  return `${proto}//${location.host}/ws/sessions/${sessionId}`
}
