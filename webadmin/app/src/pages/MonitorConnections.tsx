import React, { useEffect, useState, useCallback } from 'react';
import { Card, Table, Button, Tag, Modal } from 'antd';
import { ReloadOutlined } from '@ant-design/icons';
import { getConnections, type ConnectionItem } from '../api/dashboard';
import { formatBytes, formatTimestamp, formatDuration } from '../utils/format';

// 连接监控页：活跃连接列表，2s 轮询
const MonitorConnections: React.FC = () => {
  const [connections, setConnections] = useState<ConnectionItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [now, setNow] = useState(Date.now());

  const fetchConnections = useCallback(async () => {
    try {
      const data = await getConnections();
      setConnections(data || []);
    } catch (e) {
      // 静默
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchConnections();
    const timer = setInterval(fetchConnections, 2000);
    // 用于刷新持续时长
    const clock = setInterval(() => setNow(Date.now()), 1000);
    return () => {
      clearInterval(timer);
      clearInterval(clock);
    };
  }, [fetchConnections]);

  // 断开连接：后端未提供 API，提示暂不支持
  const handleDisconnect = (record: ConnectionItem) => {
    Modal.info({
      title: '暂不支持',
      content: `断开连接 ${record.conn_id} 功能暂未开放（后端未提供对应 API）。`,
      okText: '知道了',
    });
  };

  const columns = [
    {
      title: '连接 ID',
      dataIndex: 'conn_id',
      key: 'conn_id',
      ellipsis: true,
    },
    {
      title: '代理名称',
      dataIndex: 'proxy_name',
      key: 'proxy_name',
    },
    {
      title: '入流量',
      dataIndex: 'bytes_in',
      key: 'bytes_in',
      render: (v: number) => formatBytes(v),
    },
    {
      title: '出流量',
      dataIndex: 'bytes_out',
      key: 'bytes_out',
      render: (v: number) => formatBytes(v),
    },
    {
      title: '建立时间',
      dataIndex: 'created_at',
      key: 'created_at',
      render: (v: number | string | null) => formatTimestamp(v),
    },
    {
      title: '持续时长',
      key: 'duration',
      render: (_: unknown, record: ConnectionItem) => {
        if (!record.created_at) return '-';
        const ts = typeof record.created_at === 'number'
          ? (record.created_at > 1e12 ? record.created_at : record.created_at * 1000)
          : new Date(record.created_at).getTime();
        if (isNaN(ts)) return '-';
        return formatDuration((now - ts) / 1000);
      },
    },
    {
      title: '操作',
      key: 'action',
      render: (_: unknown, record: ConnectionItem) => (
        <Button size="small" danger onClick={() => handleDisconnect(record)}>
          断开
        </Button>
      ),
    },
  ];

  return (
    <Card
      title="连接监控"
      extra={
        <Button icon={<ReloadOutlined />} onClick={fetchConnections}>
          刷新
        </Button>
      }
    >
      <Table
        rowKey="conn_id"
        columns={columns}
        dataSource={connections}
        loading={loading}
        pagination={{ pageSize: 20, showSizeChanger: true }}
        size="middle"
        locale={{ emptyText: '暂无活跃连接' }}
      />
      <div style={{ marginTop: 8, color: '#999', fontSize: 12 }}>
        <Tag color="blue">每 2 秒自动刷新</Tag>
      </div>
    </Card>
  );
};

export default MonitorConnections;
