import react from '@vitejs/plugin-react';
import { defineConfig } from 'vite';

// In development the console talks to the gateway through this proxy (same origin => no CORS needed).
const gateway = process.env.VITE_GATEWAY_URL ?? 'http://localhost:8000';

export default defineConfig({
  plugins: [react()],
  server: { port: 5173, proxy: { '/api': { target: gateway, changeOrigin: true } } },
  build: {
    sourcemap: false,
    chunkSizeWarningLimit: 900,
    rollupOptions: {
      output: {
        manualChunks: {
          react: ['react', 'react-dom', 'react-router-dom'],
          mui: ['@mui/material', '@mui/icons-material', '@emotion/react', '@emotion/styled'],
          charts: ['recharts'],
          flow: ['@xyflow/react'],
        },
      },
    },
  },
});
