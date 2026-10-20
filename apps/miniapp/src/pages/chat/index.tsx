import { useEffect, useRef, useState } from 'react';
import { View, Text, Input, Button, ScrollView } from '@tarojs/components';
import Taro from '@tarojs/taro';

import { api, streamChat, type Citation, type StreamEvent } from '../../services/api';
import { track } from '../../utils/track';

interface Message {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  citations?: Citation[];
  action?: string;
  streaming?: boolean;
}

const SESSION_KEY = 'lumina_session_id';

function getSessionId(): string {
  let id = Taro.getStorageSync(SESSION_KEY);
  if (!id) {
    id = `s_${Date.now().toString(36)}`;
    Taro.setStorageSync(SESSION_KEY, id);
  }
  return id;
}

export default function ChatPage() {
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState('');
  const [sending, setSending] = useState(false);
  const scrollRef = useRef<ScrollView>(null);
  const abortRef = useRef<(() => void) | null>(null);

  useEffect(() => {
    track('page_view', { page: 'chat' });
    return () => abortRef.current?.();
  }, []);

  const scrollToBottom = () => {
    setTimeout(() => scrollRef.current?.scrollTo({ scrollTop: 99999, duration: 200 }), 50);
  };

  const send = async () => {
    const text = input.trim();
    if (!text || sending) return;

    setInput('');
    setSending(true);

    const userMsg: Message = { id: `u_${Date.now()}`, role: 'user', content: text };
    const assistantId = `a_${Date.now()}`;
    setMessages((prev) => [
      ...prev,
      userMsg,
      { id: assistantId, role: 'assistant', content: '', streaming: true },
    ]);
    scrollToBottom();

    const started = Date.now();
    let firstTokenAt = 0;
    let ttft = 0;

    abortRef.current = streamChat(
      { session_id: getSessionId(), message: text },
      (event: StreamEvent) => {
        switch (event.type) {
          case 'delta': {
            if (!firstTokenAt) {
              firstTokenAt = Date.now();
              ttft = firstTokenAt - started;
            }
            const chunk = (event.data as { text: string }).text;
            setMessages((prev) =>
              prev.map((m) =>
                m.id === assistantId
                  ? { ...m, content: m.content + chunk }
                  : m,
              ),
            );
            scrollToBottom();
            break;
          }
          case 'citation': {
            const citation = event.data as Citation;
            setMessages((prev) =>
              prev.map((m) =>
                m.id === assistantId
                  ? { ...m, citations: [...(m.citations ?? []), citation] }
                  : m,
              ),
            );
            break;
          }
          case 'action': {
            const action = (event.data as { text: string }).text;
            setMessages((prev) =>
              prev.map((m) => (m.id === assistantId ? { ...m, action } : m)),
            );
            break;
          }
          case 'error': {
            const err = event.data as { code: string; message: string };
            Taro.showToast({ title: err.message, icon: 'none' });
            setMessages((prev) =>
              prev.map((m) =>
                m.id === assistantId
                  ? { ...m, streaming: false, content: m.content || `（${err.message}）` }
                  : m,
              ),
            );
            break;
          }
          default:
            break;
        }
      },
      () => {
        setSending(false);
        setMessages((prev) =>
          prev.map((m) => (m.id === assistantId ? { ...m, streaming: false } : m)),
        );
        track('chat_request', {
          ttft_ms: ttft,
          total_ms: Date.now() - started,
        });
      },
    );
  };

  const feedback = async (messageId: string, rating: number) => {
    try {
      await api.chat.feedback({ message_id: messageId, rating });
      track('chat_feedback', { message_id: messageId, rating });
      Taro.showToast({ title: '感谢反馈', icon: 'none' });
    } catch {
      Taro.showToast({ title: '反馈失败', icon: 'none' });
    }
  };

  return (
    <View className="chat-page">
      <ScrollView
        ref={scrollRef}
        className="chat-scroll"
        scrollY
        scrollWithAnimation
      >
        {messages.length === 0 && (
          <View className="chat-empty">
            <Text className="chat-empty-title">想聊点什么都可以</Text>
            <Text className="chat-empty-sub">
              今天的情绪、卡住的事、或者一个心理学方法
            </Text>
          </View>
        )}

        {messages.map((msg) => (
          <View key={msg.id} className={`msg msg-${msg.role}`}>
            {msg.role === 'assistant' && (
              <View className="msg-body">
                {msg.sections?.empathy ? null : null}
                <Text className="msg-text">
                  {msg.content || (msg.streaming ? '…' : '')}
                </Text>

                {msg.citations && msg.citations.length > 0 && (
                  <View className="msg-citations">
                    {msg.citations.map((c) => (
                      <View key={c.id} className="citation-chip">
                        <Text className="citation-index">[{c.id}]</Text>
                        <Text className="citation-title">{c.title}</Text>
                      </View>
                    ))}
                  </View>
                )}

                {msg.action && (
                  <View className="msg-action">
                    <Text className="msg-action-label">微行动</Text>
                    <Text className="msg-action-text">{msg.action}</Text>
                  </View>
                )}

                {!msg.streaming && msg.content && (
                  <View className="msg-feedback">
                    <Text
                      className="msg-feedback-btn"
                      onClick={() => feedback(msg.id, 5)}
                    >
                      有用
                    </Text>
                    <Text
                      className="msg-feedback-btn"
                      onClick={() => feedback(msg.id, 1)}
                    >
                      没帮上
                    </Text>
                  </View>
                )}
              </View>
            )}

            {msg.role === 'user' && (
              <View className="msg-bubble">
                <Text className="msg-text">{msg.content}</Text>
              </View>
            )}
          </View>
        ))}
      </ScrollView>

      <View className="chat-input-bar">
        <Input
          className="chat-input"
          value={input}
          placeholder="说说现在怎么样…"
          placeholderClass="chat-input-placeholder"
          confirmType="send"
          onInput={(e) => setInput(e.detail.value)}
          onConfirm={send}
        />
        <Button className="chat-send" onClick={send} disabled={!input.trim() || sending}>
          发送
        </Button>
      </View>
    </View>
  );
}
