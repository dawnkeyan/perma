/**
 * 微信登录。
 *
 * 流程：wx.login -> code -> 后端 code2Session -> 自签 JWT -> 存本地
 * 静默失败不阻塞使用（游客态也能本地记录情绪）。
 */

import Taro from '@tarojs/taro';

import { api, getToken, setToken } from './api';

export async function initAuth(): Promise<boolean> {
  if (getToken()) return true;

  try {
    const { code } = await Taro.login();
    const res = await api.auth.wechat(code);
    setToken(res.access_token);
    return true;
  } catch {
    // 静默失败：游客态
    return false;
  }
}

export function logout(): void {
  Taro.removeStorageSync('lumina_token');
}
