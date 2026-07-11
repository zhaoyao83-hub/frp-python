import React, { useState, useEffect, useCallback } from 'react';
import {
  Table,
  Button,
  Tag,
  Space,
  Card,
  Tabs,
  Modal,
  Input,
  List,
  message,
  Descriptions,
  Row,
  Col,
  Statistic,
  Empty,
  Spin,
  Alert,
  Typography,
  InputNumber,
} from 'antd';
import {
  DesktopOutlined,
  ReloadOutlined,
  SnippetsOutlined,
  FolderOpenOutlined,
  CameraOutlined,
  InfoCircleOutlined,
  CodeOutlined,
  ArrowUpOutlined,
  PlayCircleOutlined,
} from '@ant-design/icons';
import type { ColumnsType } from 'antd/es/table';
import { listClients, sendRemoteCmd, ClientInfo } from '../api/remote';

const { TextArea } = Input;
const { TabPane } = Tabs;

function formatTime(ts: number): string {
  if (!ts) return '-';
  const d = new Date(ts * 1000);
  return d.toLocaleString('zh-CN');
}

function formatDuration(seconds: number): string {
  if (!seconds) return '-';
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = Math.floor(seconds % 60);
  if (h > 0) return `${h}h ${m}m`;
  if (m > 0) return `${m}m ${s}s`;
  return `${s}s`;
}

function formatBytes(bytes: number): string {
  if (!bytes) return '0 B';
  const units = ['B', 'KB', 'MB', 'GB'];
  let i = 0;
  while (bytes >= 1024 && i < units.length - 1) {
    bytes /= 1024;
    i++;
  }
  return bytes.toFixed(2) + ' ' + units[i];
}

const RemoteManagement: React.FC = () => {
  const [clients, setClients] = useState<ClientInfo[]>([]);
  const [loading, setLoading] = useState(false);
  const [frpsRunning, setFrpsRunning] = useState(true);
  const [selectedClient, setSelectedClient] = useState<ClientInfo | null>(null);
  const [detailOpen, setDetailOpen] = useState(false);
  const [activeTab, setActiveTab] = useState('proxies');

  const [proxies, setProxies] = useState<any[]>([]);
  const [proxiesLoading, setProxiesLoading] = useState(false);

  const [currentPath, setCurrentPath] = useState('.');
  const [fileEntries, setFileEntries] = useState<any[]>([]);
  const [filesLoading, setFilesLoading] = useState(false);

  const [screenshotImg, setScreenshotImg] = useState<string>('');
  const [screenshotLoading, setScreenshotLoading] = useState(false);

  const [sysInfo, setSysInfo] = useState<any>(null);
  const [sysInfoLoading, setSysInfoLoading] = useState(false);

  const [shellCmd, setShellCmd] = useState('');
  const [shellOutput, setShellOutput] = useState('');
  const [shellLoading, setShellLoading] = useState(false);
  const [shellTimeout, setShellTimeout] = useState(30);

  const loadClients = useCallback(async () => {
    setLoading(true);
    try {
      const data = await listClients();
      setClients(data.clients || []);
      setFrpsRunning(data.frps_running !== false);
    } catch (e: any) {
      message.error('获取客户端列表失败: ' + (e?.message || e));
      setFrpsRunning(false);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadClients();
    const timer = setInterval(loadClients, 5000);
    return () => clearInterval(timer);
  }, [loadClients]);

  const openDetail = (client: ClientInfo) => {
    setSelectedClient(client);
    setDetailOpen(true);
    setActiveTab('proxies');
    setProxies([]);
    setFileEntries([]);
    setScreenshotImg('');
    setSysInfo(null);
    setShellOutput('');
  };

  const loadProxies = async () => {
    if (!selectedClient) return;
    setProxiesLoading(true);
    try {
      const result = await sendRemoteCmd(selectedClient.session_id, 'list_proxies');
      if (result.success) {
        setProxies(result.proxies || []);
      } else {
        message.error('获取代理列表失败: ' + result.error);
      }
    } catch (e: any) {
      message.error('请求失败: ' + (e?.message || e));
    } finally {
      setProxiesLoading(false);
    }
  };

  const loadFiles = async (path: string) => {
    if (!selectedClient) return;
    setFilesLoading(true);
    try {
      const result = await sendRemoteCmd(selectedClient.session_id, 'list_files', { path });
      if (result.success) {
        setFileEntries(result.entries || []);
        setCurrentPath(result.path || path);
      } else {
        message.error('获取文件列表失败: ' + result.error);
      }
    } catch (e: any) {
      message.error('请求失败: ' + (e?.message || e));
    } finally {
      setFilesLoading(false);
    }
  };

  const takeScreenshot = async () => {
    if (!selectedClient) return;
    setScreenshotLoading(true);
    try {
      const result = await sendRemoteCmd(selectedClient.session_id, 'screenshot', {}, 60);
      if (result.success && result.image) {
        setScreenshotImg('data:image/png;base64,' + result.image);
      } else {
        message.error('截图失败: ' + (result.error || '未知错误'));
      }
    } catch (e: any) {
      message.error('请求失败: ' + (e?.message || e));
    } finally {
      setScreenshotLoading(false);
    }
  };

  const loadSysInfo = async () => {
    if (!selectedClient) return;
    setSysInfoLoading(true);
    try {
      const result = await sendRemoteCmd(selectedClient.session_id, 'sys_info');
      if (result.success) {
        setSysInfo(result.info);
      } else {
        message.error('获取系统信息失败: ' + result.error);
      }
    } catch (e: any) {
      message.error('请求失败: ' + (e?.message || e));
    } finally {
      setSysInfoLoading(false);
    }
  };

  const runShellCmd = async () => {
    if (!selectedClient || !shellCmd.trim()) return;
    setShellLoading(true);
    try {
      const result = await sendRemoteCmd(
        selectedClient.session_id,
        'exec_shell',
        { command: shellCmd, timeout: shellTimeout },
        shellTimeout + 5,
      );
      if (result.success) {
        const output = [
          result.stdout ? `$ ${shellCmd}\n${result.stdout}` : '',
          result.stderr ? `\n[stderr]\n${result.stderr}` : '',
          `\n[exit code: ${result.returncode}]`,
        ].join('');
        setShellOutput(prev => prev + output + '\n');
      } else {
        setShellOutput(prev => prev + `[error] ${result.error}\n`);
      }
    } catch (e: any) {
      setShellOutput(prev => prev + `[error] 请求失败: ${e?.message || e}\n`);
    } finally {
      setShellLoading(false);
    }
  };

  const handleTabChange = (key: string) => {
    setActiveTab(key);
    if (key === 'proxies' && proxies.length === 0) {
      loadProxies();
    } else if (key === 'files' && fileEntries.length === 0) {
      loadFiles('.');
    } else if (key === 'sysinfo' && !sysInfo) {
      loadSysInfo();
    }
  };

  const columns: ColumnsType<ClientInfo> = [
    {
      title: '客户端名称',
      dataIndex: 'client_name',
      key: 'client_name',
      render: (text, record) => (
        <Space>
          <DesktopOutlined style={{ color: '#1890ff' }} />
          <span style={{ fontWeight: 500 }}>{text || record.hostname || '未命名'}</span>
        </Space>
      ),
    },
    {
      title: '主机名',
      dataIndex: 'hostname',
      key: 'hostname',
    },
    {
      title: '系统',
      dataIndex: 'os',
      key: 'os',
      render: (os, record) => (
        <Tag color="blue">{os} {record.os_version}</Tag>
      ),
    },
    {
      title: '架构',
      dataIndex: 'arch',
      key: 'arch',
    },
    {
      title: 'IP 地址',
      dataIndex: 'ip',
      key: 'ip',
    },
    {
      title: '代理数',
      dataIndex: 'proxy_count',
      key: 'proxy_count',
      render: (n) => <Tag color="green">{n}</Tag>,
    },
    {
      title: '登录时间',
      dataIndex: 'login_time',
      key: 'login_time',
      render: (ts) => formatTime(ts),
    },
    {
      title: '操作',
      key: 'action',
      render: (_, record) => (
        <Button type="link" onClick={() => openDetail(record)}>
          管理
        </Button>
      ),
    },
  ];

  return (
    <div>
      <Row justify="space-between" align="middle" style={{ marginBottom: 16 }}>
        <Col>
          <Typography.Title level={4} style={{ margin: 0 }}>
            远程管理
          </Typography.Title>
          <Typography.Text type="secondary">
            管理已连接的 FRP 客户端
          </Typography.Text>
        </Col>
        <Col>
          <Button
            icon={<ReloadOutlined />}
            onClick={loadClients}
            loading={loading}
          >
            刷新
          </Button>
        </Col>
      </Row>

      {!frpsRunning && (
        <Alert
          type="warning"
          message="frps 服务未运行"
          description="请先启动 frps 服务以查看连接的客户端"
          style={{ marginBottom: 16 }}
        />
      )}

      <Card bodyStyle={{ padding: 0 }}>
        <Table
          rowKey="session_id"
          columns={columns}
          dataSource={clients}
          loading={loading}
          locale={{ emptyText: <Empty description="暂无连接的客户端" /> }}
          pagination={{ pageSize: 10 }}
        />
      </Card>

      <Modal
        title={
          <Space>
            <DesktopOutlined />
            <span>{selectedClient?.client_name || selectedClient?.hostname || '客户端管理'}</span>
            <Tag color="green">在线</Tag>
          </Space>
        }
        open={detailOpen}
        onCancel={() => setDetailOpen(false)}
        footer={null}
        width={900}
        destroyOnClose
      >
        {selectedClient && (
          <>
            <Descriptions size="small" column={3} style={{ marginBottom: 16 }}>
              <Descriptions.Item label="IP">{selectedClient.ip}</Descriptions.Item>
              <Descriptions.Item label="系统">
                {selectedClient.os} {selectedClient.os_version}
              </Descriptions.Item>
              <Descriptions.Item label="架构">{selectedClient.arch}</Descriptions.Item>
              <Descriptions.Item label="客户端版本">
                {selectedClient.client_version}
              </Descriptions.Item>
              <Descriptions.Item label="代理数">{selectedClient.proxy_count}</Descriptions.Item>
              <Descriptions.Item label="运行时长">
                {formatDuration(Date.now() / 1000 - selectedClient.login_time)}
              </Descriptions.Item>
            </Descriptions>

            <Tabs activeKey={activeTab} onChange={handleTabChange}>
              <TabPane
                tab={
                  <span>
                    <SnippetsOutlined />
                    端口映射
                  </span>
                }
                key="proxies"
              >
                <div style={{ textAlign: 'right', marginBottom: 8 }}>
                  <Button size="small" icon={<ReloadOutlined />} onClick={loadProxies} loading={proxiesLoading}>
                    刷新
                  </Button>
                </div>
                <Spin spinning={proxiesLoading}>
                  {proxies.length === 0 ? (
                    <Empty description="暂无代理配置" />
                  ) : (
                    <List
                      dataSource={proxies}
                      renderItem={(p) => (
                        <List.Item>
                          <List.Item.Meta
                            title={
                              <Space>
                                <Tag color="blue">{p.type}</Tag>
                                <span style={{ fontWeight: 500 }}>{p.name}</span>
                              </Space>
                            }
                            description={
                              p.type === 'http'
                                ? `域名: ${p.custom_domains?.join(', ') || '-'}  子域名: ${p.subdomain || '-'}`
                                : p.type === 'stcp' || p.type === 'stcp_visitor'
                                ? `SK: ${p.sk}`
                                : `本地: ${p.local_ip}:${p.local_port} → 远程: ${p.remote_port}`
                            }
                          />
                        </List.Item>
                      )}
                    />
                  )}
                </Spin>
              </TabPane>

              <TabPane
                tab={
                  <span>
                    <FolderOpenOutlined />
                    文件管理
                  </span>
                }
                key="files"
              >
                <Space style={{ marginBottom: 12 }}>
                  <Button size="small" icon={<ArrowUpOutlined />} onClick={() => {
                    const parent = currentPath.substring(0, currentPath.lastIndexOf('/'));
                    loadFiles(parent || '/');
                  }}>
                    上级目录
                  </Button>
                  <Button size="small" icon={<ReloadOutlined />} onClick={() => loadFiles(currentPath)} loading={filesLoading}>
                    刷新
                  </Button>
                  <Typography.Text code>{currentPath}</Typography.Text>
                </Space>
                <Spin spinning={filesLoading}>
                  {fileEntries.length === 0 ? (
                    <Empty description="空目录" />
                  ) : (
                    <List
                      size="small"
                      dataSource={fileEntries}
                      renderItem={(f) => (
                        <List.Item
                          style={{ cursor: f.is_dir ? 'pointer' : 'default' }}
                          onClick={() => {
                            if (f.is_dir) {
                              loadFiles(currentPath + '/' + f.name);
                            }
                          }}
                        >
                          <List.Item.Meta
                            avatar={f.is_dir ? <FolderOpenOutlined style={{ color: '#faad14' }} /> : <SnippetsOutlined />}
                            title={f.name}
                            description={
                              <Space size="large">
                                <span>{f.is_dir ? '目录' : formatBytes(f.size)}</span>
                                <span>{formatTime(f.mtime)}</span>
                              </Space>
                            }
                          />
                        </List.Item>
                      )}
                    />
                  )}
                </Spin>
              </TabPane>

              <TabPane
                tab={
                  <span>
                    <CameraOutlined />
                    屏幕截图
                  </span>
                }
                key="screenshot"
              >
                <div style={{ textAlign: 'center', marginBottom: 16 }}>
                  <Button
                    type="primary"
                    icon={<CameraOutlined />}
                    onClick={takeScreenshot}
                    loading={screenshotLoading}
                  >
                    截取屏幕
                  </Button>
                </div>
                <Spin spinning={screenshotLoading}>
                  {screenshotImg ? (
                    <img
                      src={screenshotImg}
                      alt="screenshot"
                      style={{ width: '100%', border: '1px solid #d9d9d9', borderRadius: 4 }}
                    />
                  ) : (
                    <Empty description="点击上方按钮截取屏幕" />
                  )}
                </Spin>
              </TabPane>

              <TabPane
                tab={
                  <span>
                    <InfoCircleOutlined />
                    系统信息
                  </span>
                }
                key="sysinfo"
              >
                <div style={{ textAlign: 'right', marginBottom: 8 }}>
                  <Button size="small" icon={<ReloadOutlined />} onClick={loadSysInfo} loading={sysInfoLoading}>
                    刷新
                  </Button>
                </div>
                <Spin spinning={sysInfoLoading}>
                  {sysInfo ? (
                    <>
                      <Row gutter={16} style={{ marginBottom: 16 }}>
                        <Col span={6}>
                          <Statistic title="PID" value={sysInfo.pid} />
                        </Col>
                        {sysInfo.cpu_count && (
                          <Col span={6}>
                            <Statistic title="CPU 核心" value={sysInfo.cpu_count} suffix="核" />
                          </Col>
                        )}
                        {sysInfo.memory_total && (
                          <Col span={6}>
                            <Statistic
                              title="总内存"
                              value={parseFloat((sysInfo.memory_total / 1024 / 1024 / 1024).toFixed(2))}
                              suffix="GB"
                            />
                          </Col>
                        )}
                        {sysInfo.disk_total && (
                          <Col span={6}>
                            <Statistic
                              title="总磁盘"
                              value={parseFloat((sysInfo.disk_total / 1024 / 1024 / 1024).toFixed(2))}
                              suffix="GB"
                            />
                          </Col>
                        )}
                      </Row>
                      <Descriptions column={2} size="small" bordered>
                        <Descriptions.Item label="主机名">{sysInfo.hostname}</Descriptions.Item>
                        <Descriptions.Item label="操作系统">{sysInfo.platform}</Descriptions.Item>
                        <Descriptions.Item label="系统架构">{sysInfo.arch}</Descriptions.Item>
                        <Descriptions.Item label="Python 版本">{sysInfo.python_version}</Descriptions.Item>
                        <Descriptions.Item label="工作目录">{sysInfo.cwd}</Descriptions.Item>
                        <Descriptions.Item label="处理器">{sysInfo.processor || '-'}</Descriptions.Item>
                      </Descriptions>
                    </>
                  ) : (
                    <Empty />
                  )}
                </Spin>
              </TabPane>

              <TabPane
                tab={
                  <span>
                    <CodeOutlined />
                    终端
                  </span>
                }
                key="shell"
              >
                <Alert
                  type="warning"
                  showIcon
                  message="危险操作"
                  description="Shell 命令将直接在客户端执行，请谨慎使用"
                  style={{ marginBottom: 12 }}
                />
                <Space.Compact style={{ width: '100%', marginBottom: 8 }}>
                  <Input
                    placeholder="输入 shell 命令，例如: ls -la"
                    value={shellCmd}
                    onChange={(e) => setShellCmd(e.target.value)}
                    onPressEnter={runShellCmd}
                  />
                  <InputNumber
                    min={1}
                    max={300}
                    value={shellTimeout}
                    onChange={(v) => setShellTimeout(v || 30)}
                    style={{ width: 100 }}
                    addonBefore="超时"
                    addonAfter="s"
                  />
                  <Button
                    type="primary"
                    icon={<PlayCircleOutlined />}
                    onClick={runShellCmd}
                    loading={shellLoading}
                  >
                    执行
                  </Button>
                </Space.Compact>
                <TextArea
                  value={shellOutput}
                  readOnly
                  rows={12}
                  style={{
                    fontFamily: 'monospace',
                    background: '#1e1e1e',
                    color: '#d4d4d4',
                    resize: 'none',
                  }}
                  placeholder="命令输出将显示在此处..."
                />
              </TabPane>
            </Tabs>
          </>
        )}
      </Modal>
    </div>
  );
};

export default RemoteManagement;
