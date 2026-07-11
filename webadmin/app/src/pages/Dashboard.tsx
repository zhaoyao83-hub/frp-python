import React, { useEffect, useState, useRef } from 'react';
import { Row, Col, Card, Statistic, Spin } from 'antd';
import {
  ApartmentOutlined,
  LinkOutlined,
  ArrowDownOutlined,
  ArrowUpOutlined,
} from '@ant-design/icons';
import ReactECharts from 'echarts-for-react';
import StatusCard from '../components/StatusCard';
import type { StatusType } from '../components/StatusCard';
import { getOverview, type Overview } from '../api/dashboard';
import { formatBytes } from '../utils/format';

// 单个历史数据点
interface TrendPoint {
  time: number;
  bytesIn: number;
  bytesOut: number;
  conns: number;
}

// 仪表盘页：服务状态、总览数据、流量/连接趋势图
const Dashboard: React.FC = () => {
  const [overview, setOverview] = useState<Overview | null>(null);
  const [loading, setLoading] = useState(true);
  // 历史数据缓冲（最近 5 分钟 = 150 个点 @2s）
  const historyRef = useRef<TrendPoint[]>([]);
  const [, forceUpdate] = useState(0);
  const lastOverviewRef = useRef<Overview | null>(null);

  // 轮询获取总览数据
  const fetchOverview = async () => {
    try {
      const data = await getOverview();
      setOverview(data);
      setLoading(false);

      // 计算流量速率（与上次对比）
      const now = Date.now();
      const last = lastOverviewRef.current;
      const point: TrendPoint = {
        time: now,
        bytesIn: data.total_bytes_in,
        bytesOut: data.total_bytes_out,
        conns: data.current_connections,
      };
      if (last) {
        // 用速率替换累计值，便于展示趋势
        const dt = (now - historyRef.current[historyRef.current.length - 1]?.time || now) / 1000;
        if (dt > 0) {
          point.bytesIn = Math.max(0, (data.total_bytes_in - last.total_bytes_in) / dt);
          point.bytesOut = Math.max(0, (data.total_bytes_out - last.total_bytes_out) / dt);
        }
      }
      lastOverviewRef.current = data;
      historyRef.current.push(point);
      // 保留最近 150 个点
      if (historyRef.current.length > 150) {
        historyRef.current = historyRef.current.slice(-150);
      }
      forceUpdate((n) => n + 1);
    } catch (e) {
      // 静默错误，避免轮询刷屏
    }
  };

  useEffect(() => {
    fetchOverview();
    const timer = setInterval(fetchOverview, 2000);
    return () => clearInterval(timer);
  }, []);

  // 服务状态映射
  const mapStatus = (s: Overview['frps_status'] | undefined): StatusType => {
    if (!s) return 'stopped';
    if (s.running) return 'running';
    // 有 pid 但未运行视为异常，否则停止
    return s.pid !== null ? 'error' : 'stopped';
  };

  const refreshStatus = () => {
    fetchOverview();
  };

  // 流量趋势图配置
  const trafficOption = {
    title: { text: '流量趋势 (B/s)', left: 'center', textStyle: { fontSize: 14 } },
    tooltip: { trigger: 'axis' },
    legend: { data: ['入站', '出站'], bottom: 0 },
    grid: { left: 50, right: 20, top: 40, bottom: 40 },
    xAxis: {
      type: 'category',
      data: historyRef.current.map((p) => new Date(p.time).toLocaleTimeString()),
    },
    yAxis: { type: 'value', axisLabel: { formatter: (v: number) => formatBytes(v, 0) } },
    series: [
      {
        name: '入站',
        type: 'line',
        smooth: true,
        showSymbol: false,
        data: historyRef.current.map((p) => Math.round(p.bytesIn)),
        areaStyle: { opacity: 0.1 },
      },
      {
        name: '出站',
        type: 'line',
        smooth: true,
        showSymbol: false,
        data: historyRef.current.map((p) => Math.round(p.bytesOut)),
        areaStyle: { opacity: 0.1 },
      },
    ],
  };

  // 连接数趋势图配置
  const connOption = {
    title: { text: '连接数趋势', left: 'center', textStyle: { fontSize: 14 } },
    tooltip: { trigger: 'axis' },
    grid: { left: 50, right: 20, top: 40, bottom: 40 },
    xAxis: {
      type: 'category',
      data: historyRef.current.map((p) => new Date(p.time).toLocaleTimeString()),
    },
    yAxis: { type: 'value', minInterval: 1 },
    series: [
      {
        name: '当前连接',
        type: 'line',
        smooth: true,
        showSymbol: false,
        data: historyRef.current.map((p) => p.conns),
        areaStyle: { opacity: 0.1 },
        itemStyle: { color: '#1890ff' },
      },
    ],
  };

  if (loading && !overview) {
    return (
      <div style={{ textAlign: 'center', padding: 80 }}>
        <Spin size="large" />
      </div>
    );
  }

  return (
    <div>
      <Row gutter={[16, 16]}>
        <Col xs={24} sm={12}>
          <StatusCard
            name="frps 服务端"
            status={mapStatus(overview?.frps_status)}
            pid={overview?.frps_status?.pid ?? null}
            uptime={overview?.frps_status?.uptime ?? 0}
            hasExternalProcess={overview?.frps_status?.has_external_process ?? false}
            externalPids={overview?.frps_status?.external_pids ?? []}
            serviceName="frps"
            onStatusChanged={refreshStatus}
          />
        </Col>
        <Col xs={24} sm={12}>
          <StatusCard
            name="frpc 客户端"
            status={mapStatus(overview?.frpc_status)}
            pid={overview?.frpc_status?.pid ?? null}
            uptime={overview?.frpc_status?.uptime ?? 0}
            hasExternalProcess={overview?.frpc_status?.has_external_process ?? false}
            externalPids={overview?.frpc_status?.external_pids ?? []}
            serviceName="frpc"
            onStatusChanged={refreshStatus}
          />
        </Col>
      </Row>

      <Row gutter={[16, 16]} style={{ marginTop: 16 }}>
        <Col xs={12} sm={6}>
          <Card>
            <Statistic
              title="在线代理"
              value={overview?.total_proxies ?? 0}
              prefix={<ApartmentOutlined />}
            />
          </Card>
        </Col>
        <Col xs={12} sm={6}>
          <Card>
            <Statistic
              title="当前连接"
              value={overview?.current_connections ?? 0}
              prefix={<LinkOutlined />}
            />
          </Card>
        </Col>
        <Col xs={12} sm={6}>
          <Card>
            <Statistic
              title="总入流量"
              value={formatBytes(overview?.total_bytes_in ?? 0)}
              prefix={<ArrowDownOutlined />}
            />
          </Card>
        </Col>
        <Col xs={12} sm={6}>
          <Card>
            <Statistic
              title="总出流量"
              value={formatBytes(overview?.total_bytes_out ?? 0)}
              prefix={<ArrowUpOutlined />}
            />
          </Card>
        </Col>
      </Row>

      <Row gutter={[16, 16]} style={{ marginTop: 16 }}>
        <Col xs={24} lg={12}>
          <Card>
            <ReactECharts option={trafficOption} style={{ height: 300 }} />
          </Card>
        </Col>
        <Col xs={24} lg={12}>
          <Card>
            <ReactECharts option={connOption} style={{ height: 300 }} />
          </Card>
        </Col>
      </Row>
    </div>
  );
};

export default Dashboard;
