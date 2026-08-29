import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// The API runs as its own process during development. Proxying /api keeps the
// browser on one origin, which matters for EventSource: a cross-origin SSE
// stream is a different set of rules to get wrong on demo day.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
    },
  },
  build: { outDir: 'dist', emptyOutDir: true },
})
