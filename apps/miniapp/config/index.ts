import { defineConfig } from '@tarojs/cli';

export default defineConfig(async (merge) => {
  const baseConfig = {
    projectName: 'lumina-miniapp',
    date: '2026-10-20',
    designWidth: 750,
    deviceRatio: {
      640: 2.34 / 2,
      750: 1,
      375: 2,
      828: 1.81 / 2,
    },
    sourceRoot: 'src',
    outputRoot: `dist/${process.env.TARO_ENV}`,
    plugins: [],
    defineConstants: {
      API_BASE_URL: JSON.stringify(
        process.env.TARO_APP_API_BASE ?? 'http://localhost:8000/v1',
      ),
      APP_VERSION: JSON.stringify(process.env.TARO_APP_VERSION ?? '0.1.0'),
      ENV: JSON.stringify(process.env.TARO_ENV ?? 'development'),
    },
    copy: {
      patterns: [],
      options: {},
    },
    framework: 'react',
    compiler: {
      type: 'webpack5',
      prebundle: { enable: false },
    },
    cache: { enable: false },
    mini: {
      postcss: {
        pxtransform: { enable: true, config: {} },
        cssModules: { enable: false },
      },
    },
    h5: {
      publicPath: '/',
      staticDirectory: 'static',
      postcss: {
        autoprefixer: { enable: true, config: {} },
        cssModules: { enable: false },
      },
    },
  };

  if (process.env.NODE_ENV === 'development') {
    return merge({}, baseConfig, {
      mini: {},
      h5: {},
    });
  }
  return merge({}, baseConfig);
});
