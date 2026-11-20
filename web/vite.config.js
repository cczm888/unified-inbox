import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import Components from 'unplugin-vue-components/vite'
import { AntDesignVueResolver } from 'unplugin-vue-components/resolvers'

// 端口可以环境变量覆盖：同一台机器上跑多个互不相干的实例时要用，
// 否则第二个实例会跟第一个抢 5173 / 连到第一个的后端。
const WEB_PORT = Number(process.env.WEB_PORT || 5173)
const API_TARGET = process.env.API_TARGET || 'http://127.0.0.1:8000'

export default defineConfig({
  plugins: [
    vue(),
    // 按需引入 ant-design-vue：原来 main.js 里 `use(Antd)` 会把整套组件库打进包，
    // 页面实际只用到十几个组件，多出来的近 900 KB 纯粹是首屏负担。
    // importStyle: false —— 全局样式已由 reset.css 兜底，组件自身的样式随组件按需带上。
    Components({
      resolvers: [AntDesignVueResolver({ importStyle: false })],
      dts: false,
    }),
  ],
  server: {
    port: WEB_PORT,
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
      '/api': { target: API_TARGET, changeOrigin: true },
      '/healthz': { target: API_TARGET, changeOrigin: true },
    },
  },
  build: {
    outDir: 'dist',
    sourcemap: false,
    // antd / vue / dayjs 各自单独成块：它们体积大且几乎不变，
    // 单独一个 chunk 能长期命中浏览器缓存，改业务代码时不必重新下载。
    rollupOptions: {
      output: {
        manualChunks(id) {
          if (id.includes('node_modules/ant-design-vue')) return 'antd'
          if (id.includes('node_modules/@ant-design')) return 'antd'
          if (id.includes('node_modules/dayjs')) return 'dayjs'
          if (id.includes('node_modules/@vue') || id.includes('node_modules/vue')) return 'vue'
        },
      },
    },
  },
})
