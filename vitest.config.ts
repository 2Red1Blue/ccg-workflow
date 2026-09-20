import { defineConfig } from 'vitest/config'

export default defineConfig({
  test: {
    include: ['src/**/__tests__/**/*.test.ts'],
    // Installer E2E copies the full template tree. Running those filesystem
    // fixtures beside other installer suites makes their cleanup race a timed-
    // out copy, rather than testing independent behavior.
    fileParallelism: false,
    coverage: {
      provider: 'v8',
      reporter: ['text', 'json-summary', 'lcov'],
      include: ['src/**/*.ts'],
      exclude: ['src/**/__tests__/**', 'src/types/**'],
    },
  },
})
