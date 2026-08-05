import { Component } from 'react'

export default class ErrorBoundary extends Component {
  constructor(props) {
    super(props)
    this.state = { hasError: false, error: null, errorInfo: null }
  }

  static getDerivedStateFromError(error) {
    return { hasError: true, error }
  }

  componentDidCatch(error, errorInfo) {
    this.setState({ errorInfo })
    console.error('[ErrorBoundary] Unhandled render error:', error, errorInfo)
  }

  handleReset = () => {
    this.setState({ hasError: false, error: null, errorInfo: null })
  }

  render() {
    if (this.state.hasError) {
      return (
        <div style={{
          padding: '40px 24px', textAlign: 'center', minHeight: '100vh',
          display: 'flex', flexDirection: 'column', alignItems: 'center',
          justifyContent: 'center', background: '#0f172a', color: '#e2e8f0',
        }}>
          <h2 style={{ fontSize: 24, marginBottom: 12 }}>⚠️ 应用出现错误</h2>
          <p style={{ color: '#94a3b8', marginBottom: 8, maxWidth: 480 }}>
            {this.state.error?.message || '未知渲染错误'}
          </p>
          {this.props.fallback || (
            <button
              onClick={this.handleReset}
              style={{
                marginTop: 16, padding: '10px 24px', background: '#3b82f6',
                color: '#fff', border: 'none', borderRadius: 8, cursor: 'pointer',
                fontSize: 14, fontWeight: 600,
              }}
            >
              重试
            </button>
          )}
          {this.props.fallback && this.props.fallback}
        </div>
      )
    }

    return this.props.children
  }
}
