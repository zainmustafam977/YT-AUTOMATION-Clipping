import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vitejs.dev/config/
export default defineConfig({
  plugins: [react()],
  base: '/app/',
  build: {
    outDir: '../shorts_engine/dashboard/static/dist',
    emptyOutDir: true,
    assetsDir: 'assets',
    sourcemap: false,
    rollupOptions: {
      output: {
        manualChunks: {
          vendor: ['react', 'react-dom'],
          router: ['react-router-dom'],
        }
      }
    }
  },
  server: {
    proxy: {
      '/api': 'http://127.0.0.1:8000',
      '/queue': 'http://127.0.0.1:8000',
      '/channels': 'http://127.0.0.1:8000',
      '/settings': 'http://127.0.0.1:8000',
    }
  }
})
