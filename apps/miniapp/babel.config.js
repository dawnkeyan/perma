/**
 * Babel 配置。
 *
 * 必须显式声明 `ts: true`：
 * Taro 的 babel-preset-taro 默认按「纯 JS 项目」处理，
 * 只有打开 ts 开关才会挂上 @babel/preset-typescript，
 * 否则 .ts/.tsx 文件会在 `import type` 这类语法上报
 * "Unexpected token, expected ','"。
 *
 * 文档：https://docs.taro.zone/docs/next/babel-config
 */
module.exports = {
  presets: [
    [
      'taro',
      {
        framework: 'react',
        ts: true,
      },
    ],
  ],
};
