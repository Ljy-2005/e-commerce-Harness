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

export function authHeaders(extra = {}) {
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
  // 审计修复（第二轮）：此前裸 fetch 漏发 X-API-Key —— 配置 ECOMM_API_KEY 后
  // 会话详情页（含 3s 轮询）永久 401
  const res = await fetch(`${BASE}/sessions/${sessionId}`, { headers: authHeaders() })
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
  // 审计修复（第二轮）：人工审批同样需要鉴权头，否则 HITL 按钮在启用鉴权后必失败
  const res = await fetch(`${BASE}/sessions/${sessionId}/decision`,
    { method: 'POST', body: form, headers: authHeaders() })
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

/** 保存单个 Provider 的端点与模型（第三方 coding plan / 代理 / 自建网关） */
export async function saveProviderConfig(route, payload) {
  return request(`/settings/providers/${encodeURIComponent(route)}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
}

/**
 * 测试连接（B3-24）：对该 Provider 发起 1 次最小调用，验证 Key / 端点 / 模型真的可用。
 * 返回 {ok, skipped, is_mock, model, latency_ms, base_url, detail, reason}；
 * 连接失败也是 200（结果里 ok=false + detail），由界面直接展示。
 */
export async function testProviderConnection(route, payload = {}) {
  return request(`/settings/providers/${encodeURIComponent(route)}/test`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
}

/** 二进制下载（图片/ZIP）：带鉴权头取回 blob（裸 <a href> 不会带 X-API-Key） */
async function requestBlob(path) {
  const res = await fetch(`${BASE}${path}`, { headers: authHeaders() })
  if (!res.ok) {
    let detail = `HTTP ${res.status}`
    try {
      const body = await res.json()
      if (body?.detail) detail = body.detail
    } catch { /* 非 JSON 错误体，保留状态码 */ }
    throw new Error(detail)
  }
  return res.blob()
}

/** 下载单张生成图（按序号，与落盘文件名对应） */
export async function downloadSessionImage(sessionId, index) {
  return requestBlob(`/sessions/${encodeURIComponent(sessionId)}/images/${index}/download`)
}

/** 导出该会话全部生成图（ZIP） */
export async function exportSessionImages(sessionId) {
  return requestBlob(`/sessions/${encodeURIComponent(sessionId)}/export`)
}

/** 设置生成图输出根目录（仅 admin；空字符串 = 回落默认 ./output） */
export async function saveOutputDir(dir) {
  return request('/settings/output', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ dir }),
  })
}

/** 保存会话策略（连续失败即停 / 最大轮次 / TTL；仅 admin） */
export async function saveChatSettings(payload) {
  return request('/settings/chat', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
}

/** 平台档案（config/platforms.yaml）—— 平台选择器与套图槽位的单一事实来源 */
export async function getPlatforms() {
  return request('/platforms')
}

/** 保存生图质量策略（文字策略/参考图模式/水印/体检阈值；仅 admin） */
export async function saveImageSettings(payload) {
  return request('/settings/image', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
}

/** 试生成一张（真调一次生图 + 本地体检；仅 admin） */
export async function probeImageGeneration(payload) {
  return request('/settings/image/probe', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
}

/** 价格表现状：已标定条目 + 你实际用过的模型（未标定的不会估算金额；仅 admin） */
export async function getPricing() {
  return request('/settings/pricing')
}

/** 写入单价（键 = 模型 id 或 路由/模型 id；仅 admin） */
export async function savePricing(prices) {
  return request('/settings/pricing', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ prices }),
  })
}

/** 标定：用"本次实际花费 + 本次用量"反推单价（仅 admin） */
export async function calibratePricing(payload) {
  return request('/settings/pricing/calibrate', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
}

/** 补充会话事实（成分/用法/规格/对比…）—— 信息图缺素材时用来恢复出图 */
export async function saveSessionFacts(sessionId, facts) {
  return request(`/sessions/${sessionId}/facts`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(facts),
  })
}

/** 一键添加用的服务商预设（智谱/Kimi/硅基流动/OpenRouter/方舟/Ollama…） */
export async function getProviderPresets() {
  return request('/settings/providers/presets')
}

/** 新增/覆盖一个自定义服务商（OpenAI 或 Anthropic 兼容），返回最新设置载荷 */
export async function addCustomProvider(payload) {
  return request('/settings/providers/custom', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
}

/** 删除一个自定义服务商（内置路由不可删） */
export async function deleteCustomProvider(route) {
  return request(`/settings/providers/custom/${encodeURIComponent(route)}`, { method: 'DELETE' })
}

/**
 * 把凭据从一个槽位迁移到另一个（实测场景：火山方舟 Key 被填进旧版签名卡片的 AccessKey 槽）。
 * 返回最新设置载荷 + `moved: {from_env, to_env}`；不回显密钥本身。
 */
export async function moveProviderCredential(fromEnv, toEnv) {
  return request('/settings/providers/move-credential', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ from_env: fromEnv, to_env: toEnv }),
  })
}

/**
 * 拉取该服务商账号实际可用的模型（OpenAI 兼容的 GET /models）。
 * 返回 {ok, base_url, total, models[], applicable{text,vision,image}, detail}。
 */
export async function pullProviderModels(route) {
  return request(`/settings/providers/${encodeURIComponent(route)}/models`)
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

// ── 风格词库 ──
// 契约（后端 /style-library）：
//   summary = {id,name,source,status,enabled,auto_disabled,summary,style_words,shot_flow,
//              shot_role_count,as_anchor,applies_to,error,adopted,usage{...},cover,photo_count}
//   usage.cost.amount 为 null = 价格未标定 —— 前端按"未标定"显示，**不得当 0**。
//   张数/体积口径来自 `/style-library` 的 `stats.limits`（前端**不硬编码**）：
//   max_photos / max_photo_mb / max_total_mb / vision_batch / max_entries_per_tenant

/** 词库总览：内置档案 + 我的词条 + 统计（含被丢弃的坏条目与 limits） */
export async function getStyleLibrary() {
  return request('/style-library')
}

/** 单个词条详情（含可编辑文本字段、逐张角色、导入的照片、已剔除项、相似档案） */
export async function getStyleEntry(id) {
  return request(`/style-library/${encodeURIComponent(id)}`)
}

/**
 * 新建词条：multipart 上传照片（张数上限见接口的 limits.max_photos，当前 20）
 * + 名称 + 适用范围 + 是否锚点。
 * 返回 {entry, estimate:{images,calls,batch,amount,currency,note}, message}；
 * estimate.amount 为 null 表示后端未标定价格（前端不显示金额）。
 * 超过单批（limits.vision_batch）时会**分批**：estimate.calls = 视觉调用次数。
 */
export async function createStyleEntry({ files = [], name = '', appliesTo = {}, asAnchor = true } = {}) {
  const form = new FormData()
  files.forEach(f => form.append('files', f))
  form.append('name', name)
  form.append('applies_to', JSON.stringify(appliesTo || {}))
  form.append('as_anchor', asAnchor ? 'true' : 'false')

  const res = await fetch(`${BASE}/style-library`, { method: 'POST', body: form, headers: authHeaders() })
  if (!res.ok) {
    const body = await res.json().catch(() => ({}))
    throw new Error(body.detail || body.error || `创建失败 (${res.status})`)
  }
  return res.json()
}

/** 给已有词条**追加照片**（只 append：序号不变，已识别的逐张角色仍然对得上） */
export async function addStylePhotos(id, files = []) {
  const form = new FormData()
  Array.from(files).forEach(f => form.append('files', f))
  const res = await fetch(`${BASE}/style-library/${encodeURIComponent(id)}/photos`,
    { method: 'POST', body: form, headers: authHeaders() })
  if (!res.ok) {
    const body = await res.json().catch(() => ({}))
    throw new Error(body.detail || body.error || `追加失败 (${res.status})`)
  }
  return res.json()
}

/** 移除第 N 张照片（1 起）。会重排序号 → 后端同时清空逐张角色并如实回报 */
export async function removeStylePhoto(id, index) {
  return request(`/style-library/${encodeURIComponent(id)}/photos/${index}`, { method: 'DELETE' })
}

/** 再分析（失败态重试 / 换个方向重跑）：可选自然语言提示，如"更冷一点" */
export async function reanalyzeStyleEntry(id, hint = '') {
  return request(`/style-library/${encodeURIComponent(id)}/reanalyze`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ hint: hint || '' }),
  })
}

/**
 * 编辑词条（只发改动过的字段）；返回 {entry, removed, similar, auto_disabled, message}。
 * `enabled: true` 会**自动停用其他风格词条**（一轮会话一套风格词），名单在 auto_disabled 里。
 */
export async function updateStyleEntry(id, patch) {
  return request(`/style-library/${encodeURIComponent(id)}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(patch || {}),
  })
}

/** 删除词条；返回 {deleted, adopted, message}（adopted = 曾被多少会话采用） */
export async function deleteStyleEntry(id) {
  return request(`/style-library/${encodeURIComponent(id)}`, { method: 'DELETE' })
}

/**
 * 切换**本次会话的风格词**（一轮会话一套风格词）：`entryId` 传空串 = 清除指定。
 * 会话一旦定下风格就锁在产物里，改词库的启用项**不影响**进行中的会话 —— 要换就调这里。
 */
export async function setSessionStyle(sessionId, entryId = '') {
  return request(`/sessions/${encodeURIComponent(sessionId)}/style`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ entry_id: entryId || '' }),
  })
}

/**
 * 零成本预览：看某平台/品类/槽位会注入哪一段风格档案（不调模型）。
 * 返回 {platform, policy, slots, block, notes, message}
 */
export async function previewStyleLibrary({ platform = '', category = '', slot = '', entryId = '' } = {}) {
  const params = new URLSearchParams()
  if (platform) params.set('platform', platform)
  if (category) params.set('category', category)
  if (slot) params.set('slot', slot)
  if (entryId) params.set('entry_id', entryId)
  const qs = params.toString()
  return request(`/style-library/preview${qs ? '?' + qs : ''}`)
}

// ── 工作流 ──

export async function getWorkflowTemplates() {
  return request('/workflows/templates')
}

export async function exportTemplate(name) {
  // 审计修复（第二轮）：模板导出走鉴权头（此前裸 fetch → 启用鉴权后必 401）
  const res = await fetch(`${BASE}/workflows/templates/${name}/export`,
    { headers: authHeaders() })
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
