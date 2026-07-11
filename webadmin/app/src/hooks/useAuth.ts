import { useEffect } from 'react';
import { useAuthStore } from '../store/auth';

// 鉴权 hook：封装 store，启动时若有 token 则拉取用户信息
export function useAuth() {
  const token = useAuthStore((s) => s.token);
  const user = useAuthStore((s) => s.user);
  const loading = useAuthStore((s) => s.loading);
  const login = useAuthStore((s) => s.login);
  const logout = useAuthStore((s) => s.logout);
  const fetchMe = useAuthStore((s) => s.fetchMe);

  // 有 token 但无用户信息时拉取
  useEffect(() => {
    if (token && !user) {
      fetchMe();
    }
  }, [token, user, fetchMe]);

  return {
    token,
    user,
    loading,
    isAuthenticated: !!token,
    isAdmin: user?.role === 'admin',
    mustChangePassword: !!user?.must_change_password,
    login,
    logout,
    fetchMe,
  };
}
