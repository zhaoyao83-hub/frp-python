import api from './client';

// 用户信息
export interface UserInfo {
  username: string;
  role: string;
  must_change_password?: boolean;
}

// 登录响应
export interface LoginResponse {
  access_token: string;
  token_type: string;
  expires_in: number;
  must_change_password?: boolean;
}

// 登录
export async function login(username: string, password: string): Promise<LoginResponse> {
  const { data } = await api.post<LoginResponse>('/auth/login', { username, password });
  return data;
}

// 登出
export async function logout(): Promise<void> {
  await api.post('/auth/logout');
}

// 刷新 token
export async function refresh(): Promise<{ access_token: string; expires_in: number }> {
  const { data } = await api.post('/auth/refresh');
  return data;
}

// 获取当前用户信息
export async function getMe(): Promise<UserInfo> {
  const { data } = await api.get<UserInfo>('/auth/me');
  return data;
}

// 创建用户（admin）
export async function createUser(
  username: string,
  password: string,
  role: string
): Promise<UserInfo> {
  const { data } = await api.post<UserInfo>('/auth/users', { username, password, role });
  return data;
}

// 用户列表（admin）
export async function listUsers(): Promise<UserInfo[]> {
  const { data } = await api.get<UserInfo[]>('/auth/users');
  return data;
}

// 删除用户（admin）
export async function deleteUser(username: string): Promise<void> {
  await api.delete(`/auth/users/${username}`);
}

// 修改密码
export async function changePassword(
  oldPassword: string,
  newPassword: string
): Promise<void> {
  await api.put('/auth/password', { old_password: oldPassword, new_password: newPassword });
}
