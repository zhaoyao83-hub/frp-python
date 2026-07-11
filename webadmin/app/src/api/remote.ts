import request from './client';

export interface ClientInfo {
  session_id: string;
  client_name: string;
  hostname: string;
  os: string;
  os_version: string;
  arch: string;
  client_version: string;
  ip: string;
  login_time: number;
  last_heartbeat: number;
  proxy_count: number;
}

export interface ClientsResponse {
  clients: ClientInfo[];
  frps_running: boolean;
}

export interface CmdResult {
  success: boolean;
  error?: string;
  [key: string]: any;
}

export async function listClients(): Promise<ClientsResponse> {
  const resp = await request.get('/remote/clients');
  return resp.data;
}

export async function sendRemoteCmd(
  sessionId: string,
  cmd: string,
  args: Record<string, any> = {},
  timeout = 30,
): Promise<CmdResult> {
  const resp = await request.post(
    `/remote/clients/${sessionId}/cmd`,
    { cmd, args, timeout },
  );
  return resp.data;
}
