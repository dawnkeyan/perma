/**
 * ESLint 配置（ESLint 8 flat 之外的 eslintrc 风格，与 eslint ^8.57 匹配）。
 *
 * 设计取舍：
 * 1. 类型相关的问题交给 `tsc --noEmit`（CI 里是独立一步），ESLint 只管代码风格与
 *    React Hooks 规则，不重复做类型检查。
 * 2. `no-undef` 关闭：TS 已经检查了未定义标识符，在 TS 文件里再开一遍会误报
 *    Taro 的全局宏（defineAppConfig / definePageConfig）。
 * 3. 关闭 `react/react-in-jsx-scope`：React 18 新的 JSX transform 不需要显式
 *    import React。
 */

module.exports = {
  root: true,
  env: {
    browser: true,
    es2022: true,
    node: true,
  },
  parser: '@typescript-eslint/parser',
  parserOptions: {
    ecmaVersion: 2022,
    sourceType: 'module',
    ecmaFeatures: { jsx: true },
  },
  plugins: ['@typescript-eslint', 'react', 'react-hooks'],
  extends: [
    'eslint:recommended',
    'plugin:@typescript-eslint/recommended',
    'plugin:react/recommended',
    'plugin:react-hooks/recommended',
    'prettier',
  ],
  settings: {
    react: { version: 'detect' },
  },
  rules: {
    'no-undef': 'off',
    'react/react-in-jsx-scope': 'off',
    'react/jsx-uses-react': 'off',
    'react/prop-types': 'off',
    'no-unused-vars': 'off',
    '@typescript-eslint/no-unused-vars': [
      'error',
      { argsIgnorePattern: '^_', varsIgnorePattern: '^_' },
    ],
    '@typescript-eslint/no-explicit-any': 'warn',
    '@typescript-eslint/consistent-type-imports': [
      'error',
      { prefer: 'type-imports', fixStyle: 'inline-type-imports' },
    ],
  },
  ignorePatterns: ['dist', 'node_modules', 'config'],
};
