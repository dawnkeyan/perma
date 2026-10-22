/**
 * 首页（今天）。
 *
 * 结构：
 *   1. 问候 + 今日状态速览（来自情绪打卡）
 *   2. 工具网格 —— 「心绪导航」等工具卡片
 *
 * 工具卡片的数据来自后端 `GET /v1/tools`（不是前端硬编码）：
 * 产品改一句工具简介不需要发版，前端只负责渲染。
 * 后端按「当前情绪」返回的顺序即推荐顺序（推荐的工具排最前）。
 */

import { useCallback, useEffect, useState } from 'react';
import { View, Text } from '@tarojs/components';
import Taro, { usePullDownRefresh } from '@tarojs/taro';

import { api, EMOTIONS, type Emotion, type Tool } from '../../services/api';
import { useChatStore } from '../../stores/chat';
import { track } from '../../utils/track';

/**
 * 服务端下发的语义图标名 -> 客户端展示符号。
 * 服务端只给 key，不给图片资源（图片资源随小程序包体走，不适合塞进接口）。
 */
const ICONS: Record<string, string> = {
  compass: '🧭',
  sprout: '🌱',
  sun: '☀️',
  wave: '🌊',
  target: '🎯',
};

function iconOf(key: string): string {
  return ICONS[key] ?? '✨';
}

/** 把后端返回的情绪字符串收敛成前端的 Emotion 联合类型。 */
function normalizeEmotion(raw?: string | null): Emotion | undefined {
  if (!raw) return undefined;
  return EMOTIONS.find((item) => item.key === raw)?.key;
}

function greeting(): string {
  const hour = new Date().getHours();
  if (hour < 5) return '夜深了';
  if (hour < 11) return '早上好';
  if (hour < 14) return '中午好';
  if (hour < 18) return '下午好';
  return '晚上好';
}

export default function IndexPage() {
  const [tools, setTools] = useState<Tool[]>([]);
  const [emotion, setEmotion] = useState<Emotion | undefined>();
  const [intensity, setIntensity] = useState<number | null>(null);
  const [activeDays, setActiveDays] = useState(0);
  const [hasRecord, setHasRecord] = useState(false);
  const [loading, setLoading] = useState(true);
  const [expanded, setExpanded] = useState<string | null>(null);

  const setPendingTool = useChatStore((state) => state.setPendingTool);

  const load = useCallback(async () => {
    try {
      // 先取情绪时间线：首页要展示「最近一次状态」，同时它也是工具推荐依据
      const timeline = await api.journal.timeline();
      const latest = timeline.items[0];
      const current = normalizeEmotion(latest?.dominant_emotion);

      setEmotion(current);
      setIntensity(latest ? Number(latest.avg_intensity) : null);
      setActiveDays(timeline.stats.active_days);
      setHasRecord(timeline.items.length > 0);

      // 再按当前情绪取工具（后端负责把推荐的排前面）
      const list = await api.tools.list(current);
      setTools(list);
    } catch {
      // 首页不因为后端不可用就白屏：工具列表为空也允许用户直接进入对话
      setTools([]);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    track('page_view', { page: 'index' });
    load();
  }, [load]);

  usePullDownRefresh(() => {
    load().finally(() => Taro.stopPullDownRefresh());
  });

  /**
   * 进入工具。
   *
   * 对话页在 tabBar 里，`switchTab` 不能带 query，
   * 所以工具 id 通过 store 传递（见 stores/chat.ts 的说明）。
   */
  const enterTool = (tool: Tool) => {
    setPendingTool({
      id: tool.id,
      name: tool.name,
      starterPrompt: tool.starter_prompt,
    });
    track('tool_enter', { tool_id: tool.id });
    Taro.switchTab({ url: tool.entry });
  };

  const emotionLabel = emotion
    ? EMOTIONS.find((item) => item.key === emotion)?.label
    : undefined;
  const emotionEmoji = emotion
    ? EMOTIONS.find((item) => item.key === emotion)?.emoji
    : undefined;

  return (
    <View className="home-page">
      <View className="section">
        <Text className="home-greeting">{greeting()}</Text>
        <Text className="home-sub">
          {hasRecord
            ? '记录一下此刻，再选一个想试试的方向。'
            : '先记录此刻的感受，之后我们才知道从哪一步开始。'}
        </Text>
      </View>

      <View className="section">
        <View className="home-status-card">
          <View className="home-status-main">
            <Text className="home-status-emoji">{emotionEmoji ?? '🫧'}</Text>
            <View className="home-status-text">
              <Text className="home-status-title">
                {hasRecord ? `最近一次：${emotionLabel ?? '已记录'}` : '还没有记录'}
              </Text>
              <Text className="home-status-sub">
                {hasRecord && intensity !== null
                  ? `强度 ${intensity} · 近 7 天记录 ${activeDays} 天`
                  : '去「记录」页签写下第一个瞬间'}
              </Text>
            </View>
          </View>
          <View
            className="home-status-link"
            onClick={() => Taro.switchTab({ url: '/pages/journal/index' })}
          >
            <Text className="home-status-link-text">
              {hasRecord ? '再记一次' : '去记录'}
            </Text>
          </View>
        </View>
      </View>

      <View className="section">
        <Text className="section-title">想做点什么？</Text>
        <Text className="section-desc">
          每个工具都是一条固定的引导流程，进来之前你能先看到它会做什么。
        </Text>

        {loading && <Text className="section-desc">正在加载…</Text>}

        {!loading && tools.length === 0 && (
          <View className="tool-empty" onClick={() => Taro.switchTab({ url: '/pages/chat/index' })}>
            <Text className="tool-empty-text">工具暂时拿不到，直接开始对话吧</Text>
          </View>
        )}

        {tools.map((tool) => (
          <View key={tool.id} className="tool-card">
            <View className="tool-card-head" onClick={() => enterTool(tool)}>
              <Text className="tool-icon">{iconOf(tool.icon)}</Text>
              <View className="tool-card-title-wrap">
                <Text className="tool-name">{tool.name}</Text>
                <Text className="tool-desc">{tool.description}</Text>
              </View>
              <Text className="tool-arrow">›</Text>
            </View>

            <View
              className="tool-steps-toggle"
              onClick={() => setExpanded(expanded === tool.id ? null : tool.id)}
            >
              <Text className="tool-steps-toggle-text">
                {expanded === tool.id ? '收起流程' : `查看流程（${tool.steps.length} 步）`}
              </Text>
            </View>

            {expanded === tool.id && (
              <View className="tool-steps">
                {tool.steps.map((step, index) => (
                  <View key={step.title} className="tool-step">
                    <Text className="tool-step-index">{index + 1}</Text>
                    <View className="tool-step-body">
                      <Text className="tool-step-title">{step.title}</Text>
                      <Text className="tool-step-hint">{step.hint}</Text>
                    </View>
                  </View>
                ))}
              </View>
            )}
          </View>
        ))}
      </View>
    </View>
  );
}
