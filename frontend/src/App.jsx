import { Routes, Route, Link } from 'react-router-dom'
import Home from './pages/Home'
import Session from './pages/Session'

export default function App() {
  return (
    <div>
      <header className="header">
        <Link to="/"><h1>E-Commerce Harness</h1></Link>
        <span className="text-sm">群聊式多智能体商品图生成</span>
      </header>
      <main className="container mt-2">
        <Routes>
          <Route path="/" element={<Home />} />
          <Route path="/session/:id" element={<Session />} />
        </Routes>
      </main>
    </div>
  )
}
