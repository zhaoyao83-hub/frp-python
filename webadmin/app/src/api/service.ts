import api from './client';

export type ServiceName = 'frps' | 'frpc';

// 服务状态
export interface ServiceInfo {
  name: string;
  running: boolean;
  pid: number | null;
  uptime: number;
  restart_count: number;
  exit_code: number | null;
  has_external_process: boolean;
  external_pids: number[];
}

// 操作结果
export interface ServiceOpResult {
  name: string;
  pid?: number;
  message: string;
}

// 获取服务状态
export async function getStatus(name: ServiceName): Promise<ServiceInfo> {
  const { data } = await api.get<ServiceInfo>(`/service/${name}/status`);
  return data;
}

// 启动服务
export async function startService(name: ServiceName, killExisting = false): Promise<ServiceOpResult> {
  const { data } = await api.post<ServiceOpResult>(`/service/${name}/start`, null, {
    params: { kill_existing: killExisting },
  });
  return data;
}

// 停止服务
export async function stopService(name: ServiceName): Promise<ServiceOpResult> {
  const { data } = await api.post<ServiceOpResult>(`/service/${name}/stop`);
  return data;
}

// 重启服务
export async function restartService(name: ServiceName): Promise<ServiceOpResult> {
  const { data } = await api.post<ServiceOpResult>(`/service/${name}/restart`);
  return data;
}

// 清理外部遗留进程
export async function killExternalProcess(name: ServiceName): Promise<ServiceOpResult> {
  const { data } = await api.post<ServiceOpResult>(`/service/${name}/kill-external`);
  return data;
}
