import { create } from 'zustand';
import * as authApi from '../api/auth';
import { setToken, clearToken, getToken } from '../api/client';
import type { UserInfo } from '../api/auth';

// auth store 状态
interface AuthState {
  token: string | null;
  user: UserInfo | null;
  loading: boolean;
  // 登录
  login: (username: string, password: string) => Promise<LoginResult>;
  // 登出
  logout: () => Promise<void>;
  // 获取当前用户信息
  fetchMe: () => Promise<void>;
  // 刷新 token
  refresh: () => Promise<void>;
  // 修改密码后更新本地状态
  markPasswordChanged: () => void;
}

interface LoginResult {
  mustChangePassword: boolean;
}

// zustand auth store：token 持久化到 localStorage
export const useAuthStore = create<AuthState>((set) => ({
  token: getToken(),
  user: null,
  loading: false,

  login: async (username, password) => {
    const resp = await authApi.login(username, password);
    setToken(resp.access_token);
    set({ token: resp.access_token });
    // 登录后获取用户信息
    try {
      const me = await authApi.getMe();
      set({ user: me });
    } catch {
      // 获取用户信息失败不阻断登录流程
    }
    return { mustChangePassword: !!resp.must_change_password };
  },

  logout: async () => {
    try {
      await authApi.logout();
    } catch {
      // 忽略登出接口错误
    }
    clearToken();
    set({ token: null, user: null });
  },

  fetchMe: async () => {
    try {
      const me = await authApi.getMe();
      set({ user: me });
    } catch {
      clearToken();
      set({ token: null, user: null });
    }
  },

  refresh: async () => {
    const resp = await authApi.refresh();
    setToken(resp.access_token);
    set({ token: resp.access_token });
  },

  markPasswordChanged: () => {
    set((state) => ({
      user: state.user ? { ...state.user, must_change_password: false } : null,
    }));
  },
}));
