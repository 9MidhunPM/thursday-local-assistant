import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import { fileURLToPath, URL } from 'node:url'

const BACKEND = process.env.THURSDAY_BACKEND || 'http://127.0.0.1:5005'

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  server: {
    port: 5173,
    proxy: {
      '/api': { target: BACKEND, changeOrigin: true, ws: false },
      '/health': { target: BACKEND, changeOrigin: true },
    },
  },
  build: {
    outDir: 'dist',
    sourcemap: false,
    emptyOutDir: true,
  },
})
