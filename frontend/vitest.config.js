import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'

// 前端自动化测试（测试计划 P1）：Vitest + jsdom + @testing-library/react
// 运行: npm test（一次性）/ npm run test:watch（监听）
export default defineConfig({
  plugins: [react()],
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: './src/test/setup.js',
    exclude: ['node_modules', 'dist'],
  },
})
