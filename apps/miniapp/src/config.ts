/**
 * 全局配置。
 *
 * 环境变量通过 Taro 的 define 插件注入，构建时会被静态替换。
 * 这样可以避免在运行时依赖 process.env（小程序环境没有 process）。
 */

declare const API_BASE_URL: string;
declare const APP_VERSION: string;
declare const ENV: string;

export const API_BASE = API_BASE_URL;
export const VERSION = APP_VERSION;
export const IS_PROD = ENV === 'production';

/** 埋点上报地址 */
export const TRACK_URL = `${API_BASE}/track/events`;

/** 业务常量 */
export const CONSTANTS = {
  /** 情绪打卡最短间隔（秒），用于防抖 */
  JOURNAL_DEBOUNCE_SECONDS: 3,
  /** 对话首字超时（毫秒），超过则提示重试 */
  CHAT_FIRST_TOKEN_TIMEOUT: 15000,
  /** 单条消息最大长度 */
  MAX_MESSAGE_LENGTH: 4000,
  /** 连续打卡徽章门槛 */
  STREAK_BADGES: [3, 7, 21, 100],
} as const;
