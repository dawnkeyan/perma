import { useEffect, useRef, useState, type ComponentRef } from 'react';
import { View, Text, Input, Button, ScrollView } from '@tarojs/components';
import Taro, { useDidShow } from '@tarojs/taro';

import { api, streamChat, type Citation, type StreamEvent } from '../../services/api';
import { useChatStore } from '../../stores/chat';
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
  /** 当前激活的工具；null = 普通对话。 */
  const [activeTool, setActiveTool] = useState<{ id: string; name: string } | null>(null);
  const scrollRef = useRef<ComponentRef<typeof ScrollView>>(null);
  const abortRef = useRef<(() => void) | null>(null);
  const consumePendingTool = useChatStore((state) => state.consumePendingTool);

  useEffect(() => {
    track('page_view', { page: 'chat' });
    return () => abortRef.current?.();
  }, []);

  /**
   * 消费首页传来的工具。
   *
   * 对话页在 tabBar 里，`switchTab` 不能带 query，
   * 所以首页把工具放进 store，这里在页面每次显示时取一次（取完即清）。
   */
  useDidShow(() => {
    const pending = consumePendingTool();
    if (!pending) return;
    setActiveTool({ id: pending.id, name: pending.name });
    // 预填开场白而不是直接发送：给用户一次「改一改再问」的机会
    setInput(pending.starterPrompt);
    track('tool_activated', { tool_id: pending.id });
  });

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
      {
        session_id: getSessionId(),
        message: text,
        // 工具 id 透传给服务端：由服务端决定检索侧重与上下文范围
        tool_id: activeTool?.id,
      },
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
            <Text className="chat-empty-title">
              {activeTool ? `正在使用「${activeTool.name}」` : '想聊点什么都可以'}
            </Text>
            <Text className="chat-empty-sub">
              {activeTool
                ? '开场白已经帮你填好，改成你自己的话再发送也可以'
                : '今天的情绪、卡住的事、或者一个心理学方法'}
            </Text>
          </View>
        )}

        {messages.map((msg) => (
          <View key={msg.id} className={`msg msg-${msg.role}`}>
            {msg.role === 'assistant' && (
              <View className="msg-body">
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

      {activeTool && (
        <View className="chat-tool-bar">
          <Text className="chat-tool-name">{activeTool.name}</Text>
          <Text className="chat-tool-exit" onClick={() => setActiveTool(null)}>
            退出工具
          </Text>
        </View>
      )}

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
