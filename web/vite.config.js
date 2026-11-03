import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  server: {
    port: 5173,
    // 默认只认 localhost，任何「换个域名访问」的姿势（cloudflared 隧道、内网 IP、
    // 反向代理）都会被 Vite 的 Host 校验挡成 403。演示时要让评委在自己电脑上打开，
    // 所以这里放开：本地回环 + 私网 IP + Cloudflare 快速隧道后缀。
    allowedHosts: ['localhost', '127.0.0.1', '.trycloudflare.com', '.local', '.internal'],
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
