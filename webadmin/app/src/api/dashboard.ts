import api from './client';

// 单个服务状态
export interface ServiceStatus {
  name: string;
  running: boolean;
  pid: number | null;
  uptime: number;
  restart_count: number;
  exit_code: number | null;
  has_external_process: boolean;
  external_pids: number[];
}

// 仪表盘总览
export interface Overview {
  frps_status: ServiceStatus;
  frpc_status: ServiceStatus;
  total_proxies: number;
  current_connections: number;
  total_bytes_in: number;
  total_bytes_out: number;
}

// 代理项
export interface ProxyItem {
  name: string;
  type: string;
  remote_port: number | null;
  status: string;
  current_conns: number;
  total_conns: number;
  bytes_in: number;
  bytes_out: number;
  created_at: number | string | null;
}

// 连接项
export interface ConnectionItem {
  conn_id: string;
  proxy_name: string;
  bytes_in: number;
  bytes_out: number;
  created_at: number | string | null;
}

// 获取总览
export async function getOverview(): Promise<Overview> {
  const { data } = await api.get<Overview>('/dashboard/overview');
  return data;
}

// 获取代理列表
export async function getProxies(): Promise<ProxyItem[]> {
  const { data } = await api.get<ProxyItem[]>('/dashboard/proxies');
  return data;
}

// 获取活跃连接
export async function getConnections(): Promise<ConnectionItem[]> {
  const { data } = await api.get<ConnectionItem[]>('/dashboard/connections');
  return data;
}
