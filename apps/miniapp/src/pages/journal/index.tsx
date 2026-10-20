import { useState } from 'react';
import { View, Text, Button, Textarea, Slider } from '@tarojs/components';
import Taro from '@tarojs/taro';

import { api, EMOTIONS, TAGS, type Emotion } from '../../services/api';
import { track } from '../../utils/track';

export default function JournalPage() {
  const [emotion, setEmotion] = useState<Emotion | null>(null);
  const [intensity, setIntensity] = useState(3);
  const [tags, setTags] = useState<string[]>([]);
  const [content, setContent] = useState('');
  const [submitting, setSubmitting] = useState(false);

  const toggleTag = (tag: string) => {
    setTags((prev) => {
      if (prev.includes(tag)) return prev.filter((t) => t !== tag);
      if (prev.length >= 3) {
        Taro.showToast({ title: '最多选 3 个', icon: 'none' });
        return prev;
      }
      return [...prev, tag];
    });
  };

  const submit = async () => {
    if (!emotion || submitting) return;
    setSubmitting(true);
    try {
      await api.journal.create({
        emotion,
        intensity,
        tags,
        content: content.trim() || undefined,
      });
      track('journal_submit', { emotion, intensity, tags: tags.join(',') });
      Taro.showToast({ title: '已记录', icon: 'success' });
      setEmotion(null);
      setIntensity(3);
      setTags([]);
      setContent('');
    } catch {
      Taro.showToast({ title: '记录失败，请重试', icon: 'none' });
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <View className="journal-page">
      <View className="section">
        <Text className="section-title">此刻的你，是什么感觉？</Text>
        <View className="emotion-grid">
          {EMOTIONS.map((item) => (
            <View
              key={item.key}
              className={`emotion-item ${emotion === item.key ? 'is-active' : ''}`}
              onClick={() => setEmotion(item.key)}
            >
              <Text className="emotion-emoji">{item.emoji}</Text>
              <Text className="emotion-label">{item.label}</Text>
            </View>
          ))}
        </View>
      </View>

      <View className="section">
        <Text className="section-title">有多强烈？</Text>
        <View className="intensity-row">
          <Text className="intensity-label">轻</Text>
          <Slider
            className="intensity-slider"
            min={1}
            max={5}
            step={1}
            value={intensity}
            activeColor="#C87F5F"
            onChanging={(e) => setIntensity(e.detail.value)}
            onChange={(e) => setIntensity(e.detail.value)}
          />
          <Text className="intensity-label">强</Text>
          <Text className="intensity-value">{intensity}</Text>
        </View>
      </View>

      <View className="section">
        <Text className="section-title">和什么有关？（最多 3 个）</Text>
        <View className="tag-row">
          {TAGS.map((tag) => (
            <View
              key={tag}
              className={`tag-chip ${tags.includes(tag) ? 'is-active' : ''}`}
              onClick={() => toggleTag(tag)}
            >
              <Text className="tag-text">{tag}</Text>
            </View>
          ))}
        </View>
      </View>

      <View className="section">
        <Text className="section-title">发生了什么？（可留空）</Text>
        <Textarea
          className="journal-textarea"
          value={content}
          maxlength={500}
          placeholder="用你自己的话写一句就好"
          placeholderClass="textarea-placeholder"
          onInput={(e) => setContent(e.detail.value)}
        />
      </View>

      <Button
        className="primary-button"
        onClick={submit}
        disabled={!emotion || submitting}
      >
        {submitting ? '保存中…' : '保存这一刻'}
      </Button>
    </View>
  );
}
