import api from './client';

export interface FileItem {
  name: string;
  path: string;
  is_dir: boolean;
  size: number;
  modified_at: number;
}

export interface FileListResponse {
  path: string;
  items: FileItem[];
  parent?: string | null;
}

export interface FileContentResponse {
  path: string;
  content: string;
  size: number;
}

export async function listFiles(path: string = ''): Promise<FileListResponse> {
  const { data } = await api.get<FileListResponse>('/files/list', { params: { path } });
  return data;
}

export async function getFileContent(path: string): Promise<FileContentResponse> {
  const { data } = await api.get<FileContentResponse>('/files/content', { params: { path } });
  return data;
}

export async function saveFileContent(path: string, content: string): Promise<{ message: string }> {
  const { data } = await api.put<{ message: string }>('/files/content', { content }, { params: { path } });
  return data;
}

export async function deleteFile(path: string): Promise<{ message: string }> {
  const { data } = await api.delete<{ message: string }>('/files/delete', { data: { path } });
  return data;
}

export async function renameFile(path: string, newName: string): Promise<{ message: string }> {
  const { data } = await api.post<{ message: string }>('/files/rename', { path, new_name: newName });
  return data;
}

export async function makeDir(path: string, dirName: string): Promise<{ message: string }> {
  const { data } = await api.post<{ message: string }>('/files/mkdir', { path, dir_name: dirName });
  return data;
}

export async function uploadFile(path: string, file: File): Promise<{ message: string }> {
  const formData = new FormData();
  formData.append('path', path);
  formData.append('file', file);
  const { data } = await api.post<{ message: string }>('/files/upload', formData, {
    headers: {
      'Content-Type': 'multipart/form-data',
    },
  });
  return data;
}

export function getDownloadUrl(path: string): string {
  const token = localStorage.getItem('myfrp_token');
  return `/api/files/download?path=${encodeURIComponent(path)}&token=${token}`;
}
