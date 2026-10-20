import { useLaunch, useShow } from '@tarojs/taro';

import { initAuth } from './services/auth';
import { track } from './utils/track';

import './app.scss';

function App() {
  useLaunch(() => {
    // 冷启动：静默登录 + 初始化埋点
    initAuth();
    track('app_launch', {});
  });

  useShow(() => {
    track('app_show', {});
  });

  return null;
}

export default App;
