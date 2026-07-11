import React, { useEffect, useState, useCallback } from 'react';
import { Row, Col, Card, Badge, Button, Space, Statistic, message, Spin } from 'antd';
import {
  PlayCircleOutlined,
  StopOutlined,
  ReloadOutlined,
} from '@ant-design/icons';
import {
  getStatus,
  startService,
  stopService,
  restartService,
  type ServiceName,
  type ServiceInfo,
} from '../api/service';
import { formatDuration } from '../utils/format';

// 状态到 Badge 状态的映射
const badgeStatusMap: Record<string, 'success' | 'default' | 'error'> = {
  running: 'success',
  stopped: 'default',
  error: 'error',
};

const statusTextMap: Record<string, string> = {
  running: '运行中',
  stopped: '已停止',
  error: '异常',
};

// 单个服务卡片
const ServiceCard: React.FC<{ name: ServiceName }> = ({ name }) => {
  const [info, setInfo] = useState<ServiceInfo | null>(null);
  const [loading, setLoading] = useState(true);
  const [operating, setOperating] = useState(false);

  const fetchStatus = useCallback(async () => {
    try {
      const data = await getStatus(name);
      setInfo(data);
    } catch (e) {
      // 静默
    } finally {
      setLoading(false);
    }
  }, [name]);

  useEffect(() => {
    fetchStatus();
    const timer = setInterval(fetchStatus, 2000);
    return () => clearInterval(timer);
  }, [fetchStatus]);

  // 推断状态
  const inferStatus = (): string => {
    if (!info) return 'stopped';
    if (info.running) return 'running';
    // 有退出码且非 0 视为异常
    if (info.exit_code !== null && info.exit_code !== 0) return 'error';
    return 'stopped';
  };

  const status = inferStatus();

  const handleStart = async () => {
    setOperating(true);
    try {
      const res = await startService(name);
      message.success(res.message || `${name} 已启动`);
      fetchStatus();
    } catch (e: unknown) {
      const err = e as { response?: { data?: { detail?: string } } };
      message.error(err?.response?.data?.detail || `启动 ${name} 失败`);
    } finally {
      setOperating(false);
    }
  };

  const handleStop = async () => {
    setOperating(true);
    try {
      const res = await stopService(name);
      message.success(res.message || `${name} 已停止`);
      fetchStatus();
    } catch (e: unknown) {
      const err = e as { response?: { data?: { detail?: string } } };
      message.error(err?.response?.data?.detail || `停止 ${name} 失败`);
    } finally {
      setOperating(false);
    }
  };

  const handleRestart = async () => {
    setOperating(true);
    try {
      const res = await restartService(name);
      message.success(res.message || `${name} 已重启`);
      fetchStatus();
    } catch (e: unknown) {
      const err = e as { response?: { data?: { detail?: string } } };
      message.error(err?.response?.data?.detail || `重启 ${name} 失败`);
    } finally {
      setOperating(false);
    }
  };

  return (
    <Card
      title={
        <Space>
          <Badge status={badgeStatusMap[status]} />
          <span>{name === 'frps' ? 'frps 服务端' : 'frpc 客户端'}</span>
        </Space>
      }
      extra={<span style={{ color: status === 'running' ? '#52c41a' : '#8c8c8c' }}>{statusTextMap[status]}</span>}
    >
      <Spin spinning={loading}>
        <Row gutter={16}>
          <Col span={6}>
            <Statistic title="进程 PID" value={info?.pid ?? '-'} />
          </Col>
          <Col span={6}>
            <Statistic title="运行时长" value={formatDuration(info?.uptime ?? 0)} />
          </Col>
          <Col span={6}>
            <Statistic title="重启次数" value={info?.restart_count ?? 0} />
          </Col>
          <Col span={6}>
            <Statistic title="最后退出码" value={info?.exit_code ?? '-'} />
          </Col>
        </Row>
        <Space style={{ marginTop: 24 }}>
          <Button
            type="primary"
            icon={<PlayCircleOutlined />}
            onClick={handleStart}
            disabled={status === 'running'}
            loading={operating}
          >
            启动
          </Button>
          <Button
            danger
            icon={<StopOutlined />}
            onClick={handleStop}
            disabled={status !== 'running'}
            loading={operating}
          >
            停止
          </Button>
          <Button
            icon={<ReloadOutlined />}
            onClick={handleRestart}
            disabled={status !== 'running'}
            loading={operating}
          >
            重启
          </Button>
        </Space>
      </Spin>
    </Card>
  );
};

// 服务管理页
const Service: React.FC = () => {
  return (
    <Row gutter={[16, 16]}>
      <Col xs={24} lg={12}>
        <ServiceCard name="frps" />
      </Col>
      <Col xs={24} lg={12}>
        <ServiceCard name="frpc" />
      </Col>
    </Row>
  );
};

export default Service;
