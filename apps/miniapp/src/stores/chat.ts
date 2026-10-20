/**
 * 跨页面的「待进入工具」状态。
 *
 * 为什么需要它：微信小程序的 tabBar 页面只能用 `switchTab` 跳转，
 * 而 `switchTab` **不支持携带 query 参数**（`url` 里的 `?tool_id=xxx` 会被丢弃）。
 * 所以首页点「心绪导航」时没法用 `/pages/chat/index?tool_id=xxx` 直接带参。
 *
 * 方案：首页把工具写进 store，再 `switchTab` 到对话页；
 * 对话页在 `useShow` 里消费并清空。
 * 一次性消费（清空）是关键——否则下次切回对话页会重复注入旧的工具。
 */

import { create } from 'zustand';

export interface PendingTool {
  id: string;
  name: string;
  /** 预填给用户的开场白。 */
  starterPrompt: string;
}

interface ChatStore {
  /** 待带入对话页的工具，null 表示普通对话。 */
  pendingTool: PendingTool | null;
  setPendingTool: (tool: PendingTool | null) => void;
  /** 消费后清空，避免重复注入。 */
  consumePendingTool: () => PendingTool | null;
}

export const useChatStore = create<ChatStore>((set, get) => ({
  pendingTool: null,
  setPendingTool: (pendingTool) => set({ pendingTool }),
  consumePendingTool: () => {
    const tool = get().pendingTool;
    if (tool) set({ pendingTool: null });
    return tool;
  },
}));
