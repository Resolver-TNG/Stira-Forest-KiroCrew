import { resolve } from 'node:path'
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  root: resolve(import.meta.dirname, 'preview'),
  base: './',
  plugins: [react()],
  publicDir: false,
  build: {
    outDir: resolve(import.meta.dirname, 'preview-dist'),
    emptyOutDir: true,
  },
})
