import { useState } from 'react'
import { runABTest } from '../api'

const DEFAULT_VARIANTS = [
  { variant_id: 'v_gpt4o', label: 'GPT-4o', model_override: 'gpt-4o' },
  { variant_id: 'v_deepseek', label: 'DeepSeek', model_override: 'deepseek-chat' },
  { variant_id: 'v_qwen', label: 'Qwen-Max', model_override: 'qwen-max' },
]

export default function ABTestPanel({ sessionId, onDone }) {
  const [open, setOpen] = useState(false)
  const [variants, setVariants] = useState(DEFAULT_VARIANTS)
  const [reviewCount, setReviewCount] = useState(3)
  const [agentName, setAgentName] = useState('提示词生成员')
  const [running, setRunning] = useState(false)
  const [result, setResult] = useState(null)
  const [error, setError] = useState('')

  async function handleRun() {
    setRunning(true)
    setError('')
    setResult(null)
    try {
      const data = await runABTest(sessionId, {
        agent_name: agentName,
        variants,
        review_count: reviewCount,
      })
      setResult(data)
      if (onDone) onDone()
    } catch (err) {
      setError(err.message || 'A/B 测试失败')
    }
    setRunning(false)
  }

  return (
    <div className="card mt-2">
      <div className="flex items-center justify-between">
        <h3 style={{ marginBottom: 0 }}>🔬 A/B 测试</h3>
        <button className="btn btn-ghost btn-sm" onClick={() => setOpen(!open)}>
          {open ? '收起' : '展开'}
        </button>
      </div>

      {open && (
        <div className="mt-2">
          <p className="text-sm mb-2">对同一 Agent 配置多个模型/提示词变体并行执行，由审查员评分自动选优。</p>

          <div className="grid-2 mb-2">
            <div className="form-group">
              <label className="label">测试 Agent</label>
              <select className="select" value={agentName} onChange={e => setAgentName(e.target.value)}>
                <option value="提示词生成员">提示词生成员</option>
                <option value="商品分析员">商品分析员</option>
                <option value="品类专项分析员">品类专项分析员</option>
              </select>
            </div>
            <div className="form-group">
              <label className="label">评审人数</label>
              <input className="input" type="number" min="1" max="10"
                value={reviewCount} onChange={e => setReviewCount(parseInt(e.target.value) || 3)} />
            </div>
          </div>

          <div className="form-group">
            <label className="label">变体列表（label 用于展示，model_override 为目标模型）</label>
            <textarea
              className="textarea"
              style={{ minHeight: 120, fontFamily: 'Consolas, monospace', fontSize: 12 }}
              value={JSON.stringify(variants, null, 2)}
              onChange={e => {
                try { setVariants(JSON.parse(e.target.value)) } catch {}
              }}
            />
          </div>

          {error && <div className="alert alert-error">{error}</div>}
          {result && <ABTestResult result={result} />}

          <button className="btn btn-primary" onClick={handleRun} disabled={running}>
            {running ? '运行中...' : '🚀 运行 A/B 测试'}
          </button>
        </div>
      )}
    </div>
  )
}

function ABTestResult({ result }) {
  const ranking = result.ranking || []
  return (
    <div className="mb-2">
      <div className="alert alert-info">
        优胜变体: <span className="strong">{result.winner !== 'none' && result.winner ? result.winner : '无（未达阈值）'}</span>
        {result.winner_score > 0 && <span> · 得分 {result.winner_score}/100</span>}
        <span> · 总成本 ${(result.total_cost_usd || 0).toFixed(4)}</span>
      </div>
      <div className="table-wrap">
        <table className="table">
          <thead>
            <tr><th>排名</th><th>变体</th><th>标签</th><th>得分</th><th>成本</th><th>耗时</th></tr>
          </thead>
          <tbody>
            {ranking.map((v, i) => (
              <tr key={v.id}>
                <td>{i === 0 ? '🏆' : i + 1}</td>
                <td className="mono">{v.id}</td>
                <td>{v.label}</td>
                <td className="strong">{v.score}/100</td>
                <td className="text-sm">${(v.cost_usd || 0).toFixed(4)}</td>
                <td className="text-sm">{Math.round(v.elapsed_ms || 0)}ms</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
