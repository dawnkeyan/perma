// 注意：Taro 的 React 版生命周期 hook 叫 useDidShow（不是 Vue 版的 useShow）
import { useLaunch, useDidShow } from '@tarojs/taro';

import { initAuth } from './services/auth';
import { track } from './utils/track';

import './app.scss';

function App() {
  useLaunch(() => {
    // 冷启动：静默登录 + 初始化埋点
    initAuth();
    track('app_launch', {});
  });

  useDidShow(() => {
    track('app_show', {});
  });

  return null;
}

export default App;
