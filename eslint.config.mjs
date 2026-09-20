import js from '@eslint/js'
import tsParser from '@typescript-eslint/parser'

export default [{
  files: ['src/**/*.ts'],
  languageOptions: {
    parser: tsParser,
    parserOptions: {
      ecmaVersion: 'latest',
      sourceType: 'module',
    },
  },
  rules: {
    ...js.configs.recommended.rules,
    // TypeScript resolves names such as Buffer and process; tsc is the type gate.
    'no-undef': 'off',
    // Existing CLI entry points intentionally retain unused compatibility hooks.
    'no-unused-vars': 'off',
    // The menu deliberately matches ANSI escape sequences before measuring width.
    'no-control-regex': 'off',
  },
}]
