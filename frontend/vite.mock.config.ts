import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import tailwindcss from '@tailwindcss/vite';
import { mockApi } from './dev/mock-api';

// Design/QA server with an in-memory fake API (see dev/mock-api.ts). No proxy: nothing reaches the real backend.
export default defineConfig({
  plugins: [mockApi(), react(), tailwindcss()],
  server: { host: '127.0.0.1', port: 5175, strictPort: true },
});
