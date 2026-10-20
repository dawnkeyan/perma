/**
 * 埋点。
 *
 * 设计：批量上报 + 失败重试 + 本地队列，避免弱网环境丢数据。
 * 埋点事件是 BadCase 归因与产品迭代的数据来源。
 */

import Taro from '@tarojs/taro';

import { TRACK_URL } from '../config';

const QUEUE_KEY = 'lumina_track_queue';
const FLUSH_INTERVAL = 10_000;
const MAX_QUEUE = 200;

interface TrackEvent {
  name: string;
  props: Record<string, unknown>;
  ts: number;
}

let timer: ReturnType<typeof setInterval> | null = null;

function readQueue(): TrackEvent[] {
  try {
    return Taro.getStorageSync(QUEUE_KEY) || [];
  } catch {
    return [];
  }
}

function writeQueue(queue: TrackEvent[]): void {
  try {
    Taro.setStorageSync(QUEUE_KEY, queue.slice(-MAX_QUEUE));
  } catch {
    // 存储失败不阻塞业务
  }
}

export function track(name: string, props: Record<string, unknown> = {}): void {
  const event: TrackEvent = { name, props, ts: Date.now() };
  const queue = readQueue();
  queue.push(event);
  writeQueue(queue);

  if (!timer) {
    timer = setInterval(flush, FLUSH_INTERVAL);
  }
  if (queue.length >= 20) {
    flush();
  }
}

export function flush(): void {
  const queue = readQueue();
  if (queue.length === 0) return;

  Taro.request({
    url: TRACK_URL,
    method: 'POST',
    data: { events: queue },
    header: { 'Content-Type': 'application/json' },
    success: () => writeQueue([]),
    fail: () => {
      // 保留队列，下次重试
    },
  });
}

export function trackPageView(page: string): void {
  track('page_view', { page });
}
