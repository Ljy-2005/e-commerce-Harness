import { useState, useEffect, useCallback } from 'react'
import { useNavigate, Link } from 'react-router-dom'
import { createSession, getSessions, deleteSession, getSettings, getPlatforms } from '../api'
import { formatCost, costTitle, sessionCostMeta } from '../cost'
import { StatusBadge } from './Dashboard'

const MODES = [
  { value: 'serial', label: '串行工作流', desc: '标准流程：分析→提示词→生图→审查→合规' },
  { value: 'ab_generate', label: 'A/B 生成', desc: '两套风格图片对比选优' },
  { value: 'ab_test', label: 'A/B 测试', desc: '多模型提示词对比 + 多审查员评分' },
  { value: 'debate', label: '辩论模式', desc: '正反双方审查辩论' },
  { value: 'vote', label: '投票模式', desc: '3 位审查员独立投票' },
]

const CAP_LABEL = { text: '文本', vision: '视觉', image: '生图' }

/** 能力生效行（B3-26）：把"实际用哪个 Provider/模型、是否回落 Mock"摊开给用户看 */
export function capabilitySummary(capabilities) {
  return (capabilities || []).map(c => ({
    ...c,
    label: CAP_LABEL[c.capability] || c.capability,
    text: c.is_mock
      ? `${CAP_LABEL[c.capability] || c.capability}：Mock（回落）`
      : `${CAP_LABEL[c.capability] || c.capability}：${c.provider}${c.model ? '/' + c.model : ''}`,
  }))
}

export default function Sessions() {
  const navigate = useNavigate()
  const [sessions, setSessions] = useState([])
  const [listError, setListError] = useState('')
  const [files, setFiles] = useState([])
  const [platform, setPlatform] = useState('taobao')
  const [productInfo, setProductInfo] = useState('')
  const [category, setCategory] = useState('')
  const [mode, setMode] = useState('serial')
  const [submitting, setSubmitting] = useState(false)
  const [createError, setCreateError] = useState('')
  const [settings, setSettings] = useState(null)
  // 平台清单来自后端档案（config/platforms.yaml）：新增平台（如拼多多）无需改前端
  const [platforms, setPlatforms] = useState([{ slug: 'taobao', label: '淘宝', slot_count: 0 }])

  const refresh = useCallback(async () => {
    try {
      const data = await getSessions()
      setSessions(data?.sessions || [])
      setListError('')
    } catch (err) {
      setListError(err.message || '加载会话列表失败')
    }
  }, [])

  useEffect(() => {
    // 能力解析（B3-26）：让用户在建任务前就看到"生图会不会回落 Mock"
    getSettings().then(setSettings).catch(() => {})
    // 平台档案：选择器与"套图几张/什么规范"都由后端配置驱动
    getPlatforms()
      .then(data => {
        const list = data?.platforms || []
        if (list.length) {
          setPlatforms(list)
          const preferred = list.find(p => p.is_default) || list[0]
          setPlatform(current => (list.some(p => p.slug === current) ? current : preferred.slug))
        }
      })
      .catch(() => {})
  }, [])

  useEffect(() => {
    refresh()
    const t = setInterval(refresh, 3000)
    return () => clearInterval(t)
  }, [refresh])

  const currentPlatform = platforms.find(p => p.slug === platform)

  function handleDrop(e) {
    e.preventDefault()
    const dropped = Array.from(e.dataTransfer.files).filter(f => f.type.startsWith('image/'))
    setFiles(prev => [...prev, ...dropped].slice(0, 10))
  }

  async function handleCreate(e) {
    e.preventDefault()
    if (!files.length) {
      setCreateError('请至少选择 1 张图片')
      return
    }
    setSubmitting(true)
    setCreateError('')
    try {
      const result = await createSession(files, productInfo, platform, category, mode)
      navigate(`/session/${result.session_id}`)
    } catch (err) {
      setCreateError(err.message || '创建失败')
      setSubmitting(false)
    }
  }

  async function handleDelete(sessionId, e) {
    e.preventDefault()
    e.stopPropagation()
    if (!confirm(`确定删除会话 ${sessionId.slice(0, 8)}… 及其全部产出物？`)) return
    try {
      await deleteSession(sessionId)
      refresh()
    } catch (err) {
      alert('删除失败: ' + (err.message || '未知错误'))
    }
  }

  return (
    <div>
      <h1 className="page-title">会话任务</h1>
      <p className="page-sub">创建商品图生成任务，并实时查看群聊工作流</p>

      <div className="grid-2">
        {/* 新建任务 */}
        <form onSubmit={handleCreate} className="card">
          <h3 className="mb-1">新建商品图任务</h3>

          {/* 能力落点（B3-26）：生图回落 Mock 时用户以为在出真图，跑完才发现是占位图 */}
          {settings && (
            <div className="cap-strip mb-2">
              {capabilitySummary(settings.capabilities).map(c => (
                <span key={c.capability} className={`chip ${c.is_mock ? 'chip-warn' : 'chip-ok'}`}>
                  {c.text}
                </span>
              ))}
              {settings.capabilities?.find(c => c.capability === 'image')?.is_mock && (
                <div className="alert alert-warn mt-1" role="status">
                  ⚠️ 生图能力当前回落 <b>Mock（占位图）</b>：未配置任何图像 Provider
                  （Seedream / FLUX / OpenAI）的 Key，生成结果不是真实图片。
                  <Link className="ml-1" to="/settings">去设置 →</Link>
                </div>
              )}
              {settings.mock_mode && (
                <div className="text-xs mt-1 text-muted">
                  当前为 Mock 模式：流程可完整跑通，但所有产出均为模板数据。
                </div>
              )}
            </div>
          )}

          <div className="form-group">
            <label className="label">商品图片（最多 10 张，拖拽或点击选择）</label>
            <div
              className={`drop-zone ${files.length ? 'active' : ''}`}
              onDrop={handleDrop}
              onDragOver={e => e.preventDefault()}
              onClick={() => document.getElementById('file-input').click()}
            >
              {files.length
                ? <p className="strong">已选择 {files.length} 张图片</p>
                : <p className="text-sm">拖拽图片到这里，或点击选择</p>}
            </div>
            <input
              id="file-input" type="file" multiple accept="image/*" hidden
              onChange={e => setFiles(prev => [...prev, ...Array.from(e.target.files)].slice(0, 10))}
            />
            {files.length > 0 && (
              <div className="flex gap-1 mt-1" style={{ flexWrap: 'wrap' }}>
                {files.map((f, i) => (
                  <span key={i} className="badge badge-info">{f.name}</span>
                ))}
              </div>
            )}
          </div>

          <div className="form-group">
            <label className="label">协作模式</label>
            <select className="select" value={mode} onChange={e => setMode(e.target.value)}>
              {MODES.map(m => <option key={m.value} value={m.value}>{m.label}</option>)}
            </select>
            <p className="text-xs mt-1">{MODES.find(m => m.value === mode)?.desc}</p>
          </div>

          <div className="grid-2">
            <div className="form-group">
              <label className="label">目标平台</label>
              <select className="select" aria-label="目标平台" value={platform}
                      onChange={e => setPlatform(e.target.value)}>
                {platforms.map(p => (
                  <option key={p.slug} value={p.slug}>
                    {p.label}
                    {p.slot_count ? `（主图 ${p.slot_count}${p.detail_slot_count ? ` + 图文 ${p.detail_slot_count}` : ''}）` : ''}
                  </option>
                ))}
              </select>
              {currentPlatform && (
                <p className="text-xs mt-1">
                  {currentPlatform.aspect} · 背景 {currentPlatform.bg || '不限'} ·
                  {currentPlatform.text_policy === 'none' ? ' 白底图不得添加文字' : ' 文字由系统排版'}
                  {currentPlatform.slot_count ? ` · 主图上限 ${currentPlatform.max_images} 张` : ''}
                </p>
              )}
              {currentPlatform?.slot_roles?.length > 0 && (
                <p className="text-xs text-muted">
                  套图角色：
                  {currentPlatform.slot_roles
                    .map(slot => `${slot.label}${slot.kind === 'info' ? '（图文）' : ''}`)
                    .join('、')}
                </p>
              )}
            </div>
            <div className="form-group">
              <label className="label">品类提示</label>
              <select className="select" value={category} onChange={e => setCategory(e.target.value)}>
                <option value="">自动识别</option>
                <option value="保健品">保健品</option>
                <option value="化妆品">化妆品</option>
                <option value="食品">食品</option>
                <option value="3C数码">3C 数码</option>
                <option value="服装">服装</option>
              </select>
            </div>
          </div>

          <div className="form-group">
            <label className="label">补充信息（可选）</label>
            <textarea className="textarea" value={productInfo}
              onChange={e => setProductInfo(e.target.value)}
              placeholder="例如：护肝胶囊，水飞蓟提取物，蓝帽认证，60 粒装" />
          </div>

          {createError && <div className="alert alert-error">{createError}</div>}

          <button type="submit" className="btn btn-primary" disabled={submitting}>
            {submitting ? '提交中...' : '🚀 开始生成'}
          </button>
        </form>

        {/* 会话列表 */}
        <div className="card">
          <div className="flex items-center justify-between mb-1">
            <h3 style={{ marginBottom: 0 }}>历史会话（{sessions.length}）</h3>
            <button className="btn btn-ghost btn-sm" onClick={refresh}>刷新</button>
          </div>
          {listError && <div className="alert alert-error mt-1">{listError}</div>}
          {sessions.length === 0 ? (
            <div className="empty-state">
              <p className="text-lg mb-1">📭</p>
              <p className="text-sm">还没有会话。左侧表单创建第一个任务</p>
            </div>
          ) : (
            <div className="table-wrap" style={{ maxHeight: 560, overflowY: 'auto' }}>
              <table className="table">
                <thead>
                  <tr><th>会话</th><th>状态</th><th>平台</th><th>轮次</th><th>成本</th><th>操作</th></tr>
                </thead>
                <tbody>
                  {sessions.map(s => (
                    <tr key={s.session_id}>
                      <td>
                        <Link to={`/session/${s.session_id}`} className="mono">
                          {s.session_id.slice(0, 10)}…
                        </Link>
                        {s.product_info && <div className="text-xs">{s.product_info.slice(0, 24)}</div>}
                      </td>
                      <td><StatusBadge status={s.status} /></td>
                      <td className="text-sm">{s.platform}</td>
                      <td className="text-sm">{s.turn_count}</td>
                      <td className="text-sm" title={costTitle(sessionCostMeta(s))}>{formatCost(s.cost_so_far, sessionCostMeta(s))}</td>
                      <td>
                        <div className="flex gap-1">
                          <Link to={`/session/${s.session_id}`} className="btn btn-primary btn-sm">查看</Link>
                          <button className="btn btn-danger btn-sm" onClick={e => handleDelete(s.session_id, e)}>删除</button>
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
