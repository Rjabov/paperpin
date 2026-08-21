import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// dev: `npm run dev` proxies /api to a running `paperpin lab --no-browser`
// prod: `npm run build` → dist/ is served by the FastAPI app itself
export default defineConfig({
  plugins: [react(), tailwindcss()],
  base: './',
  server: {
    proxy: { '/api': 'http://127.0.0.1:8377' },
  },
})
