import { resolve } from 'node:path'
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  build: {
    lib: {
      entry: resolve(import.meta.dirname, 'src/App.tsx'),
      formats: ['es'],
      fileName: () => 'index.mjs',
    },
    outDir: resolve(import.meta.dirname, 'dist'),
    emptyOutDir: true,
    rollupOptions: {
      external: [
        'react',
        'react-dom',
        'react/jsx-runtime',
        '@kirocrew/app-sdk',
        '@kirocrew/app-sdk/ui',
        'lucide-react',
      ],
    },
  },
})
