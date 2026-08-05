import { useState, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { createSession, getAgents } from '../api'

export default function Home() {
  const navigate = useNavigate()
  const [files, setFiles] = useState([])
  const [platform, setPlatform] = useState('taobao')
  const [productInfo, setProductInfo] = useState('')
  const [category, setCategory] = useState('')
  const [loading, setLoading] = useState(false)
  const [agents, setAgents] = useState([])
  const [agentsError, setAgentsError] = useState('')

  useEffect(() => {
    getAgents()
      .then(r => setAgents(r?.agents || []))
      .catch(err => setAgentsError(err.message || '无法加载 Agent 列表'))
  }, [])

  async function handleSubmit(e) {
    e.preventDefault()
    if (!files.length) return
    setLoading(true)
    try {
      const result = await createSession(files, productInfo, platform, category)
      navigate(`/session/${result.session_id}`)
    } catch (err) {
      alert('创建失败: ' + err.message)
      setLoading(false)
    }
  }

  function handleDrop(e) {
    e.preventDefault()
    const dropped = Array.from(e.dataTransfer.files).filter(f => f.type.startsWith('image/'))
    setFiles(prev => [...prev, ...dropped].slice(0, 10))
  }

  return (
    <div>
      <h2 className="mb-2">新建商品图任务</h2>

      <div className="grid-2">
        <form onSubmit={handleSubmit} className="card flex flex-col gap-2">
          <div>
            <label className="label">商品图片（拖拽或点击选择）</label>
            <div
              className={`drop-zone ${files.length ? 'active' : ''}`}
              onDrop={handleDrop}
              onDragOver={e => e.preventDefault()}
              onClick={() => document.getElementById('file-input').click()}
            >
              {files.length
                ? <p>已选择 {files.length} 张图片</p>
                : <p className="text-sm">拖拽图片到这里，或点击选择</p>}
            </div>
            <input
              id="file-input" type="file" multiple accept="image/*"
              className="mt-1" hidden
              onChange={e => setFiles(prev => [...prev, ...Array.from(e.target.files)].slice(0, 10))}
            />
            {files.length > 0 && (
              <div className="flex gap-1 mt-1" style={{flexWrap:'wrap'}}>
                {files.map((f, i) => (
                  <span key={i} className="badge badge-info">{f.name}</span>
                ))}
              </div>
            )}
          </div>

          <div>
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

          <div>
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

          <div>
            <label className="label">补充信息（可选）</label>
            <textarea className="textarea" value={productInfo}
              onChange={e => setProductInfo(e.target.value)}
              placeholder="例如：护肝胶囊，水飞蓟提取物，蓝帽认证" />
          </div>

          <button type="submit" className="btn btn-primary" disabled={loading || !files.length}>
            {loading ? '提交中...' : '开始生成'}
          </button>
        </form>

        <div className="card">
          <h3 className="mb-1">可用 Agent ({agents.length})</h3>
          <div className="flex flex-col gap-1">
            {agents.map(a => (
              <div key={a.name} style={{padding:'8px 0',borderBottom:'1px solid #334155'}}>
                <div className="flex items-center justify-between">
                  <strong>{a.name}</strong>
                  <span className="badge badge-info">{a.requires?.[0]}</span>
                </div>
                <p className="text-sm">{a.description}</p>
                {a.params?.length > 0 && (
                  <div className="text-sm mt-1">
                    {a.params.map(p => (
                      <span key={p.key} className="badge badge-warn" style={{marginRight:4}}>
                        {p.label}: {p.default}
                      </span>
                    ))}
                  </div>
                )}
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  )
}
