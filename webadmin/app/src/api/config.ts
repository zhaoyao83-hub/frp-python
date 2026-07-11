import api from './client';

export type ConfigType = 'server' | 'client';

// 配置 schema 字段定义
export interface ConfigSchemaField {
  key: string;
  type: 'string' | 'number' | 'boolean' | 'select' | 'array';
  default?: unknown;
  required?: boolean;
  description?: string;
  options?: string[];
}

// 配置 + schema 响应
export interface ConfigWithSchema {
  config: Record<string, unknown>;
  schema: ConfigSchemaField[];
}

// 校验结果
export interface ValidateResult {
  valid: boolean;
  errors: string[];
}

// 保存结果
export interface SaveResult {
  message: string;
  need_restart: boolean;
}

// 读取配置（含 schema）
export async function getConfig(type: ConfigType): Promise<ConfigWithSchema> {
  const { data } = await api.get<ConfigWithSchema>(`/config/${type}`);
  return data;
}

// 保存配置
export async function saveConfig(
  type: ConfigType,
  config: Record<string, unknown>
): Promise<SaveResult> {
  const { data } = await api.put<SaveResult>(`/config/${type}`, config);
  return data;
}

// 校验配置
export async function validateConfig(
  type: ConfigType,
  config: Record<string, unknown>
): Promise<ValidateResult> {
  const { data } = await api.post<ValidateResult>(`/config/${type}/validate`, config);
  return data;
}

// 获取 schema
export async function getSchema(type: ConfigType): Promise<ConfigSchemaField[]> {
  const { data } = await api.get<ConfigSchemaField[]>(`/config/${type}/schema`);
  return data;
}

// 读取原始文本
export async function getRawConfig(type: ConfigType): Promise<string> {
  const { data } = await api.get<{ content: string }>(`/config/${type}/raw`);
  return data.content;
}

// 保存原始文本
export async function saveRawConfig(type: ConfigType, content: string): Promise<SaveResult> {
  const { data } = await api.put<SaveResult>(`/config/${type}/raw`, { content });
  return data;
}
