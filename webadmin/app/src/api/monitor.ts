import api from './client';

export type LogName = 'frps' | 'frpc' | 'dashboard';

// 日志响应
export interface LogResponse {
  lines: string[];
  truncated: boolean;
}

// 获取历史日志
export async function getLogs(name: LogName): Promise<LogResponse> {
  const { data } = await api.get<LogResponse>(`/monitor/logs/${name}`);
  return data;
}

// 下载日志（返回文本）
export async function downloadLogs(name: LogName): Promise<string> {
  const { data } = await api.get<string>(`/monitor/logs/${name}/download`, {
    responseType: 'text',
    transformResponse: (d) => d,
  });
  return data;
}

// 清空日志
export async function clearLogs(name: LogName): Promise<void> {
  await api.delete(`/monitor/logs/${name}`);
}

// 获取实时统计
export async function getStats(): Promise<Record<string, unknown>> {
  const { data } = await api.get<Record<string, unknown>>('/monitor/stats');
  return data;
}
