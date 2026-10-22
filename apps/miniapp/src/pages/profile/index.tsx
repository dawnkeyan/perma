/**
 * 我的（profile）。
 *
 * 这个页面刻意不堆功能，只回答三个问题：
 *   1. 我的数据长什么样（记录了多少、什么强度）
 *   2. 这个应用是干什么的、边界在哪（不诊断 / 不替代 / 有行动）
 *   3. 我怎么退出登录、删掉我的数据
 *
 * 「边界说明」放在用户随手能点到的位置，是这类产品的基本责任：
 * 用户有权知道自己面对的是一个不会下诊断的工具。
 */

import { useEffect, useState } from 'react';
import { View, Text, Switch } from '@tarojs/components';
import Taro from '@tarojs/taro';

import { api, getToken } from '../../services/api';
import { logout } from '../../services/auth';
import { track } from '../../utils/track';

const REMINDER_KEY = 'lumina_reminder_enabled';

export default function ProfilePage() {
  const [activeDays, setActiveDays] = useState(0);
  const [totalEntries, setTotalEntries] = useState(0);
  const [reminder, setReminder] = useState(false);
  const [loggedIn, setLoggedIn] = useState<boolean>(() => Boolean(getToken()));

  useEffect(() => {
    track('page_view', { page: 'profile' });

    try {
      setReminder(Boolean(Taro.getStorageSync(REMINDER_KEY)));
    } catch {
      setReminder(false);
    }

    api.journal
      .timeline()
      .then((data) => {
        setActiveDays(data.stats.active_days);
        setTotalEntries(data.items.reduce((sum, item) => sum + item.entries, 0));
      })
      .catch(() => {
        // 拿不到统计不阻塞页面
      });
  }, []);

  const toggleReminder = (value: boolean) => {
    setReminder(value);
    try {
      Taro.setStorageSync(REMINDER_KEY, value);
    } catch {
      // 存储失败不影响使用
    }
    track('reminder_toggle', { enabled: value });
  };

  const handleLogout = () => {
    Taro.showModal({
      title: '退出登录',
      content: '退出后本地登录态会清除，未同步的记录可能无法找回。',
      confirmText: '退出',
      cancelText: '再想想',
      success: (res) => {
        if (!res.confirm) return;
        logout();
        setLoggedIn(false);
        Taro.showToast({ title: '已退出', icon: 'success' });
        track('logout', {});
      },
    });
  };

  const handleClear = () => {
    Taro.showModal({
      title: '清除本地缓存',
      content: '只清除这台设备上的缓存与偏好设置，不会删除服务端记录。',
      confirmText: '清除',
      success: (res) => {
        if (!res.confirm) return;
        try {
          Taro.removeStorageSync(REMINDER_KEY);
        } catch {
          // ignore
        }
        setReminder(false);
        Taro.showToast({ title: '已清除', icon: 'success' });
      },
    });
  };

  return (
    <View className="profile-page">
      <View className="section">
        <View className="profile-header">
          <Text className="profile-avatar">🌿</Text>
          <View className="profile-header-text">
            <Text className="profile-name">{loggedIn ? '我的账号' : '游客'}</Text>
            <Text className="profile-sub">
              {loggedIn ? '数据保存在你的账号下' : '登录后可以在多设备间同步记录'}
            </Text>
          </View>
        </View>
      </View>

      <View className="section">
        <Text className="section-title">我的数据</Text>
        <View className="profile-stat-row">
          <View className="profile-stat">
            <Text className="profile-stat-value">{activeDays}</Text>
            <Text className="profile-stat-label">近 7 天记录天数</Text>
          </View>
          <View className="profile-stat">
            <Text className="profile-stat-value">{totalEntries}</Text>
            <Text className="profile-stat-label">累计记录次数</Text>
          </View>
        </View>
      </View>

      <View className="section">
        <Text className="section-title">偏好</Text>
        <View className="profile-item">
          <View className="profile-item-text">
            <Text className="profile-item-title">每日记录提醒</Text>
            <Text className="profile-item-sub">提醒你花一分钟记录此刻</Text>
          </View>
          <Switch
            checked={reminder}
            color="#C87F5F"
            onChange={(e) => toggleReminder(e.detail.value)}
          />
        </View>
        <View className="profile-item" onClick={handleClear}>
          <View className="profile-item-text">
            <Text className="profile-item-title">清除本地缓存</Text>
            <Text className="profile-item-sub">清理本机的偏好与队列数据</Text>
          </View>
          <Text className="profile-arrow">›</Text>
        </View>
      </View>

      <View className="section">
        <Text className="section-title">关于拾光</Text>
        <View className="profile-notice">
          <Text className="profile-notice-item">
            · 拾光不做心理诊断，也不会判断你是否患有某种心理疾病
          </Text>
          <Text className="profile-notice-item">
            · 拾光不替代专业帮助；遇到危机时会直接建议你联系可信赖的人和专业机构
          </Text>
          <Text className="profile-notice-item">
            · 每条建议都会标注依据的知识条目，你可以逐条查看来源与证据等级
          </Text>
        </View>
        <View className="profile-item" onClick={handleLogout}>
          <View className="profile-item-text">
            <Text className="profile-item-title">退出登录</Text>
          </View>
          <Text className="profile-arrow">›</Text>
        </View>
      </View>
    </View>
  );
}
