import { useEffect, useRef, useCallback, useState } from 'react';
import { getToken } from '../api/client';

interface UseWebSocketOptions {
  // 是否启用（默认 true）
  enabled?: boolean;
  // 是否自动重连（默认 true）
  autoReconnect?: boolean;
  // 最大重连次数（默认无限）
  maxRetries?: number;
}

interface UseWebSocketResult {
  // 连接状态
  ready: boolean;
  // 手动关闭
  close: () => void;
  // 手动重连
  reconnect: () => void;
}

// WebSocket hook：自动拼接 token、指数退避重连
// url 形如 '/ws/logs?name=frps'，token 会以 query 参数附加
export function useWebSocket(
  url: string | null,
  onMessage: (data: string) => void,
  options: UseWebSocketOptions = {}
): UseWebSocketResult {
  const { enabled = true, autoReconnect = true, maxRetries = Infinity } = options;
  const wsRef = useRef<WebSocket | null>(null);
  const retryCountRef = useRef(0);
  const retryTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const onMessageRef = useRef(onMessage);
  const [ready, setReady] = useState(false);

  // 保持 onMessage 最新引用
  useEffect(() => {
    onMessageRef.current = onMessage;
  }, [onMessage]);

  // 清理定时器
  const clearRetryTimer = useCallback(() => {
    if (retryTimerRef.current) {
      clearTimeout(retryTimerRef.current);
      retryTimerRef.current = null;
    }
  }, []);

  // 构造带 token 的完整 URL
  const buildUrl = useCallback((rawUrl: string): string => {
    const token = getToken();
    const separator = rawUrl.includes('?') ? '&' : '?';
    const urlWithToken = token ? `${rawUrl}${separator}token=${encodeURIComponent(token)}` : rawUrl;
    // 根据当前页面协议选择 ws/wss
    const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    return `${proto}//${window.location.host}${urlWithToken}`;
  }, []);

  // 连接
  const connect = useCallback(() => {
    if (!url) return;
    clearRetryTimer();
    // 关闭旧连接
    if (wsRef.current) {
      wsRef.current.onclose = null;
      wsRef.current.close();
      wsRef.current = null;
    }

    const fullUrl = buildUrl(url);
    const ws = new WebSocket(fullUrl);
    wsRef.current = ws;

    ws.onopen = () => {
      retryCountRef.current = 0;
      setReady(true);
    };

    ws.onmessage = (event) => {
      onMessageRef.current(typeof event.data === 'string' ? event.data : String(event.data));
    };

    ws.onerror = () => {
      // 错误后通常会触发 close
    };

    ws.onclose = () => {
      setReady(false);
      wsRef.current = null;
      if (autoReconnect && retryCountRef.current < maxRetries) {
        // 指数退避：1s, 2s, 4s, 8s... 上限 30s
        const delay = Math.min(1000 * Math.pow(2, retryCountRef.current), 30000);
        retryCountRef.current += 1;
        retryTimerRef.current = setTimeout(() => {
          connect();
        }, delay);
      }
    };
  }, [url, autoReconnect, maxRetries, buildUrl, clearRetryTimer]);

  useEffect(() => {
    if (!enabled) {
      if (wsRef.current) {
        wsRef.current.onclose = null;
        wsRef.current.close();
        wsRef.current = null;
        setReady(false);
      }
      clearRetryTimer();
      return;
    }
    connect();
    return () => {
      if (wsRef.current) {
        wsRef.current.onclose = null;
        wsRef.current.close();
        wsRef.current = null;
      }
      clearRetryTimer();
    };
  }, [enabled, connect, clearRetryTimer]);

  const close = useCallback(() => {
    if (wsRef.current) {
      wsRef.current.onclose = null;
      wsRef.current.close();
      wsRef.current = null;
      setReady(false);
    }
    clearRetryTimer();
  }, [clearRetryTimer]);

  const reconnect = useCallback(() => {
    retryCountRef.current = 0;
    connect();
  }, [connect]);

  return { ready, close, reconnect };
}
