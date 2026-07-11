import React, { useEffect, useState, useRef, useCallback, useMemo } from 'react';
import { Card, Tabs, Space, Button, Input, Select, Tag, message, Tooltip } from 'antd';
import {
  PauseCircleOutlined,
  PlayCircleOutlined,
  DownloadOutlined,
  DeleteOutlined,
  LockOutlined,
  UnlockOutlined,
} from '@ant-design/icons';
import LogViewer from '../components/LogViewer';
import { useWebSocket } from '../hooks/useWebSocket';
import { getLogs, downloadLogs, clearLogs, type LogName } from '../api/monitor';

// 最大缓冲行数
const MAX_LINES = 2000;

// 日志监控页：实时日志流（WebSocket），支持筛选/暂停/下载
const MonitorLogs: React.FC = () => {
  const [activeName, setActiveName] = useState<LogName>('frps');
  const [rawLines, setRawLines] = useState<string[]>([]);
  const [paused, setPaused] = useState(false);
  const [level, setLevel] = useState<string>('ALL');
  const [keyword, setKeyword] = useState('');
  const [autoScroll, setAutoScroll] = useState(true);
  const pausedRef = useRef(false);

  // 同步 paused 到 ref（供 WebSocket 回调读取）
  useEffect(() => {
    pausedRef.current = paused;
  }, [paused]);

  // 加载历史日志
  const loadHistory = useCallback(async (name: LogName) => {
    try {
      const resp = await getLogs(name);
      setRawLines(resp.lines || []);
    } catch (e) {
      setRawLines([]);
    }
  }, []);

  // WebSocket 消息处理：追加日志行
  const handleWsMessage = useCallback((data: string) => {
    if (pausedRef.current) return;
    setRawLines((prev) => {
      const next = [...prev, data];
      return next.length > MAX_LINES ? next.slice(-MAX_LINES) : next;
    });
  }, []);

  // WebSocket 连接
  const wsUrl = `/ws/logs?name=${activeName}`;
  useWebSocket(wsUrl, handleWsMessage);

  // 切换 Tab 时重新加载历史
  useEffect(() => {
    loadHistory(activeName);
  }, [activeName, loadHistory]);

  // 级别 + 关键字过滤后的行
  const displayedLines = useMemo(() => {
    return rawLines.filter((line) => {
      if (level !== 'ALL') {
        const upper = line.toUpperCase();
        if (!upper.includes(level)) return false;
      }
      if (keyword && !line.toLowerCase().includes(keyword.toLowerCase())) {
        return false;
      }
      return true;
    });
  }, [rawLines, level, keyword]);

  // 下载日志
  const handleDownload = async () => {
    try {
      const text = await downloadLogs(activeName);
      const blob = new Blob([text], { type: 'text/plain;charset=utf-8' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `${activeName}.log`;
      a.click();
      URL.revokeObjectURL(url);
      message.success('日志已下载');
    } catch (e) {
      message.error('下载日志失败');
    }
  };

  // 清空日志
  const handleClear = async () => {
    try {
      await clearLogs(activeName);
      setRawLines([]);
      message.success('日志已清空');
    } catch (e) {
      message.error('清空日志失败');
    }
  };

  const tabItems = [
    { key: 'frps', label: 'frps' },
    { key: 'frpc', label: 'frpc' },
    { key: 'dashboard', label: 'dashboard' },
  ];

  return (
    <Card
      title="日志监控"
      extra={
        <Space>
          <Tag color={paused ? 'orange' : 'green'}>{paused ? '已暂停' : '实时'}</Tag>
          <span style={{ color: '#999', fontSize: 12 }}>共 {rawLines.length} 行</span>
        </Space>
      }
    >
      <Tabs
        activeKey={activeName}
        onChange={(key) => setActiveName(key as LogName)}
        items={tabItems}
      />
      <Space style={{ marginBottom: 12, flexWrap: 'wrap' }}>
        <Button
          icon={paused ? <PlayCircleOutlined /> : <PauseCircleOutlined />}
          onClick={() => setPaused(!paused)}
        >
          {paused ? '继续' : '暂停'}
        </Button>
        <Select
          value={level}
          onChange={setLevel}
          style={{ width: 120 }}
          options={[
            { label: '全部级别', value: 'ALL' },
            { label: 'INFO', value: 'INFO' },
            { label: 'WARNING', value: 'WARNING' },
            { label: 'ERROR', value: 'ERROR' },
          ]}
        />
        <Input.Search
          placeholder="关键字搜索"
          value={keyword}
          onChange={(e) => setKeyword(e.target.value)}
          style={{ width: 200 }}
          allowClear
        />
        <Tooltip title={autoScroll ? '取消自动滚动' : '自动滚动到底部'}>
          <Button
            icon={autoScroll ? <LockOutlined /> : <UnlockOutlined />}
            onClick={() => setAutoScroll(!autoScroll)}
          >
            {autoScroll ? '已锁定滚动' : '未锁定滚动'}
          </Button>
        </Tooltip>
        <Button icon={<DownloadOutlined />} onClick={handleDownload}>
          下载
        </Button>
        <Button danger icon={<DeleteOutlined />} onClick={handleClear}>
          清空
        </Button>
      </Space>
      <LogViewer lines={displayedLines} autoScroll={autoScroll} height={520} />
    </Card>
  );
};

export default MonitorLogs;
