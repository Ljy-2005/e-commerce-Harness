import { useState } from 'react'
import Modal from './Modal'
import { previewStyleLibrary } from '../api'

/**
 * 零成本预览：选平台 / 品类 / 槽位 → 显示后端**真实返回**的注入块。
 * 不调模型、不花钱；块内容原样展示，前端不做任何拼接。
 */
export default function StylePreview({ entryId = '', entryName = '', platforms = [], onClose }) {
  const [platform, setPlatform] = useState(() => platforms.find(p => p.is_default)?.slug || platforms[0]?.slug || '')
  const [category, setCategory] = useState('')
  const [slot, setSlot] = useState('')
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  const current = platforms.find(p => p.slug === platform)
  const slots = current?.slot_roles || []

  async function handlePreview() {
    setLoading(true)
    setError('')
    try {
      const result = await previewStyleLibrary({ platform, category, slot, entryId })
      setData(result)
    } catch (err) {
      setError(err.message || '预览失败')
      setData(null)
    }
    setLoading(false)
  }

  return (
    <Modal
      title={`预览注入块${entryName ? `：${entryName}` : ''}`}
      onClose={onClose}
      width={720}
      footer={<button className="btn btn-ghost" onClick={onClose}>关闭</button>}
    >
      <p className="text-xs mb-2">
        预览是零成本的：只读取档案并渲染注入文本，不会调用任何模型。
      </p>
      <div className="grid-3">
        <div className="form-group">
          <label className="label" htmlFor="style-preview-platform">平台</label>
          <select id="style-preview-platform" className="select" value={platform}
            onChange={e => { setPlatform(e.target.value); setSlot('') }}>
            {platforms.length === 0 && <option value="">（平台档案不可用）</option>}
            {platforms.map(p => <option key={p.slug} value={p.slug}>{p.label || p.slug}</option>)}
          </select>
        </div>
        <div className="form-group">
          <label className="label" htmlFor="style-preview-category">品类（可空）</label>
          <input id="style-preview-category" className="input" placeholder="如：保健品"
            value={category} onChange={e => setCategory(e.target.value)} />
        </div>
        <div className="form-group">
          <label className="label" htmlFor="style-preview-slot">槽位（可空）</label>
          <select id="style-preview-slot" className="select" value={slot} onChange={e => setSlot(e.target.value)}>
            <option value="">全部槽位</option>
            {slots.map(s => <option key={s.slot_id} value={s.slot_id}>{s.label || s.slot_id}</option>)}
          </select>
        </div>
      </div>

      <button className="btn btn-primary btn-sm" onClick={handlePreview} disabled={loading}>
        {loading ? '生成预览中…' : '生成预览'}
      </button>
      {error && <div className="alert alert-error mt-1">{error}</div>}

      {data && (
        <div className="mt-2">
          {data.message && <p className="text-sm mb-1">{data.message}</p>}
          <p className="text-xs mb-1">
            平台策略：<span className="strong">{data.policy || '—'}</span>
            {data.slots && Object.keys(data.slots).length > 0 && (
              <span>｜命中槽位：{Object.entries(data.slots)
                .map(([k, v]) => `${k}(${(v || []).length})`).join('、')}</span>
            )}
          </p>
          <p className="text-xs text-muted mb-1" data-testid="style-preview-hint">
            档案不会原样写进最终提示词（最终仍是六段式）。
          </p>
          <pre className="style-preview-block" data-testid="style-preview-block">{data.block || '（未命中任何档案）'}</pre>
          {(data.notes || []).length > 0 && (
            <div className="mt-1">
              {(data.notes || []).map((note, i) => <p key={i} className="text-xs">· {note}</p>)}
            </div>
          )}
        </div>
      )}
    </Modal>
  )
}
