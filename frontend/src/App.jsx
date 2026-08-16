import { Routes, Route, NavLink } from 'react-router-dom'
import ErrorBoundary from './components/ErrorBoundary'
import Dashboard from './pages/Dashboard'
import Sessions from './pages/Sessions'
import Session from './pages/Session'
import Workflows from './pages/Workflows'
import WorkflowJob from './pages/WorkflowJob'
import Batches from './pages/Batches'
import Agents from './pages/Agents'
import Settings from './pages/Settings'
import Audit from './pages/Audit'
import Memory from './pages/Memory'

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
          </ErrorBoundary>
        </div>
      </main>
    </div>
  )
}
