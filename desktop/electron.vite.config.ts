import { resolve } from 'path'
import { defineConfig } from 'electron-vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

export default defineConfig({
  main: {},
  preload: {},
  renderer: {
    resolve: {
      alias: {
        '@renderer': resolve('src/renderer/src'),
        '@shared': resolve('src/shared'),
        // v0.2.6 folder shape: feature domains own their components, logic and
        // types together. Only new code lives here; the v0.0.1 shell stays put.
        '@domains': resolve('src/domains')
      }
    },
    plugins: [react(), tailwindcss()]
  }
})
