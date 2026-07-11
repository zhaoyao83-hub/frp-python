import React, { useEffect, useState, useCallback } from 'react';
import { Card, Table, Button, Tag } from 'antd';
import { ReloadOutlined } from '@ant-design/icons';
import { getProxies, type ProxyItem } from '../api/dashboard';
import { formatBytes, formatTimestamp } from '../utils/format';

// 代理监控页：代理列表、流量、连接数，2s 轮询
const MonitorProxies: React.FC = () => {
  const [proxies, setProxies] = useState<ProxyItem[]>([]);
  const [loading, setLoading] = useState(true);

  const fetchProxies = useCallback(async () => {
    try {
      const data = await getProxies();
      setProxies(data || []);
    } catch (e) {
      // 静默
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchProxies();
    const timer = setInterval(fetchProxies, 2000);
    return () => clearInterval(timer);
  }, [fetchProxies]);

  // 状态颜色映射
  const statusColor = (status: string): string => {
    switch (status.toLowerCase()) {
      case 'running':
      case 'online':
      case 'active':
        return 'green';
      case 'error':
      case 'failed':
        return 'red';
      default:
        return 'default';
    }
  };

  const columns = [
    {
      title: '名称',
      dataIndex: 'name',
      key: 'name',
    },
    {
      title: '类型',
      dataIndex: 'type',
      key: 'type',
    },
    {
      title: '远程端口',
      dataIndex: 'remote_port',
      key: 'remote_port',
      render: (v: number | null) => v ?? '-',
    },
    {
      title: '状态',
      dataIndex: 'status',
      key: 'status',
      render: (v: string) => <Tag color={statusColor(v)}>{v}</Tag>,
    },
    {
      title: '当前连接',
      dataIndex: 'current_conns',
      key: 'current_conns',
    },
    {
      title: '总连接',
      dataIndex: 'total_conns',
      key: 'total_conns',
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
      title: '创建时间',
      dataIndex: 'created_at',
      key: 'created_at',
      render: (v: number | string | null) => formatTimestamp(v),
    },
  ];

  return (
    <Card
      title="代理监控"
      extra={
        <Button icon={<ReloadOutlined />} onClick={fetchProxies}>
          刷新
        </Button>
      }
    >
      <Table
        rowKey="name"
        columns={columns}
        dataSource={proxies}
        loading={loading}
        pagination={{ pageSize: 20, showSizeChanger: true }}
        size="middle"
        locale={{ emptyText: '暂无代理' }}
      />
      <div style={{ marginTop: 8 }}>
        <Tag color="blue">每 2 秒自动刷新</Tag>
      </div>
    </Card>
  );
};

export default MonitorProxies;
