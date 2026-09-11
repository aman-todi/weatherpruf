import { defineConfig, loadEnv } from 'vite';
import react from '@vitejs/plugin-react';

// In production the FastAPI container serves this bundle and /api from the
// same origin, so the client uses relative URLs. This proxy gives the dev
// server the same shape, which means the API client needs no environment
// branch and no CORS is involved in either mode.
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, '.', '');
  return {
    plugins: [react()],
    server: {
      port: 5173,
      proxy: {
        '/api': {
          target: env.VITE_DEV_API_TARGET || 'http://localhost:8000',
          changeOrigin: true,
        },
      },
    },
  };
});
