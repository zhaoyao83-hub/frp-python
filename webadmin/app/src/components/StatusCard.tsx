import React from 'react';
import { Card, Badge, Statistic, Row, Col, Alert, Button, Space } from 'antd';
import { WarningOutlined } from '@ant-design/icons';
import { formatDuration } from '../utils/format';
import type { ServiceName } from '../api/service';
import { killExternalProcess, startService } from '../api/service';

export type StatusType = 'running' | 'stopped' | 'error' | 'external';

interface StatusCardProps {
  name: string;
  status: StatusType;
  pid: number | null;
  uptime: number;
  hasExternalProcess?: boolean;
  externalPids?: number[];
  serviceName?: ServiceName;
  onStatusChanged?: () => void;
}

// 状态到徽标颜色的映射
const statusColorMap: Record<StatusType, 'success' | 'default' | 'error' | 'warning'> = {
  running: 'success',
  stopped: 'default',
  error: 'error',
  external: 'warning',
};

// 状态到中文文案的映射
const statusTextMap: Record<StatusType, string> = {
  running: '运行中',
  stopped: '已停止',
  error: '异常',
  external: '存在遗留进程',
};

// 状态卡片：展示服务运行状态、PID、运行时长
const StatusCard: React.FC<StatusCardProps> = ({
  name,
  status,
  pid,
  uptime,
  hasExternalProcess = false,
  externalPids = [],
  serviceName,
  onStatusChanged,
}) => {
  const displayStatus: StatusType = hasExternalProcess && status !== 'running' ? 'external' : status;

  const handleKillExternal = async () => {
    if (!serviceName) return;
    try {
      await killExternalProcess(serviceName);
      onStatusChanged?.();
    } catch (e: any) {
      // 错误已在调用处提示
    }
  };

  const handleStartWithClean = async () => {
    if (!serviceName) return;
    try {
      await startService(serviceName, true);
      onStatusChanged?.();
    } catch (e: any) {
      // 错误已在调用处提示
    }
  };

  return (
    <Card
      title={
        <span>
          <Badge status={statusColorMap[displayStatus]} />
          <span style={{ marginLeft: 8 }}>{name}</span>
        </span>
      }
      extra={
        <span style={{ color: displayStatus === 'running' ? '#52c41a' : displayStatus === 'external' ? '#faad14' : '#8c8c8c' }}>
          {statusTextMap[displayStatus]}
        </span>
      }
    >
      <Row gutter={16}>
        <Col span={12}>
          <Statistic title="进程 PID" value={pid ?? '-'} />
        </Col>
        <Col span={12}>
          <Statistic title="运行时长" value={formatDuration(uptime)} />
        </Col>
      </Row>

      {hasExternalProcess && (
        <Alert
          type="warning"
          showIcon
          icon={<WarningOutlined />}
          style={{ marginTop: 12 }}
          message={
            <span>
              检测到 {externalPids.length} 个遗留进程（PID: {externalPids.join(', ')}），
              可能是之前未正确关闭的实例，会导致端口占用和状态不一致
            </span>
          }
          description={
            <Space style={{ marginTop: 8 }}>
              <Button size="small" danger onClick={handleKillExternal}>
                清理遗留进程
              </Button>
              <Button size="small" type="primary" onClick={handleStartWithClean}>
                清理并启动
              </Button>
            </Space>
          }
        />
      )}
    </Card>
  );
};

export default StatusCard;
