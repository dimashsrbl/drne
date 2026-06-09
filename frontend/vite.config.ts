import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'

const frontendRoot = path.dirname(fileURLToPath(import.meta.url))

// https://vite.dev/config/
export default defineConfig(({ mode }) => {
  // Всегда читаем .env.local из папки frontend, даже если npm запущен из корня репо
  const env = loadEnv(mode, frontendRoot, '')
  // VITE_DRONE_API_URL задаётся через env-файл или команду запуска
  // По умолчанию: :8000 (ArduPilot backend)
  // Для симулятора: :8001 (drone-sim backend)
  const apiTarget = (env.VITE_DRONE_API_URL || '').trim() || 'http://127.0.0.1:8000'
  const trackerTarget = (env.VITE_VISION_TRACKER_URL || '').trim() || 'http://127.0.0.1:8001'

  // eslint-disable-next-line no-console
  console.log(`[vite] proxy /api → ${apiTarget}  |  /tracker → ${trackerTarget}`)

  return {
    envDir: frontendRoot,
    plugins: [
      react(),
      {
        name: 'log-proxy-targets',
        configureServer() {
          // eslint-disable-next-line no-console
          console.log(`[vite] proxy /api → ${apiTarget}  |  /tracker → ${trackerTarget}`)
        },
      },
    ],
    server: {
      proxy: {
        '/api': {
          target: apiTarget,
          changeOrigin: true,
          secure: false,
          ws: true,
          rewrite: (path) => path.replace(/^\/api/, ''),
        },
        '/tracker': {
          target: trackerTarget,
          changeOrigin: true,
          secure: false,
          ws: true,
          rewrite: (path) => path.replace(/^\/tracker/, ''),
        },
      },
    },
  }
})
