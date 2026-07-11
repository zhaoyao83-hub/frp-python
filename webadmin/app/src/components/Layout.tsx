import React, { useState, useEffect } from 'react';
import { Layout as AntLayout, Menu, Dropdown, Button, theme, Space } from 'antd';
import {
  DashboardOutlined,
  SettingOutlined,
  ControlOutlined,
  MonitorOutlined,
  UserOutlined,
  MenuFoldOutlined,
  MenuUnfoldOutlined,
  LogoutOutlined,
  KeyOutlined,
  FileTextOutlined,
  ApartmentOutlined,
  SwapOutlined,
  FolderOpenOutlined,
} from '@ant-design/icons';
import { Outlet, useNavigate, useLocation } from 'react-router-dom';
import { useAuthStore } from '../store/auth';
import ChangePassword from '../pages/ChangePassword';

const { Header, Sider, Content } = AntLayout;

// 主布局：顶栏（logo + 用户菜单）+ 侧栏（菜单）+ 内容区
const Layout: React.FC = () => {
  const [collapsed, setCollapsed] = useState(false);
  const [pwdOpen, setPwdOpen] = useState(false);
  const navigate = useNavigate();
  const location = useLocation();
  const { token: themeToken } = theme.useToken();

  const user = useAuthStore((s) => s.user);
  const token = useAuthStore((s) => s.token);
  const logout = useAuthStore((s) => s.logout);
  const fetchMe = useAuthStore((s) => s.fetchMe);
  const mustChangePassword = !!user?.must_change_password;

  useEffect(() => {
    if (token && !user) {
      fetchMe();
    }
  }, [token, user, fetchMe]);

  // 退出登录
  const handleLogout = async () => {
    await logout();
    navigate('/login', { replace: true });
  };

  // 用户下拉菜单
  const userMenu = {
    items: [
      {
        key: 'changePwd',
        icon: <KeyOutlined />,
        label: '修改密码',
      },
      { type: 'divider' as const },
      {
        key: 'logout',
        icon: <LogoutOutlined />,
        label: '退出登录',
      },
    ],
    onClick: ({ key }: { key: string }) => {
      if (key === 'changePwd') {
        setPwdOpen(true);
      } else if (key === 'logout') {
        handleLogout();
      }
    },
  };

  // 侧栏菜单项
  const menuItems = [
    { key: '/', icon: <DashboardOutlined />, label: '仪表盘' },
    { key: '/proxies', icon: <SwapOutlined />, label: '端口映射' },
    {
      key: 'config',
      icon: <SettingOutlined />,
      label: '配置管理',
      children: [
        { key: '/config/server', icon: <FileTextOutlined />, label: '服务端配置' },
        { key: '/config/client', icon: <FileTextOutlined />, label: '客户端配置' },
      ],
    },
    { key: '/service', icon: <ControlOutlined />, label: '服务管理' },
    {
      key: 'monitor',
      icon: <MonitorOutlined />,
      label: '监控',
      children: [
        { key: '/monitor/logs', icon: <FileTextOutlined />, label: '日志监控' },
        { key: '/monitor/connections', icon: <ApartmentOutlined />, label: '连接监控' },
        { key: '/monitor/proxies', icon: <ApartmentOutlined />, label: '代理监控' },
      ],
    },
    { key: '/users', icon: <UserOutlined />, label: '用户管理', role: 'admin' as const },
    { key: '/files', icon: <FolderOpenOutlined />, label: '文件管理', role: 'admin' as const },
  ];

  // 根据当前路径计算选中与展开的菜单
  const selectedKey = location.pathname;
  const openKeys: string[] = [];
  if (selectedKey.startsWith('/config')) openKeys.push('config');
  if (selectedKey.startsWith('/monitor')) openKeys.push('monitor');

  // 仅 admin 可见用户管理
  const filteredMenuItems = menuItems.filter((item) => {
    if ((item as any).role === 'admin') return user?.role === 'admin';
    if (item.key === '/users') return user?.role === 'admin';
    return true;
  });

  return (
    <AntLayout style={{ minHeight: '100vh' }}>
      <Sider
        trigger={null}
        collapsible
        collapsed={collapsed}
        theme="dark"
        width={220}
      >
        <div
          style={{
            height: 56,
            color: '#fff',
            fontWeight: 700,
            fontSize: collapsed ? 14 : 18,
            textAlign: 'center',
            lineHeight: '56px',
            whiteSpace: 'nowrap',
            overflow: 'hidden',
          }}
        >
          {collapsed ? 'FRP' : 'MyFRP 管理面板'}
        </div>
        <Menu
          theme="dark"
          mode="inline"
          selectedKeys={[selectedKey]}
          defaultOpenKeys={openKeys}
          items={filteredMenuItems}
          onClick={({ key }) => {
            if (key.startsWith('/')) navigate(key);
          }}
        />
      </Sider>
      <AntLayout>
        <Header
          style={{
            padding: '0 16px',
            background: themeToken.colorBgContainer,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
          }}
        >
          <Button
            type="text"
            onClick={() => setCollapsed(!collapsed)}
            icon={collapsed ? <MenuUnfoldOutlined /> : <MenuFoldOutlined />}
          />
          <Dropdown menu={userMenu} placement="bottomRight">
            <Space style={{ cursor: 'pointer' }}>
              <UserOutlined />
              <span>{user?.username ?? '未登录'}</span>
            </Space>
          </Dropdown>
        </Header>
        <Content style={{ margin: 16, padding: 24, background: themeToken.colorBgContainer, borderRadius: 8 }}>
          <Outlet />
        </Content>
      </AntLayout>

      {/* 修改密码弹窗：must_change_password 时强制且不可关闭 */}
      <ChangePassword
        open={pwdOpen || mustChangePassword}
        forced={mustChangePassword}
        onClose={() => setPwdOpen(false)}
      />
    </AntLayout>
  );
};

export default Layout;
