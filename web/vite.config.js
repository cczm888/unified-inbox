import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  server: {
    port: 5173,
    // Windows 下编辑器/工具会在 src 里留下临时目录，watcher 碰到被占用的文件会 EBUSY 直接把 dev server 打崩
    watch: {
      ignored: ['**/node_modules/**', '**/dist/**', '**/.tmp/**', '**/*.tmpdir/**', '**/*.tmp'],
    },
    // 本地开发直接代理到后端，前端代码里只写 /api/v1/...
    proxy: {
      '/api': { target: 'http://127.0.0.1:8000', changeOrigin: true },
      '/healthz': { target: 'http://127.0.0.1:8000', changeOrigin: true },
    },
  },
  build: { outDir: 'dist', sourcemap: false },
})
