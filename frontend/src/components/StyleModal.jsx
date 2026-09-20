import { useState, useEffect, useRef } from 'react'
import Modal from './Modal'
import {
  getStyleEntry, createStyleEntry, updateStyleEntry, reanalyzeStyleEntry,
  addStylePhotos, removeStylePhoto,
} from '../api'
import {
  validateStyleFiles, MAX_STYLE_FILES, MAX_STYLE_FILE_MB, MAX_STYLE_TOTAL_MB,
  slotOptions, roleRows, rowsToRoles,
} from '../styleFormat'

/**
 * 风格词条弹窗，三种模式：
 * - `create`：导入照片（拖拽 / 点选 / 粘贴）+ 命名 + 适用范围 → 「开始分析」
 * - `edit`：改所有文本字段（PATCH）、**逐张核对/修正套图结构**、追加或移除照片
 * - `reanalyze`：填一句方向提示（如"更冷一点"）重跑分析
 * 金额一律用后端 estimate，前端**不估算**：amount 为 null 就写"价格未标定"。
 * 张数/体积口径来自 `limits`（后端下发），前端不硬编码。
 */

// 文本字段表：type=text 单行；type=list 每行一条（数组）
const TEXT_FIELDS = [
  { key: 'summary', label: '风格摘要', type: 'text' },
  { key: 'style_words', label: '风格词', type: 'text' },
  { key: 'background', label: '背景', type: 'text' },
  { key: 'composition', label: '构图', type: 'text' },
  { key: 'lighting', label: '光影', type: 'text' },
  { key: 'materials', label: '材质', type: 'text' },
  { key: 'elements', label: '元素', type: 'list' },
  { key: 'whitespace', label: '留白', type: 'text' },
  { key: 'forbid', label: '禁忌', type: 'list' },
  { key: 'taste_verdict', label: '判词（审美锚点）', type: 'text' },
  { key: 'reward_points', label: '要点（要有）', type: 'list' },
  { key: 'avoid_points', label: '避免项', type: 'list' },
]

const POLICY_OPTIONS = [
  { value: 'design_allowed', label: '允许设计底' },
  { value: 'white_preferred', label: '白底优先' },
  { value: 'white_required', label: '必须纯白' },
]
const KIND_OPTIONS = [
  { value: 'photo', label: '纯摄影（模型直接出成品）' },
  { value: 'info', label: '信息图底图（文字由系统排版）' },
]

function asList(value) {
  if (Array.isArray(value)) return value.filter(v => v != null && v !== '')
  if (typeof value === 'string' && value.trim()) return [value.trim()]
  return []
}

function asCsList(value) {
  return asList(value).map(v => String(v).trim()).filter(Boolean)
}

function listToText(value) {
  return asList(value).join('\n')
}

function textToList(text) {
  return String(text || '').split('\n').map(s => s.trim()).filter(Boolean)
}

function csToText(value) {
  return asCsList(value).join('，')
}

function textToCs(text) {
  return String(text || '').split(/[,，、]/).map(s => s.trim()).filter(Boolean)
}

function buildInitialForm(entry) {
  const applies = entry?.applies_to || {}
  return {
    name: entry?.name || '',
    categories: csToText(applies.categories),
    platforms: csToText(applies.platforms),
    slots: csToText(applies.slots),
    not_slots: csToText(applies.not_slots),
    kinds: asList(applies.kinds),
    requires_policy: asList(applies.requires_policy),
    summary: entry?.summary || '',
    style_words: entry?.style_words || '',
    background: entry?.background || '',
    composition: entry?.composition || '',
    lighting: entry?.lighting || '',
    materials: entry?.materials || '',
    elements: listToText(entry?.elements),
    whitespace: entry?.whitespace || '',
    forbid: listToText(entry?.forbid),
    shot_flow: entry?.shot_flow || '',
    taste_verdict: entry?.taste_verdict || '',
    reward_points: listToText(entry?.reward_points),
    avoid_points: listToText(entry?.avoid_points),
    as_anchor: entry?.as_anchor !== false,
  }
}

const CREATE_FORM = { ...buildInitialForm(null), as_anchor: true }

/** 只把真正改动过的字段发给后端（PATCH） */
export function buildPatch(original, form, roles = null) {
  const base = buildInitialForm(original)
  const appliesKeys = ['categories', 'platforms', 'slots', 'not_slots']
  const patch = {}
  for (const key of Object.keys(form)) {
    if (appliesKeys.includes(key) || key === 'kinds' || key === 'requires_policy') continue
    if (key === 'as_anchor') {
      if (Boolean(form.as_anchor) !== Boolean(base.as_anchor)) patch.as_anchor = Boolean(form.as_anchor)
      continue
    }
    if (String(form[key] ?? '') !== String(base[key] ?? '')) {
      patch[key] = TEXT_FIELDS.find(f => f.key === key)?.type === 'list'
        ? textToList(form[key])
        : form[key]
    }
  }
  const applies = {}
  for (const key of appliesKeys) {
    if (String(form[key] ?? '') !== String(base[key] ?? '')) applies[key] = textToCs(form[key])
  }
  if (JSON.stringify(asList(form.kinds)) !== JSON.stringify(asList(base.kinds))) {
    applies.kinds = asList(form.kinds)
  }
  if (JSON.stringify(asList(form.requires_policy)) !== JSON.stringify(asList(base.requires_policy))) {
    applies.requires_policy = asList(form.requires_policy)
  }
  if (Object.keys(applies).length) patch.applies_to = applies
  if (roles) {
    const before = JSON.stringify((original?.shot_roles || []).map(item => [item.slot, item.treatment]))
    const after = JSON.stringify(rowsToRoles(roles).map(item => [item.slot, item.treatment]))
    if (before !== after) patch.shot_roles = rowsToRoles(roles)
  }
  return patch
}

function toggle(list, value) {
  const items = asList(list)
  return items.includes(value) ? items.filter(item => item !== value) : [...items, value]
}

export default function StyleModal({ mode = 'create', entry = null, platforms = [], mockMode = false,
                                     limits = null, onClose, onDone }) {
  const editing = mode === 'edit'
  const reanalyzing = mode === 'reanalyze'
  const maxFiles = Number(limits?.max_photos) > 0 ? Number(limits.max_photos) : MAX_STYLE_FILES
  const maxFileMb = Number(limits?.max_photo_mb) > 0 ? Number(limits.max_photo_mb) : MAX_STYLE_FILE_MB
  const maxTotalMb = Number(limits?.max_total_mb) > 0 ? Number(limits.max_total_mb) : MAX_STYLE_TOTAL_MB
  const batch = Number(limits?.vision_batch) > 0 ? Number(limits.vision_batch) : 12

  const [form, setForm] = useState(() => (editing ? buildInitialForm(null) : CREATE_FORM))
  const [original, setOriginal] = useState(null)
  const [files, setFiles] = useState([])          // [{file, url}]
  const [fileErrors, setFileErrors] = useState([])
  const [dragging, setDragging] = useState(false)
  const [loading, setLoading] = useState(editing)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [hint, setHint] = useState('')
  const [removed, setRemoved] = useState([])
  const [similar, setSimilar] = useState([])
  const [photos, setPhotos] = useState([])        // 详情里的照片（data URI，按序号）
  const [roles, setRoles] = useState([])          // 逐张角色行
  const inputRef = useRef(null)
  const addRef = useRef(null)

  const entryId = entry?.id || ''
  const entryRef = useRef(entry)
  entryRef.current = entry

  // 编辑态需要详情里的文本字段（列表 summary 里没有）
  useEffect(() => {
    if (!editing) return
    let alive = true
    const target = entryRef.current
    setLoading(true)
    setForm(buildInitialForm(target))
    setOriginal(target)
    getStyleEntry(target.id)
      .then(data => {
        if (!alive) return
        const detail = data?.entry && typeof data.entry === 'object' ? data.entry : data
        setOriginal(detail)
        setForm(buildInitialForm(detail))
        setRemoved(detail?.removed || [])
        setPhotos(detail?.photos || [])
        setRoles(roleRows(detail?.photos || [], detail?.shot_roles || []))
        setSimilar((detail?.similar || []).map(s => (
          typeof s === 'string' ? { id: s, name: s, similarity: 0 } : s
        )))
      })
      .catch(err => { if (alive) setError(err.message || '加载词条详情失败') })
      .finally(() => { if (alive) setLoading(false) })
    return () => { alive = false }
  }, [editing, entryId])

  function set(key, value) {
    setForm(prev => ({ ...prev, [key]: value }))
  }

  function setRole(index, patch) {
    setRoles(prev => prev.map((row, i) => (i === index ? { ...row, ...patch } : row)))
  }

  function addFiles(incoming) {
    const { accepted, errors } = validateStyleFiles(incoming, files.length, {
      maxFiles, maxFileMb, maxTotalMb,
      existingBytes: files.reduce((sum, item) => sum + (item.file?.size || 0), 0),
    })
    setFileErrors(errors)
    if (!accepted.length) return
    setFiles(prev => [...prev, ...accepted.map(file => ({ file, url: URL.createObjectURL(file) }))])
  }

  function removeFile(index) {
    setFiles(prev => {
      const next = [...prev]
      const [dropped] = next.splice(index, 1)
      if (dropped?.url) URL.revokeObjectURL(dropped.url)
      return next
    })
  }

  async function handleAddPhotos(incoming) {
    const { accepted, errors } = validateStyleFiles(incoming, photos.length, { maxFiles, maxFileMb, maxTotalMb })
    setFileErrors(errors)
    if (!accepted.length) return
    setBusy(true)
    setError('')
    try {
      const res = await addStylePhotos(entryId, accepted.map(item => item.file))
      setNotice(res?.message || '已追加照片')
      const detail = await getStyleEntry(entryId)
      const entryDetail = detail?.entry && typeof detail.entry === 'object' ? detail.entry : detail
      setPhotos(entryDetail?.photos || [])
      setRoles(roleRows(entryDetail?.photos || [], entryDetail?.shot_roles || []))
      setOriginal(entryDetail)
      setForm(buildInitialForm(entryDetail))
    } catch (err) {
      setError(err.message || '追加照片失败')
    }
    setBusy(false)
  }

  async function handleRemovePhoto(index) {
    if (busy) return
    if (!window.confirm(`移除第 ${index} 张照片？照片序号会重排，已识别的逐张角色会被清空（共同美术保留）。`)) return
    setBusy(true)
    setError('')
    try {
      const res = await removeStylePhoto(entryId, index)
      setNotice(res?.message || '已移除照片')
      const detail = await getStyleEntry(entryId)
      const entryDetail = detail?.entry && typeof detail.entry === 'object' ? detail.entry : detail
      setPhotos(entryDetail?.photos || [])
      setRoles(roleRows(entryDetail?.photos || [], entryDetail?.shot_roles || []))
      setOriginal(entryDetail)
    } catch (err) {
      setError(err.message || '移除照片失败')
    }
    setBusy(false)
  }

  function handleDrop(e) {
    e.preventDefault()
    setDragging(false)
    addFiles(e.dataTransfer?.files)
  }

  function handlePaste(e) {
    const items = e.clipboardData?.items || []
    const pasted = []
    for (const item of items) {
      if ((item.type || '').startsWith('image/')) {
        const file = item.getAsFile?.()
        if (file) pasted.push(file)
      }
    }
    if (pasted.length) {
      e.preventDefault()
      addFiles(pasted)
    }
  }

  async function handleSubmit() {
    if (busy) return
    setError('')
    if (reanalyzing) {
      setBusy(true)
      try {
        await reanalyzeStyleEntry(entryId, hint)
        onDone?.()
      } catch (err) {
        setError(err.message || '重新分析失败')
        setBusy(false)
      }
      return
    }
    if (editing) {
      const patch = buildPatch(original, form, roles)
      if (!Object.keys(patch).length) {
        setError('没有检测到改动')
        return
      }
      setBusy(true)
      try {
        const res = await updateStyleEntry(entryId, patch)
        const stripped = res?.removed || []
        setRemoved(stripped)
        setSimilar(res?.similar || [])
        setOriginal({ ...(original || {}), ...patch })
        setBusy(false)
        if (!stripped.length) onDone?.()
      } catch (err) {
        setError(err.message || '保存失败')
        setBusy(false)
      }
      return
    }
    // create
    if (!files.length) {
      setError('请至少导入 1 张照片')
      return
    }
    if (!form.name.trim()) {
      setError('请填写风格名称')
      return
    }
    setBusy(true)
    try {
      await createStyleEntry({
        files: files.map(f => f.file),
        name: form.name.trim(),
        appliesTo: {
          categories: textToCs(form.categories),
          platforms: textToCs(form.platforms),
          slots: textToCs(form.slots),
          not_slots: textToCs(form.not_slots),
          kinds: asList(form.kinds),
          requires_policy: asList(form.requires_policy),
        },
        asAnchor: form.as_anchor,
      })
      files.forEach(f => f.url && URL.revokeObjectURL(f.url))
      onDone?.()
    } catch (err) {
      setError(err.message || '创建失败')
      setBusy(false)
    }
  }

  const calls = files.length ? Math.ceil(files.length / batch) : 0
  const analyzed = Number(original?.usage?.images) || 0
  const slots = slotOptions(platforms)

  const title = reanalyzing
    ? `再分析：${entry?.name || ''}`
    : editing ? `编辑风格词条：${entry?.name || ''}` : '新建风格词条'

  return (
    <Modal
      title={title}
      onClose={onClose}
      width={760}
      footer={(
        <>
          {!reanalyzing && !editing && (
            <span className="style-estimate" data-testid="style-estimate">
              将送 {files.length} 张图做 {calls} 次视觉分析
              {calls > 1 && <span className="text-muted">（超过单批 {batch} 张 → 分批，多次计费）</span>}
              {mockMode && <span className="text-muted">（Mock 模式：$0，不走网络）</span>}
            </span>
          )}
          <div className="style-modal-buttons">
            <button className="btn btn-ghost" onClick={onClose} disabled={busy}>取消</button>
            <button className="btn btn-primary" onClick={handleSubmit} disabled={busy || loading}>
              {busy ? '提交中…' : reanalyzing ? '开始再分析' : editing ? '保存修改' : '开始分析'}
            </button>
          </div>
        </>
      )}
    >
      {loading && <div className="style-card-progress"><div className="spinner" /><p className="text-sm mt-1">正在读取词条详情…</p></div>}

      {!loading && (
        <div onPaste={handlePaste} data-testid="style-modal-body">
          {error && <div className="alert alert-error" role="alert">{error}</div>}
          {notice && <div className="alert alert-info" data-testid="style-notice">{notice}</div>}

          {removed.length > 0 && (
            <div className="alert alert-warn" data-testid="style-removed">
              已剔除 {removed.length} 处：{removed.slice(0, 6).map(r => String(r)).join('；')}
            </div>
          )}
          {similar.length > 0 && (
            <div className="alert alert-info" data-testid="style-similar">
              相似档案提示：
              {similar.map(s => `${s.name}（${Math.round((s.similarity || 0) * 100)}%）`).join('、')}
              —— 可考虑合并或改个更具体的名字。
            </div>
          )}

          {reanalyzing ? (
            <div>
              <p className="text-sm mb-2">
                可给一句方向提示，风格分析 Agent 会重新看这组照片。
                {entry?.photos?.length ? `（本词条已存 ${entry.photos.length} 张照片）` : ''}
              </p>
              <div className="form-group">
                <label className="label" htmlFor="style-hint">方向提示（可空）</label>
                <input id="style-hint" className="input" placeholder="如：更冷一点"
                  value={hint} onChange={e => setHint(e.target.value)} />
              </div>
            </div>
          ) : (
            <>
              {!editing && (
                <>
                  {/* 导入照片：拖拽 / 点选 / 粘贴 */}
                  <div
                    className={`drop-zone ${dragging ? 'active' : ''}`}
                    data-testid="style-drop-zone"
                    onClick={() => inputRef.current?.click()}
                    onDragOver={e => { e.preventDefault(); setDragging(true) }}
                    onDragLeave={() => setDragging(false)}
                    onDrop={handleDrop}
                  >
                    <p className="text-lg" aria-hidden>🖼️</p>
                    <p className="text-sm">拖拽照片到这里，或点击选择（也可直接粘贴截图）</p>
                    <p className="text-xs text-muted">
                      最多 {maxFiles} 张，每张不超过 {maxFileMb}MB，一次总量不超过 {maxTotalMb}MB
                      （超过 {batch} 张会分 {Math.ceil(maxFiles / batch)} 次调用分析）
                    </p>
                    <input
                      ref={inputRef}
                      type="file"
                      accept="image/*"
                      multiple
                      aria-label="导入风格照片"
                      style={{ display: 'none' }}
                      onChange={e => { addFiles(e.target.files); e.target.value = '' }}
                    />
                  </div>

                  {fileErrors.length > 0 && (
                    <div className="alert alert-error mt-1" data-testid="style-file-errors">
                      {fileErrors.map((msg, i) => <div key={i}>{msg}</div>)}
                    </div>
                  )}

                  {files.length > 0 && (
                    <div className="style-thumbs" data-testid="style-thumbs">
                      {files.map((item, i) => (
                        <div key={`${item.file.name}-${i}`} className="style-thumb">
                          <img src={item.url} alt={item.file.name} />
                          <button className="style-thumb-remove" aria-label={`移除照片 ${i + 1}`}
                            onClick={() => removeFile(i)}>✕</button>
                        </div>
                      ))}
                    </div>
                  )}
                </>
              )}

              <div className="form-group">
                <label className="label" htmlFor="style-name">名称（必填）</label>
                <input id="style-name" className="input" placeholder="如：冷调实验室风"
                  value={form.name} onChange={e => set('name', e.target.value)} />
              </div>

              <div className="grid-3">
                <div className="form-group">
                  <label className="label" htmlFor="style-categories">适用范围·品类关键词</label>
                  <input id="style-categories" className="input" placeholder="逗号分隔，如：保健品，护肤"
                    value={form.categories} onChange={e => set('categories', e.target.value)} />
                </div>
                <div className="form-group">
                  <label className="label" htmlFor="style-platforms">适用范围·平台</label>
                  <input id="style-platforms" className="input" placeholder="逗号分隔，留空=全平台"
                    value={form.platforms} onChange={e => set('platforms', e.target.value)} />
                </div>
                <div className="form-group">
                  <label className="label" htmlFor="style-slots">适用范围·槽位（可空）</label>
                  <input id="style-slots" className="input" placeholder="逗号分隔，如：main_white"
                    value={form.slots} onChange={e => set('slots', e.target.value)} />
                </div>
              </div>

              {/* 产出方式 / 背景策略 / 排除槽位：此前**没有入口**，而"只在允许设计底的平台生效"
                  正是避免"浅粉渐层 vs Amazon 首图必须纯白"这类冲突的正确开关 */}
              <div className="grid-3">
                <div className="form-group">
                  <span className="label">产出方式（留空=都适用）</span>
                  {KIND_OPTIONS.map(option => (
                    <label key={option.value} className="style-switch" htmlFor={`style-kind-${option.value}`}>
                      <input id={`style-kind-${option.value}`} type="checkbox"
                        aria-label={`产出方式 ${option.value}`}
                        checked={asList(form.kinds).includes(option.value)}
                        onChange={() => set('kinds', toggle(form.kinds, option.value))} />
                      <span className="text-sm">{option.label}</span>
                    </label>
                  ))}
                </div>
                <div className="form-group">
                  <span className="label">只在这些背景策略的平台用</span>
                  {POLICY_OPTIONS.map(option => (
                    <label key={option.value} className="style-switch" htmlFor={`style-policy-${option.value}`}>
                      <input id={`style-policy-${option.value}`} type="checkbox"
                        aria-label={`背景策略 ${option.value}`}
                        checked={asList(form.requires_policy).includes(option.value)}
                        onChange={() => set('requires_policy', toggle(form.requires_policy, option.value))} />
                      <span className="text-sm">{option.label}</span>
                    </label>
                  ))}
                </div>
                <div className="form-group">
                  <label className="label" htmlFor="style-not-slots">排除槽位（可空）</label>
                  <input id="style-not-slots" className="input" placeholder="逗号分隔，如：main_white"
                    value={form.not_slots} onChange={e => set('not_slots', e.target.value)} />
                </div>
              </div>
              {platforms.length > 0 && (
                <p className="text-xs text-muted mb-2">
                  平台档案可用：{platforms.map(p => `${p.label || p.slug}（${p.slug}）`).join('、')}
                </p>
              )}

              <label className="style-switch" htmlFor="style-anchor">
                <input id="style-anchor" type="checkbox" aria-label="作为审美锚点"
                  checked={form.as_anchor} onChange={e => set('as_anchor', e.target.checked)} />
                <span>作为审美锚点（审核打分以此为准绳，建议开）</span>
              </label>

              {editing && (
                <div className="mt-2">
                  <p className="card-section-title">套图结构（你给的这一套是怎么排的）</p>
                  <p className="text-xs text-muted mb-2" data-testid="style-sequence-hint">
                    逐张核对：这张在讲什么角色、与共同美术不同的地方在哪里。识别错了直接改；
                    <b>一轮会话只用这一套风格</b>，所以这里的角色对齐会决定每一张怎么写。
                  </p>
                  <div className="form-group">
                    <label className="label" htmlFor="style-shot-flow">叙事顺序（一句话）</label>
                    <input id="style-shot-flow" className="input"
                      placeholder="如：先白底立信任；再讲成分配方图"
                      value={form.shot_flow} onChange={e => set('shot_flow', e.target.value)} />
                  </div>
                  {roles.length === 0 && (
                    <p className="text-xs text-muted">还没有识别出逐张角色（分析完成后会出现）。</p>
                  )}
                  {roles.map((row, index) => (
                    <div className="style-role-row" key={row.number} data-testid={`style-role-${row.number}`}>
                      <span className="style-role-index">第{row.number}张</span>
                      {row.photo
                        ? <img className="style-role-thumb" src={row.photo} alt={`第${row.number}张`} />
                        : <span className="style-role-thumb style-role-thumb-empty" aria-hidden>?</span>}
                      <select className="select" aria-label={`第${row.number}张的角色`}
                        value={row.slot} onChange={e => setRole(index, { slot: e.target.value })}>
                        {slots.map(option => (
                          <option key={option.slot_id || 'none'} value={option.slot_id}>{option.label}</option>
                        ))}
                      </select>
                      <input className="input" aria-label={`第${row.number}张的做法`}
                        placeholder="这一张与共同美术不同的地方"
                        value={row.treatment} onChange={e => setRole(index, { treatment: e.target.value })} />
                      <button className="btn btn-ghost btn-sm" disabled={busy}
                        onClick={() => handleRemovePhoto(row.number)}>移除</button>
                    </div>
                  ))}
                  <div className="style-role-actions">
                    <button className="btn btn-ghost btn-sm" disabled={busy}
                      onClick={() => addRef.current?.click()}>＋ 追加照片</button>
                    <input ref={addRef} type="file" accept="image/*" multiple
                      aria-label="追加照片" style={{ display: 'none' }}
                      onChange={e => { handleAddPhotos(e.target.files); e.target.value = '' }} />
                    <span className="text-xs text-muted">
                      已存 {photos.length} 张{analyzed ? `／已分析 ${analyzed} 张` : ''}
                      {analyzed && analyzed < photos.length
                        ? '（照片多了，点卡片上的「再分析」让档案跟上）' : ''}
                    </span>
                  </div>
                  {fileErrors.length > 0 && (
                    <div className="alert alert-error mt-1" data-testid="style-file-errors">
                      {fileErrors.map((msg, i) => <div key={i}>{msg}</div>)}
                    </div>
                  )}

                  <p className="card-section-title mt-2">风格档案字段</p>
                  {TEXT_FIELDS.map(field => (
                    <div className="form-group" key={field.key}>
                      <label className="label" htmlFor={`style-field-${field.key}`}>{field.label}</label>
                      {field.type === 'list' ? (
                        <textarea id={`style-field-${field.key}`} className="textarea" rows={3}
                          placeholder="每行一条"
                          value={form[field.key]} onChange={e => set(field.key, e.target.value)} />
                      ) : (
                        <input id={`style-field-${field.key}`} className="input"
                          value={form[field.key]} onChange={e => set(field.key, e.target.value)} />
                      )}
                    </div>
                  ))}
                </div>
              )}

              <p className="text-xs text-muted">
                导入的照片只用于风格分析，不会成为生图参考图；完成后卡片会显示用量与模型。
              </p>
            </>
          )}
        </div>
      )}
    </Modal>
  )
}
