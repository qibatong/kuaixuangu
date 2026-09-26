/* eslint-env node */
module.exports = {
  root: true,
  env: { browser: true, es2022: true, node: true },
  parserOptions: { ecmaVersion: 2022, sourceType: 'module' },
  extends: [
    'eslint:recommended',
    'plugin:vue/vue3-recommended'
  ],
  rules: {
    // 关键: 阻止未定义引用(历史上 computed 未 import 导致白屏)
    'no-undef': 'error',
    // 允许 console(项目有 logger 体系, 但 console 直接调用也放行)
    'no-console': 'off',
    // Vue 3 推荐规则的宽松化(与现有代码风格对齐)
    'vue/multi-word-component-names': 'off',
    'vue/max-attributes-per-line': 'off',
    'vue/singleline-html-element-content-newline': 'off',
    'vue/html-indent': 'off',
    'vue/html-self-closing': 'off',
    'vue/no-v-html': 'off',
    'vue/require-default-prop': 'off',
    'vue/require-prop-types': 'off',
    // 未使用变量: 警告(存量代码有, 逐步清理)
    'no-unused-vars': ['warn', { argsIgnorePattern: '^_', varsIgnorePattern: '^_' }],
    // 2026-09-27 v4.11.60: 允许空 catch —— 项目里 `try{...}catch(e){}` 都是"尽力而为"型
    // (localStorage 兜底 / 图表实例销毁), 空块是有意为之, 不该拦发布。
    'no-empty': ['error', { allowEmptyCatch: true }]
  },
  // 2026-09-27 v4.11.60: 冒烟测试产物目录(临时构建输出)不参与 lint
  ignorePatterns: ['dist/', 'node_modules/', 'public/', '.navssr/']
}
