import { defineConfig } from 'vitest/config';

// Kept separate from vite.config.ts: vitest bundles its own Vite version, so sharing plugin types would not type-check.
export default defineConfig({
  esbuild: { jsx: 'automatic' },
  test: { environment: 'jsdom', globals: true, setupFiles: ['./src/test/setup.ts'], css: false, include: ['src/**/*.test.{ts,tsx}'] },
});
