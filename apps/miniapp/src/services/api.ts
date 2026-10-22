/**
 * API 客户端。
 *
 * 设计要点：
 * 1. 类型由后端契约派生，避免前后端类型漂移
 * 2. 统一错误处理：后端错误码 -> 可展示的文案
 * 3. SSE 流式解析（微信小程序不支持 EventSource，需要用 wx.request 的 enableChunked）
 */

import Taro from '@tarojs/taro';

import { API_BASE } from '../config';

export const TOKEN_KEY = 'lumina_token';

export interface ApiError {
  code: string;
  message: string;
  details?: Array<{ field: string; reason: string }>;
}

export class RequestError extends Error {
  code: string;
  details?: ApiError['details'];

  constructor(error: ApiError) {
    super(error.message);
    this.name = 'RequestError';
    this.code = error.code;
    this.details = error.details;
  }
}

export function getToken(): string {
  try {
    return Taro.getStorageSync(TOKEN_KEY) || '';
  } catch {
    return '';
  }
}

export function setToken(token: string): void {
  Taro.setStorageSync(TOKEN_KEY, token);
}

async function request<T>(
  path: string,
  options: {
    method?: 'GET' | 'POST' | 'PATCH' | 'DELETE';
    data?: unknown;
    timeout?: number;
  } = {},
): Promise<T> {
  const token = getToken();

  try {
    const res = await Taro.request({
      url: `${API_BASE}${path}`,
      method: options.method ?? 'GET',
      data: options.data as Taro.request.Option['data'],
      timeout: options.timeout ?? 15000,
      header: {
        'Content-Type': 'application/json',
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
      },
    });

    if (res.statusCode >= 200 && res.statusCode < 300) {
      return res.data as T;
    }

    const payload = (res.data as { error?: ApiError })?.error;
    throw new RequestError(
      payload ?? { code: 'UNKNOWN', message: `请求失败 (${res.statusCode})` },
    );
  } catch (err) {
    if (err instanceof RequestError) throw err;
    throw new RequestError({
      code: 'NETWORK_ERROR',
      message: '网络连接失败，请检查网络后重试',
    });
  }
}

// --------------------------------------------------------------------------
// 类型
// --------------------------------------------------------------------------
export type Emotion =
  | 'joy'
  | 'calm'
  | 'expectation'
  | 'gratitude'
  | 'fatigue'
  | 'anxiety'
  | 'sadness'
  | 'anger';

export const EMOTIONS: Array<{ key: Emotion; label: string; emoji: string }> = [
  { key: 'joy', label: '喜悦', emoji: '😊' },
  { key: 'calm', label: '平静', emoji: '😌' },
  { key: 'expectation', label: '期待', emoji: '✨' },
  { key: 'gratitude', label: '感动', emoji: '🥹' },
  { key: 'fatigue', label: '疲惫', emoji: '😪' },
  { key: 'anxiety', label: '焦虑', emoji: '😰' },
  { key: 'sadness', label: '低落', emoji: '😔' },
  { key: 'anger', label: '愤怒', emoji: '😤' },
];

export const TAGS = ['工作', '人际', '身体', '财务', '自我', '未来'];

export interface JournalEntry {
  id: string;
  emotion: Emotion;
  intensity: number;
  tags: string[];
  content?: string | null;
  occurred_at: string;
  created_at: string;
}

export interface Citation {
  id: number;
  chunk_id: string;
  title: string;
  score: number;
  evidence_level: number;
  quote: string;
  heading_path: string[];
}

export interface ChatResponse {
  message_id: string;
  content: string;
  sections: {
    empathy: string;
    insight: string;
    evidence: string;
    action: string;
  };
  citations: Citation[];
  usage: Record<string, number>;
  cost_cny: number;
  prompt_version: string;
  safety: Record<string, unknown>;
}

export interface ChatRequest {
  session_id: string;
  message: string;
  /**
   * 工具 id（如 `mood_navigation`）。
   *
   * 传了以后服务端会按该工具的检索侧重与上下文要求执行：
   * 「先识别状态，再匹配干预策略」这句话由后端保证，客户端只负责把用户
   * 引到正确的入口，不在本地复刻业务语义。
   */
  tool_id?: string;
  context?: {
    emotion_window_days?: number;
    include_profile?: boolean;
  };
  idempotency_key?: string;
}

// --------------------------------------------------------------------------
// 工具目录
// --------------------------------------------------------------------------

/** 工具流程中的一步（展示用）。 */
export interface ToolStep {
  title: string;
  hint: string;
}

/**
 * 首页工具卡片。
 *
 * 字段与后端 `GET /v1/tools` 的 `ToolOut` 一一对应，
 * 注意后端**不下发** `focus`（检索侧重），那是服务端内部策略。
 */
export interface Tool {
  id: string;
  name: string;
  description: string;
  /** 图标标识，服务端给的是语义名，前端映射成 emoji/图形。 */
  icon: string;
  /** 点击后的目标路由。 */
  entry: string;
  /** 预填给用户的开场白，用户可改后再发送。 */
  starter_prompt: string;
  steps: ToolStep[];
  /** 推荐该工具的情绪 key，用于首页置顶与标签展示。 */
  recommended_for: string[];
}

// --------------------------------------------------------------------------
// 接口
// --------------------------------------------------------------------------
export const api = {
  auth: {
    wechat: (code: string, nickname?: string, avatarUrl?: string) =>
      request<{ access_token: string; expires_in: number }>('/auth/wechat', {
        method: 'POST',
        data: { code, nickname, avatar_url: avatarUrl },
      }),
  },

  journal: {
    create: (payload: {
      emotion: Emotion;
      intensity: number;
      tags: string[];
      content?: string;
    }) => request<JournalEntry>('/journal/entries', { method: 'POST', data: payload }),

    timeline: (start?: string, end?: string) => {
      const params: string[] = [];
      if (start) params.push(`start=${encodeURIComponent(start)}`);
      if (end) params.push(`end=${encodeURIComponent(end)}`);
      const query = params.length ? `?${params.join('&')}` : '';
      return request<{
        items: Array<{
          day: string;
          avg_intensity: number;
          entries: number;
          dominant_emotion: string;
        }>;
        stats: { active_days: number; avg_intensity_7d: number };
      }>(`/journal/timeline${query}`);
    },
  },

  /**
   * 工具目录。
   *
   * 走接口而不是前端硬编码：产品改一句文案不需要发版，
   * 也避免前后端各维护一份工具清单导致漂移。
   */
  tools: {
    list: (emotion?: Emotion) => {
      const query = emotion ? `?emotion=${encodeURIComponent(emotion)}` : '';
      return request<Tool[]>(`/tools${query}`);
    },
  },

  chat: {
    send: (payload: ChatRequest) =>
      request<ChatResponse>('/chat/messages', { method: 'POST', data: payload }),

    feedback: (payload: {
      message_id: string;
      rating: number;
      reason_code?: string;
      comment?: string;
    }) => request<{ ok: boolean }>('/chat/feedback', { method: 'POST', data: payload }),
  },
};

// --------------------------------------------------------------------------
// SSE 流式对话
// --------------------------------------------------------------------------
export type StreamEvent =
  | { type: 'meta'; data: Record<string, unknown> }
  | { type: 'delta'; data: { text: string } }
  | { type: 'citation'; data: Citation }
  | { type: 'action'; data: { text: string } }
  | { type: 'done'; data: { message_id: string } }
  | { type: 'error'; data: ApiError };

/**
 * 流式对话。
 *
 * 微信小程序不支持 EventSource，用 wx.request 的 enableChunked 模式接收分块数据，
 * 手动按 SSE 帧解析（以 \n\n 分隔）。
 */
export function streamChat(
  payload: ChatRequest,
  onEvent: (event: StreamEvent) => void,
  onComplete?: () => void,
): () => void {
  const token = getToken();
  const task = Taro.request({
    url: `${API_BASE}/chat/stream`,
    method: 'POST',
    data: payload,
    header: {
      'Content-Type': 'application/json',
      'X-Accel-Buffering': 'no',
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    enableChunked: true,
    success: () => onComplete?.(),
    fail: () => {
      onEvent({
        type: 'error',
        data: { code: 'NETWORK_ERROR', message: '连接失败，请重试' },
      });
      onComplete?.();
    },
  });

  // SSE 解析状态：
  // - decoder 用 stream 模式解码，保证跨 chunk 的中文等多字节字符不被截断
  // - buffer 保存尚未遇到分隔符的半帧，等下一个 chunk 到达后接着解析
  //
  // 关键：只处理**新出现的**完整帧。
  // 早期实现每次都把累计内容重新切分，会把已处理过的帧再触发一次，
  // 表现为流式回复内容重复。
  const decoder = new TextDecoder('utf-8');
  let buffer = '';

  (task as unknown as { onChunkReceived: (cb: (r: { data: ArrayBuffer }) => void) => void }).onChunkReceived(
    (res) => {
      buffer += decoder.decode(new Uint8Array(res.data), { stream: true });
      const frames = buffer.split('\n\n');
      buffer = frames.pop() ?? '';

      for (const frame of frames) {
        const event = parseSseFrame(frame);
        if (event) onEvent(event);
      }
    },
  );

  return () => task.abort();
}

function parseSseFrame(frame: string): StreamEvent | null {
  let type = 'message';
  const dataLines: string[] = [];

  for (const line of frame.split('\n')) {
    if (line.startsWith('event:')) {
      type = line.slice(6).trim();
    } else if (line.startsWith('data:')) {
      dataLines.push(line.slice(5).trim());
    }
  }

  if (dataLines.length === 0) return null;

  try {
    const data = JSON.parse(dataLines.join('\n'));
    return { type, data } as StreamEvent;
  } catch {
    return null;
  }
}
