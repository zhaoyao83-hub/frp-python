import React from 'react';
import { Routes, Route, Navigate } from 'react-router-dom';
import { useAuthStore } from './store/auth';
import Layout from './components/Layout';
import Login from './pages/Login';
import Dashboard from './pages/Dashboard';
import ConfigServer from './pages/ConfigServer';
import ConfigClient from './pages/ConfigClient';
import Service from './pages/Service';
import MonitorLogs from './pages/MonitorLogs';
import MonitorConnections from './pages/MonitorConnections';
import MonitorProxies from './pages/MonitorProxies';
import PortMapping from './pages/PortMapping';
import Users from './pages/Users';
import FileManager from './pages/FileManager';
import RemoteManagement from './pages/RemoteManagement';

// 登录守卫：无 token 跳 /login
const ProtectedLayout: React.FC = () => {
  const token = useAuthStore((s) => s.token);
  if (!token) {
    return <Navigate to="/login" replace />;
  }
  // must_change_password 时由 Layout 内的 ChangePassword 强制弹窗处理
  return <Layout />;
};

// 路由配置
const AppRoutes: React.FC = () => {
  return (
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route path="/" element={<ProtectedLayout />}>
        <Route index element={<Dashboard />} />
        <Route path="config/server" element={<ConfigServer />} />
        <Route path="config/client" element={<ConfigClient />} />
        <Route path="proxies" element={<PortMapping />} />
        <Route path="service" element={<Service />} />
        <Route path="monitor/logs" element={<MonitorLogs />} />
        <Route path="monitor/connections" element={<MonitorConnections />} />
        <Route path="monitor/proxies" element={<MonitorProxies />} />
        <Route path="users" element={<Users />} />
        <Route path="files" element={<FileManager />} />
        <Route path="remote" element={<RemoteManagement />} />
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
};

export default AppRoutes;
