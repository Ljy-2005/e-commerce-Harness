import { useState, useEffect, useCallback } from 'react'
import {
  getStyleLibrary, createStyleEntry, updateStyleEntry,
  deleteStyleEntry, reanalyzeStyleEntry, getPlatforms, getSettings,
} from '../api'
import StyleCard, { StyleAddCard } from '../components/StyleCard'
import StyleModal from '../components/StyleModal'
import StylePreview from '../components/StylePreview'

/** 分析中每 2 秒轮询列表；全部就绪 / 组件卸载即停 */
export const POLL_INTERVAL_MS = 2000

const TABS = [
  { key: 'mine', label: '我的词条' },
  { key: 'builtin', label: '内置档案' },
]

export default function Styles() {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [tab, setTab] = useState('mine')
  const [modal, setModal] = useState(null)      // {mode, entry}
  const [preview, setPreview] = useState(null)  // {entry}
  const [busyId, setBusyId] = useState('')
  const [platforms, setPlatforms] = useState([])
  const [mockMode, setMockMode] = useState(false)
  const [notice, setNotice] = useState('')

  const mine = data?.mine || []
  const builtin = data?.builtin || []
  const hasAnalyzing = mine.some(e => e.status === 'analyzing')

  const fetchLibrary = useCallback(async () => {
    try {
      const res = await getStyleLibrary()
      setData(res)
      setError('')
    } catch (err) {
      setError(err.message || '加载风格词库失败')
    }
    setLoading(false)
  }, [])

  useEffect(() => { fetchLibrary() }, [fetchLibrary])

  // 只在"列表里还有 analyzing 的条目"时轮询（契约：分析约 20–40 秒）
  useEffect(() => {
    if (!hasAnalyzing) return undefined
    const timer = setInterval(fetchLibrary, POLL_INTERVAL_MS)
    return () => clearInterval(timer)
  }, [hasAnalyzing, fetchLibrary])

  // 平台档案来自后端（getPlatforms），预览与"适用范围"提示都以此为准
  useEffect(() => {
    let alive = true
    getPlatforms()
      .then(res => { if (alive) setPlatforms(res?.platforms || []) })
      .catch(() => {})
    // Mock 模式：估算区显示"$0（不走网络）"，不显示任何金额
    getSettings()
      .then(res => { if (alive) setMockMode(Boolean(res?.mock_mode)) })
      .catch(() => {})
    return () => { alive = false }
  }, [])

  const stats = data?.stats || {}
  const limits = stats.limits || null
  const list = tab === 'mine' ? mine : builtin

  async function handleToggle(entry) {
    setBusyId(entry.id)
    setError('')
    setNotice('')
    try {
      const res = await updateStyleEntry(entry.id, { enabled: !entry.enabled })
      // 启用一套 → 其他套自动停用（一轮会话一套风格词）：名单要如实显示，
      // 否则用户会以为"我明明启用了两套，怎么只用了一套"
      const disabled = res?.auto_disabled || []
      if (disabled.length) {
        setNotice(`已启用「${entry.name}」；自动停用了 ` +
          disabled.map(item => `「${item.name || item.id}」`).join('、') +
          '（一轮会话只用一套风格词）')
      }
      await fetchLibrary()
    } catch (err) {
      setError(err.message || '操作失败')
    }
    setBusyId('')
  }

  async function handleDelete(entry) {
    const adopted = entry.adopted || 0
    const tip = adopted > 0
      ? `确定删除「${entry.name}」？它已被 ${adopted} 次会话采用（参考值），删除后这些会话的历史记录仍保留当时快照，但后续任务不再命中。`
      : `确定删除「${entry.name}」？`
    if (!window.confirm(tip)) return
    setBusyId(entry.id)
    try {
      await deleteStyleEntry(entry.id)
      await fetchLibrary()
    } catch (err) {
      setError(err.message || '删除失败')
    }
    setBusyId('')
  }

  async function handleReanalyze(entry) {
    setBusyId(entry.id)
    try {
      await reanalyzeStyleEntry(entry.id)
      await fetchLibrary()
    } catch (err) {
      setError(err.message || '重新分析失败')
    }
    setBusyId('')
  }

  function handleAction(action, entry) {
    if (action === 'preview') {
      setPreview({ entry })
      return
    }
    if (action === 'edit') {
      setModal({ mode: 'edit', entry })
      return
    }
    if (action === 'reanalyze') {
      setModal({ mode: 'reanalyze', entry })
      return
    }
    if (action === 'toggle') handleToggle(entry)
    if (action === 'delete') handleDelete(entry)
  }

  return (
    <div>
      <h1 className="page-title">风格词库</h1>
      <p className="page-sub">
        {stats.mine ?? mine.length} 条我的词条 · {stats.builtin ?? builtin.length} 条内置档案 · 本次最多注入{' '}
        {stats.max_entries ?? '—'} 套风格 · 锚点 {stats.anchors ?? 0} 条
        {stats.enabled === false && <span className="chip chip-warn ml-1">设置页已关闭风格注入</span>}
        {stats.enabled !== false && (stats.max_entries ?? 1) <= 1
          && <span className="chip ml-1" title="参考套图没覆盖的槽位只按平台槽位契约写，不会再塞一套内置原型">
            一轮会话只用一套风格</span>}
      </p>

      {/* 产品负责人指定的顶栏小字说明 */}
      <p className="style-notice">
        <b>一轮会话（一组生成图）只用一套风格词</b>：启用一套会<b>自动停用</b>其他套；
        <b>一套 = 背景 / 构图 / 光影 / 材质 / 颜色分工等一组字段</b>（一起用才构成这套风格），
        整套图共用同一套，才不会一张一个样；
        你亲自挑的风格会作为审美基准，参与每一次提示词生成与审核；导入的照片只用于风格分析，
        <span className="strong">不会</span>成为生图参考图。
      </p>

      {error && <div className="alert alert-error" role="alert">{error}</div>}
      {notice && <div className="alert alert-info" role="status" data-testid="style-notice">{notice}</div>}
      {(stats.dropped || []).length > 0 && (
        <div className="alert alert-warn">
          有 {stats.dropped.length} 条档案因不合法被丢弃：
          {stats.dropped.slice(0, 3).map(d => `${d.id}（${(d.reasons || []).join('；')}）`).join('｜')}
        </div>
      )}

      <div className="tabs">
        {TABS.map(t => (
          <button key={t.key} className={`tab ${tab === t.key ? 'active' : ''}`}
            onClick={() => setTab(t.key)}>
            {t.label}（{t.key === 'mine' ? mine.length : builtin.length}）
          </button>
        ))}
      </div>

      {loading ? (
        <div className="card text-center" style={{ minHeight: 160, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
          <div><div className="spinner" /><p className="text-sm mt-2">加载风格词库…</p></div>
        </div>
      ) : (
        <div className="style-grid" data-testid="style-grid">
          {/* ＋ 卡固定排在网格首位（空态时它是唯一一张） */}
          {tab === 'mine' && <StyleAddCard onClick={() => setModal({ mode: 'create', entry: null })} />}
          {list.map(entry => (
            <StyleCard key={entry.id} entry={entry} busy={busyId === entry.id}
              onAction={handleAction} />
          ))}
          {list.length === 0 && tab === 'builtin' && (
            <div className="empty-state"><p className="text-sm">暂无内置档案（config/style_library.yaml）</p></div>
          )}
        </div>
      )}

      {tab === 'mine' && list.length === 0 && !loading && (
        <p className="text-xs text-muted mt-2">
          还没有自己的风格词条：点上面的「＋」导入一组照片，风格分析 Agent 会读出它们的共同审美。
        </p>
      )}

      {modal?.mode === 'create' && (
        <StyleModal mode="create" platforms={platforms} mockMode={mockMode} limits={limits}
          onClose={() => setModal(null)}
          onDone={() => { setModal(null); fetchLibrary() }} />
      )}
      {modal?.mode === 'edit' && (
        <StyleModal mode="edit" entry={modal.entry} platforms={platforms} limits={limits}
          onClose={() => setModal(null)} onDone={() => { setModal(null); fetchLibrary() }} />
      )}
      {modal?.mode === 'reanalyze' && (
        <StyleModal mode="reanalyze" entry={modal.entry}
          onClose={() => setModal(null)} onDone={() => { setModal(null); fetchLibrary() }} />
      )}
      {preview && (
        <StylePreview entryId={preview.entry.id} entryName={preview.entry.name}
          platforms={platforms} onClose={() => setPreview(null)} />
      )}
    </div>
  )
}
