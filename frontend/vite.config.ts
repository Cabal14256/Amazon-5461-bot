import { fileURLToPath, URL } from 'node:url'
import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// 纯前端阶段：/api 代理预留到未来的 FastAPI 服务（src/web/api/），当前所有数据来自 src/api/mock。
// 后端按 config/settings.yaml 的 web.ssl_* 启用了自签 HTTPS，代理需走 https 并关闭证书校验。
export default defineConfig({
  plugins: [vue()],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'https://127.0.0.1:8080',
        changeOrigin: true,
        secure: false,
      },
    },
  },
})
