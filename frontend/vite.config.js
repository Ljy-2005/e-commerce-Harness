import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': 'http://localhost:8000',
      '/ws': { target: 'ws://localhost:8000', ws: true },
      '/health': 'http://localhost:8000',
    },
  },
  build: {
    chunkSizeWarningLimit: 550,
    rollupOptions: {
      output: {
        // 拆包策略（progress.md §5.3 前端产物体积优化）：
        // - 页面级 React.lazy（App.jsx）把 10 个页面拆成按需 chunk
        // - recharts + d3 家族独立成 charts chunk，仅图表页面按需加载
        // - react 全家桶独立成 react-vendor（稳定、可长期缓存）
        // - 其余第三方包进 vendor
        manualChunks(id) {
          if (!id.includes('node_modules')) return undefined
          if (
            id.includes('recharts')
            || id.includes('d3-')
            || id.includes('victory-vendor')
            || id.includes('react-smooth')
            || id.includes('decimal.js')
            || id.includes('internmap')
            || id.includes('delaunator')
            || id.includes('robust-predicates')
          ) {
            return 'charts'
          }
          if (
            id.includes('/react/')
            || id.includes('/react-dom/')
            || id.includes('react-router')
            || id.includes('@remix-run')
            || id.includes('/scheduler/')
            || id.includes('/history/')
          ) {
            return 'react-vendor'
          }
          return 'vendor'
        },
      },
    },
  },
})
