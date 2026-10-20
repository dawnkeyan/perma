/**
 * 回顾页（时间线）。
 *
 * 展示按日聚合的情绪轨迹：哪天记录了几次、平均强度多少、主要情绪是什么。
 * 数据来自 `GET /v1/journal/timeline`（服务端用 SQL 做聚合，前端不做统计）。
 *
 * 设计取舍：这里只做「看见」，不做「评价」。
 * 不写「你最近心情变差了」这类判断——判断属于教练链路，且需要证据支撑。
 */

import { useCallback, useEffect, useState } from 'react';
import { View, Text } from '@tarojs/components';
import Taro, { usePullDownRefresh } from '@tarojs/taro';

import { api, EMOTIONS } from '../../services/api';
import { track } from '../../utils/track';

interface DayItem {
  day: string;
  avg_intensity: number;
  entries: number;
  dominant_emotion: string;
}

interface TimelineStats {
  active_days: number;
  avg_intensity_7d: number;
}

/** 把 `YYYY-MM-DD` 显示成「今天 / 昨天 / N 天前 / M月D日」。 */
function formatDay(day: string): string {
  const target = new Date(`${day}T00:00:00`);
  if (Number.isNaN(target.getTime())) return day;

  const now = new Date();
  const startOfToday = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const diffDays = Math.round(
    (startOfToday.getTime() - target.getTime()) / (24 * 60 * 60 * 1000),
  );

  if (diffDays === 0) return '今天';
  if (diffDays === 1) return '昨天';
  if (diffDays > 1 && diffDays < 7) return `${diffDays} 天前`;
  return `${target.getMonth() + 1} 月 ${target.getDate()} 日`;
}

function emotionOf(key: string) {
  return EMOTIONS.find((item) => item.key === key);
}

export default function TimelinePage() {
  const [items, setItems] = useState<DayItem[]>([]);
  const [stats, setStats] = useState<TimelineStats>({ active_days: 0, avg_intensity_7d: 0 });
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    try {
      const data = await api.journal.timeline();
      setItems(data.items);
      setStats(data.stats);
    } catch {
      setItems([]);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    track('page_view', { page: 'timeline' });
    load();
  }, [load]);

  usePullDownRefresh(() => {
    load().finally(() => Taro.stopPullDownRefresh());
  });

  const totalEntries = items.reduce((sum, item) => sum + item.entries, 0);

  return (
    <View className="timeline-page">
      <View className="section">
        <Text className="section-title">这段时间的你</Text>
        <View className="timeline-stats">
          <View className="timeline-stat">
            <Text className="timeline-stat-value">{stats.active_days}</Text>
            <Text className="timeline-stat-label">近 7 天记录天数</Text>
          </View>
          <View className="timeline-stat">
            <Text className="timeline-stat-value">{totalEntries}</Text>
            <Text className="timeline-stat-label">累计记录次数</Text>
          </View>
          <View className="timeline-stat">
            <Text className="timeline-stat-value">
              {stats.avg_intensity_7d ? stats.avg_intensity_7d.toFixed(1) : '—'}
            </Text>
            <Text className="timeline-stat-label">近 7 天平均强度</Text>
          </View>
        </View>
      </View>

      <View className="section">
        <Text className="section-title">记录轨迹</Text>

        {loading && <Text className="section-desc">正在加载…</Text>}

        {!loading && items.length === 0 && (
          <View className="timeline-empty">
            <Text className="timeline-empty-text">还没有可回顾的记录</Text>
            <Text className="timeline-empty-sub">记录几天之后，这里会出现你的情绪轨迹</Text>
          </View>
        )}

        {items.map((item) => {
          const emotion = emotionOf(item.dominant_emotion);
          const percent = Math.max(0, Math.min(100, (item.avg_intensity / 5) * 100));
          return (
            <View key={item.day} className="timeline-row">
              <View className="timeline-row-head">
                <Text className="timeline-day">{formatDay(item.day)}</Text>
                <Text className="timeline-entries">{item.entries} 次</Text>
              </View>
              <View className="timeline-row-body">
                <Text className="timeline-emoji">{emotion?.emoji ?? '·'}</Text>
                <Text className="timeline-emotion">{emotion?.label ?? item.dominant_emotion}</Text>
                <View className="timeline-bar-wrap">
                  <View className="timeline-bar" style={{ width: `${percent}%` }} />
                </View>
                <Text className="timeline-intensity">{Number(item.avg_intensity).toFixed(1)}</Text>
              </View>
            </View>
          );
        })}
      </View>
    </View>
  );
}
