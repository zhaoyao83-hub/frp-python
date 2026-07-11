import api from './client';

export type ProxyType = 'tcp' | 'udp' | 'http' | 'stcp' | 'stcp_visitor';

export interface ProxyConfig {
  name: string;
  type: ProxyType;
  local_ip: string;
  local_port?: number;
  remote_port?: number;
  enabled?: boolean;
  custom_domains?: string[];
  subdomain?: string;
  sk?: string;
  server_name?: string;
  bind_addr?: string;
  bind_port?: number;
  plugin?: string;
  plugin_params?: Record<string, unknown>;
}

export interface ProxyListResponse {
  proxies: ProxyConfig[];
  total: number;
}

export interface ProxySaveResponse {
  message: string;
  need_restart: boolean;
  proxy: ProxyConfig;
}

export async function listProxies(): Promise<ProxyListResponse> {
  const { data } = await api.get<ProxyListResponse>('/proxies');
  return data;
}

export async function getProxy(name: string): Promise<ProxyConfig> {
  const { data } = await api.get<ProxyConfig>(`/proxies/${name}`);
  return data;
}

export async function createProxy(proxy: ProxyConfig): Promise<ProxySaveResponse> {
  const { data } = await api.post<ProxySaveResponse>('/proxies', proxy);
  return data;
}

export async function updateProxy(
  name: string,
  proxy: Partial<ProxyConfig>
): Promise<ProxySaveResponse> {
  const { data } = await api.put<ProxySaveResponse>(`/proxies/${name}`, proxy);
  return data;
}

export async function deleteProxy(name: string): Promise<ProxySaveResponse> {
  const { data } = await api.delete<ProxySaveResponse>(`/proxies/${name}`);
  return data;
}
