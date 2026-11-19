import { defineConfig } from 'vitest/config';

const base = process.env.VITE_BASE_PATH || '/';

export default defineConfig({
  base,
  server: {
    host: true,
    port: 5173,
    proxy: {
      '/ws': {
        target: 'ws://127.0.0.1:8765',
        ws: true,
      },
    },
  },
  test: {
    poolOptions: {
      forks: {
        // Node 25+ defines its own localStorage global, which hides jsdom's in tests.
        execArgv: ['--no-experimental-webstorage'],
      },
    },
  },
});
