import axios, { AxiosError } from 'axios';

// token 在 localStorage 中的键名
export const TOKEN_KEY = 'myfrp_token';

// 从 localStorage 读取 token
export function getToken(): string | null {
  return localStorage.getItem(TOKEN_KEY);
}

// 保存 token 到 localStorage
export function setToken(token: string): void {
  localStorage.setItem(TOKEN_KEY, token);
}

// 清除 token
export function clearToken(): void {
  localStorage.removeItem(TOKEN_KEY);
}

// axios 实例
const api = axios.create({
  baseURL: '/api',
  timeout: 15000,
});

// 请求拦截器：附加 Authorization 头
api.interceptors.request.use(
  (config) => {
    const token = getToken();
    if (token) {
      config.headers.Authorization = `Bearer ${token}`;
    }
    return config;
  },
  (error) => Promise.reject(error)
);

// 响应拦截器：401 清 token 跳登录
api.interceptors.response.use(
  (response) => response,
  (error: AxiosError) => {
    if (error.response?.status === 401) {
      clearToken();
      // 避免在登录页循环跳转
      if (!window.location.pathname.startsWith('/login')) {
        window.location.href = '/login';
      }
    }
    return Promise.reject(error);
  }
);

export default api;
