import { lazy, Suspense } from 'react'
import { Routes, Route, NavLink } from 'react-router-dom'
import ErrorBoundary from './components/ErrorBoundary'

// 页面级代码分割：每个页面独立 chunk，按路由懒加载（拆包优化）
const Dashboard = lazy(() => import('./pages/Dashboard'))
const Sessions = lazy(() => import('./pages/Sessions'))
const Session = lazy(() => import('./pages/Session'))
const Workflows = lazy(() => import('./pages/Workflows'))
const WorkflowJob = lazy(() => import('./pages/WorkflowJob'))
const Batches = lazy(() => import('./pages/Batches'))
const Agents = lazy(() => import('./pages/Agents'))
const Settings = lazy(() => import('./pages/Settings'))
const Audit = lazy(() => import('./pages/Audit'))
const Memory = lazy(() => import('./pages/Memory'))

const NAV = [
  { to: '/', icon: '📊', label: '仪表盘', end: true },
  { to: '/sessions', icon: '💬', label: '会话任务' },
  { to: '/workflows', icon: '🧩', label: '工作流' },
  { to: '/batches', icon: '📦', label: '批量任务' },
  { to: '/agents', icon: '🤖', label: 'Agent 配置' },
  { to: '/settings', icon: '⚙️', label: '系统设置' },
  { to: '/audit', icon: '📜', label: '审计日志' },
  { to: '/memory', icon: '🧠', label: '记忆库' },
]

function PageFallback() {
  return <div className="page-loading">页面加载中…</div>
}

export default function App() {
  return (
    <div className="layout">
      <aside className="sidebar">
        <div className="brand">
          E-Commerce Harness
          <small>群聊式多智能体商品图生成</small>
        </div>
        {NAV.map(item => (
          <NavLink
            key={item.to}
            to={item.to}
            end={item.end}
            className={({ isActive }) => `nav-link ${isActive ? 'active' : ''}`}
          >
            <span className="nav-icon">{item.icon}</span>
            <span>{item.label}</span>
          </NavLink>
        ))}
      </aside>
      <main className="main">
        <div className="container">
          <ErrorBoundary>
            <Suspense fallback={<PageFallback />}>
              <Routes>
                <Route path="/" element={<Dashboard />} />
                <Route path="/sessions" element={<Sessions />} />
                <Route path="/session/:id" element={<Session />} />
                <Route path="/workflows" element={<Workflows />} />
                <Route path="/workflows/:id" element={<WorkflowJob />} />
                <Route path="/batches" element={<Batches />} />
                <Route path="/agents" element={<Agents />} />
                <Route path="/settings" element={<Settings />} />
                <Route path="/audit" element={<Audit />} />
                <Route path="/memory" element={<Memory />} />
                <Route path="*" element={<Dashboard />} />
              </Routes>
            </Suspense>
          </ErrorBoundary>
        </div>
      </main>
    </div>
  )
}
