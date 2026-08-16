import { useState, useEffect, useCallback } from 'react'
import { useNavigate, Link } from 'react-router-dom'
import { createSession, getSessions, deleteSession } from '../api'
import { StatusBadge } from './Dashboard'

const MODES = [
  { value: 'serial', label: '串行工作流', desc: '标准流程：分析→提示词→生图→审查→合规' },
  { value: 'ab_generate', label: 'A/B 生成', desc: '两套风格图片对比选优' },
  { value: 'ab_test', label: 'A/B 测试', desc: '多模型提示词对比 + 多审查员评分' },
  { value: 'debate', label: '辩论模式', desc: '正反双方审查辩论' },
  { value: 'vote', label: '投票模式', desc: '3 位审查员独立投票' },
]

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
    refresh()
    const t = setInterval(refresh, 3000)
    return () => clearInterval(t)
  }, [refresh])

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
              <select className="select" value={platform} onChange={e => setPlatform(e.target.value)}>
                <option value="taobao">淘宝</option>
                <option value="amazon">Amazon</option>
                <option value="xiaohongshu">小红书</option>
                <option value="douyin">抖音</option>
                <option value="jd">京东</option>
                <option value="shopify">Shopify</option>
              </select>
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
                      <td className="text-sm">${(s.cost_so_far || 0).toFixed(4)}</td>
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
