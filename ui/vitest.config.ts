import { resolve } from 'node:path'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vitest/config'

export default defineConfig({
  plugins: [react()],
  esbuild: {
    jsx: 'automatic',
  },
  resolve: {
    alias: [
      {
        find: /^@kirocrew\/app-sdk\/ui$/,
        replacement: resolve(import.meta.dirname, 'src/layout/__tests__/appSdkUiStub.tsx'),
      },
      {
        find: /^@kirocrew\/app-sdk$/,
        replacement: resolve(import.meta.dirname, 'src/layout/__tests__/appSdkStub.ts'),
      },
    ],
  },
  test: {
    environment: 'node',
    include: ['src/layout/__tests__/**/*.test.{ts,tsx}'],
  },
})
