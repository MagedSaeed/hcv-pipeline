/// <reference types="vitest/config" />
import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';
import tailwindcss from '@tailwindcss/vite';

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    // Vite's CORS middleware runs ahead of the proxy and answers every
    // OPTIONS request itself with an empty 204, so `OPTIONS /api/...` (the
    // repetitions help text) never reached Django. The browser only ever
    // talks to Vite, so nothing here needs CORS.
    cors: false,
    proxy: {
      // The Django dev server. Session cookies and the CSRF cookie ride
      // along because the browser sees this as same-origin (localhost:5173)
      // even though Vite is actually forwarding to :8000.
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
    },
  },
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./src/test/setup.js'],
  },
});
