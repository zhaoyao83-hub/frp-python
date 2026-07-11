import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// Vite 配置：开发端口 5173，代理 /api 和 /ws 到后端 :7500
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://localhost:7500',
        changeOrigin: true,
      },
      '/ws': {
        target: 'http://localhost:7500',
        changeOrigin: true,
        ws: true,
      },
    },
  },
  build: {
    outDir: 'dist',
    sourcemap: false,
  },
});
