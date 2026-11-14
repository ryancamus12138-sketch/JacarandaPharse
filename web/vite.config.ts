import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// Vite configuration. https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    host: '127.0.0.1', // Listen on localhost only
    port: 5173,
    strictPort: true, // Fail if 5173 is taken instead of silently switching ports
    proxy: {
      // Proxy /api/... to the local backend, so the browser sees a single origin (no CORS)
      '/api': 'http://127.0.0.1:8000',
    },
  },
  build: {
    chunkSizeWarningLimit: 2000, // Ant Design is large; a few hundred KB do not matter for local use
  },
  preview: {
    host: '127.0.0.1',
    port: 4173,
  },
})
