import React, { useState, useEffect, useCallback } from 'react';
import {
  Table,
  Button,
  Space,
  Modal,
  Form,
  Input,
  InputNumber,
  Select,
  Switch,
  Tag,
  Popconfirm,
  message,
  Typography,
  Alert,
} from 'antd';
import { PlusOutlined, EditOutlined, DeleteOutlined, ReloadOutlined } from '@ant-design/icons';
import {
  listProxies,
  createProxy,
  updateProxy,
  deleteProxy,
  ProxyConfig,
  ProxyType,
} from '../api/proxies';
import { restartService, getStatus } from '../api/service';

const { Title } = Typography;

const TYPE_COLORS: Record<ProxyType, string> = {
  tcp: 'blue',
  udp: 'cyan',
  http: 'green',
  stcp: 'gold',
  stcp_visitor: 'magenta',
};

const TYPE_LABELS: Record<ProxyType, string> = {
  tcp: 'TCP',
  udp: 'UDP',
  http: 'HTTP 代理',
  stcp: 'STCP 提供方',
  stcp_visitor: 'STCP 访问方',
};

const PortMapping: React.FC = () => {
  const [proxies, setProxies] = useState<ProxyConfig[]>([]);
  const [loading, setLoading] = useState(false);
  const [modalOpen, setModalOpen] = useState(false);
  const [editingProxy, setEditingProxy] = useState<ProxyConfig | null>(null);
  const [form] = Form.useForm<ProxyConfig>();
  const [submitting, setSubmitting] = useState(false);
  const [frpcRunning, setFrpcRunning] = useState(false);
  const [restarting, setRestarting] = useState(false);
  const [toggleLoading, setToggleLoading] = useState<Set<string>>(new Set());

  const proxyType = Form.useWatch('type', form) as ProxyType | undefined;

  const fetchFrpcStatus = useCallback(async () => {
    try {
      const status = await getStatus('frpc');
      setFrpcRunning(status.running);
    } catch {
      // 静默失败
    }
  }, []);

  const fetchProxies = useCallback(async () => {
    setLoading(true);
    try {
      const res = await listProxies();
      setProxies(res.proxies);
    } catch (err: any) {
      message.error(err.response?.data?.detail || '加载代理列表失败');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchProxies();
    fetchFrpcStatus();
  }, [fetchProxies, fetchFrpcStatus]);

  const handleRestartFrpc = async () => {
    setRestarting(true);
    try {
      await restartService('frpc');
      message.success('frpc 已重启，配置已生效');
      setTimeout(fetchFrpcStatus, 1000);
    } catch (err: any) {
      message.error(err.response?.data?.detail || '重启失败');
    } finally {
      setRestarting(false);
    }
  };

  const handleAdd = () => {
    setEditingProxy(null);
    form.resetFields();
    form.setFieldsValue({
      type: 'tcp',
      local_ip: '127.0.0.1',
      bind_addr: '127.0.0.1',
      enabled: true,
    });
    setModalOpen(true);
  };

  const handleEdit = (proxy: ProxyConfig) => {
    setEditingProxy(proxy);
    form.setFieldsValue({
      ...proxy,
      bind_addr: proxy.bind_addr || '127.0.0.1',
    } as any);
    setModalOpen(true);
  };

  const handleDelete = async (name: string) => {
    try {
      await deleteProxy(name);
      message.success('删除成功，需重启 frpc 生效');
      fetchProxies();
    } catch (err: any) {
      message.error(err.response?.data?.detail || '删除失败');
    }
  };

  const handleToggle = async (name: string, checked: boolean) => {
    if (toggleLoading.has(name)) return;
    
    setToggleLoading(prev => new Set(prev).add(name));
    try {
      await updateProxy(name, { enabled: checked });
      message.success(`${checked ? '启用' : '禁用'}成功，需重启 frpc 生效`);
      
      setProxies(prev => 
        prev.map(p => 
          p.name === name ? { ...p, enabled: checked } : p
        )
      );
    } catch (err: any) {
      message.error(err.response?.data?.detail || '操作失败');
      fetchProxies();
    } finally {
      setToggleLoading(prev => {
        const next = new Set(prev);
        next.delete(name);
        return next;
      });
    }
  };

  const handleSubmit = async () => {
    try {
      const values = await form.validateFields();
      setSubmitting(true);

      const payload: Partial<ProxyConfig> = { ...values };

      if (payload.type === 'http') {
        if (payload.custom_domains && typeof payload.custom_domains === 'string') {
          payload.custom_domains = (payload.custom_domains as string)
            .split(',')
            .map((s) => s.trim())
            .filter(Boolean);
        }
      }

      if (editingProxy) {
        await updateProxy(editingProxy.name, payload);
        message.success('更新成功，需重启 frpc 生效');
      } else {
        await createProxy(payload as ProxyConfig);
        message.success('添加成功，需重启 frpc 生效');
      }
      setModalOpen(false);
      fetchProxies();
    } catch (err: any) {
      if (err.errorFields) return;
      message.error(err.response?.data?.detail || '保存失败');
    } finally {
      setSubmitting(false);
    }
  };

  const columns = [
    {
      title: '状态',
      dataIndex: 'enabled',
      key: 'enabled',
      width: 70,
      render: (v: boolean | undefined, record: ProxyConfig) => (
        <Switch
          size="small"
          checked={v !== false}
          onChange={(checked) => handleToggle(record.name, checked)}
          loading={toggleLoading.has(record.name)}
        />
      ),
    },
    {
      title: '名称',
      dataIndex: 'name',
      key: 'name',
      width: 140,
      render: (v: string, record: ProxyConfig) => (
        <span style={{ opacity: record.enabled === false ? 0.5 : 1 }}>
          <b>{v}</b>
        </span>
      ),
    },
    {
      title: '类型',
      dataIndex: 'type',
      key: 'type',
      width: 110,
      render: (v: ProxyType) => (
        <Tag color={TYPE_COLORS[v]}>{TYPE_LABELS[v] || v}</Tag>
      ),
    },
    {
      title: '本地服务',
      key: 'local',
      width: 160,
      render: (_: unknown, record: ProxyConfig) => {
        if (record.type === 'stcp_visitor') {
          return <span style={{ color: '#999' }}>—</span>;
        }
        return (
          <span>
            {record.local_ip}:{record.local_port}
          </span>
        );
      },
    },
    {
      title: '映射信息',
      key: 'mapping',
      width: 220,
      ellipsis: true,
      render: (_: unknown, record: ProxyConfig) => {
        if (record.type === 'tcp' || record.type === 'udp') {
          return <span>远程端口: {record.remote_port}</span>;
        }
        if (record.type === 'http') {
          const domains = record.custom_domains?.join(', ') || '';
          const sub = record.subdomain ? `*.${record.subdomain}` : '';
          return (
            <span title={domains || sub}>
              {domains || sub || <span style={{ color: '#999' }}>未配置域名</span>}
            </span>
          );
        }
        if (record.type === 'stcp') {
          return <span>密钥: {record.sk ? '******' : <span style={{ color: '#999' }}>未设置</span>}</span>;
        }
        if (record.type === 'stcp_visitor') {
          return (
            <span>
              监听: {record.bind_addr || '127.0.0.1'}:{record.bind_port}
              <br />
              提供方: {record.server_name || <span style={{ color: '#999' }}>未设置</span>}
            </span>
          );
        }
        return <span style={{ color: '#999' }}>—</span>;
      },
    },
    {
      title: '操作',
      key: 'actions',
      width: 140,
      fixed: 'right' as const,
      render: (_: unknown, record: ProxyConfig) => (
        <Space size="small">
          <Button
            type="link"
            size="small"
            icon={<EditOutlined />}
            onClick={() => handleEdit(record)}
          >
            编辑
          </Button>
          <Popconfirm
            title={`确定删除代理 "${record.name}" 吗？`}
            onConfirm={() => handleDelete(record.name)}
            okText="删除"
            cancelText="取消"
            okButtonProps={{ danger: true }}
          >
            <Button type="link" size="small" danger icon={<DeleteOutlined />}>
              删除
            </Button>
          </Popconfirm>
        </Space>
      ),
    },
  ];

  const showLocalFields = proxyType !== 'stcp_visitor';
  const showRemotePort = proxyType === 'tcp' || proxyType === 'udp';
  const showHttpFields = proxyType === 'http';
  const showStcpFields = proxyType === 'stcp';
  const showStcpVisitorFields = proxyType === 'stcp_visitor';

  return (
    <div>
      <div
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
          marginBottom: 16,
        }}
      >
        <Title level={4} style={{ margin: 0 }}>
          端口映射管理
        </Title>
        <Space>
          <Button icon={<ReloadOutlined />} onClick={fetchProxies} loading={loading}>
            刷新
          </Button>
          <Button type="primary" icon={<PlusOutlined />} onClick={handleAdd}>
            新增映射
          </Button>
        </Space>
      </div>

      <Alert
        type="info"
        showIcon
        message={
          <span>
            增删改操作需重启 frpc 服务才能生效
            {frpcRunning && (
              <Button
                type="link"
                size="small"
                loading={restarting}
                onClick={handleRestartFrpc}
                style={{ marginLeft: 8 }}
              >
                立即重启 frpc
              </Button>
            )}
          </span>
        }
        style={{ marginBottom: 16 }}
      />

      <Table
        rowKey="name"
        columns={columns}
        dataSource={proxies}
        loading={loading}
        pagination={{ pageSize: 10 }}
        scroll={{ x: 880 }}
      />

      <Modal
        title={editingProxy ? '编辑端口映射' : '新增端口映射'}
        open={modalOpen}
        onOk={handleSubmit}
        onCancel={() => setModalOpen(false)}
        confirmLoading={submitting}
        okText="保存"
        cancelText="取消"
        width={560}
        destroyOnClose
      >
        <Form form={form} layout="vertical" style={{ marginTop: 8 }}>
          <Form.Item
            name="name"
            label="代理名称"
            rules={[{ required: true, message: '请输入代理名称' }]}
          >
            <Input placeholder="如 web、ssh、mysql" disabled={!!editingProxy} />
          </Form.Item>

          <Form.Item
            name="enabled"
            label="启用状态"
            valuePropName="checked"
          >
            <Switch checkedChildren="启用" unCheckedChildren="禁用" />
          </Form.Item>

          <Form.Item
            name="type"
            label="代理类型"
            rules={[{ required: true, message: '请选择类型' }]}
          >
            <Select>
              <Select.Option value="tcp">TCP 端口映射</Select.Option>
              <Select.Option value="udp">UDP 端口映射</Select.Option>
              <Select.Option value="http">HTTP 虚拟主机</Select.Option>
              <Select.Option value="stcp">STCP 秘密 TCP（提供方）</Select.Option>
              <Select.Option value="stcp_visitor">STCP 秘密 TCP（访问方）</Select.Option>
            </Select>
          </Form.Item>

          {showLocalFields && (
            <>
              <Form.Item
                name="local_ip"
                label="本地 IP"
                rules={[{ required: true, message: '请输入本地 IP' }]}
              >
                <Input placeholder="127.0.0.1" />
              </Form.Item>

              <Form.Item
                name="local_port"
                label="本地端口"
                rules={[{ required: true, message: '请输入本地端口' }]}
              >
                <InputNumber min={1} max={65535} style={{ width: '100%' }} placeholder="如 8080" />
              </Form.Item>
            </>
          )}

          {showRemotePort && (
            <Form.Item
              name="remote_port"
              label="远程端口"
              rules={[{ required: true, message: '请输入远程端口' }]}
            >
              <InputNumber min={1} max={65535} style={{ width: '100%' }} placeholder="如 8080" />
            </Form.Item>
          )}

          {showHttpFields && (
            <>
              <Form.Item
                name="custom_domains"
                label="自定义域名"
                extra="多个域名用英文逗号分隔，如 example.com,www.example.com"
                rules={[
                  {
                    validator: (_, value) => {
                      const subdomain = form.getFieldValue('subdomain');
                      if (!value && !subdomain) {
                        return Promise.reject(new Error('自定义域名和子域名至少填一个'));
                      }
                      return Promise.resolve();
                    },
                  },
                ]}
              >
                <Input placeholder="example.com" />
              </Form.Item>

              <Form.Item
                name="subdomain"
                label="子域名前缀"
                extra="服务端配置 subdomain_host 后可使用"
              >
                <Input placeholder="如 app，则访问 app.example.com" />
              </Form.Item>
            </>
          )}

          {showStcpFields && (
            <Form.Item
              name="sk"
              label="密钥 (SK)"
              rules={[{ required: true, message: '请输入密钥' }]}
              extra="访问方需使用相同密钥才能连接"
            >
              <Input.Password placeholder="请输入共享密钥" />
            </Form.Item>
          )}

          {showStcpVisitorFields && (
            <>
              <Form.Item
                name="sk"
                label="密钥 (SK)"
                rules={[{ required: true, message: '请输入密钥' }]}
                extra="需与提供方配置一致"
              >
                <Input.Password placeholder="请输入共享密钥" />
              </Form.Item>

              <Form.Item
                name="server_name"
                label="提供方代理名称"
                rules={[{ required: true, message: '请输入提供方代理名称' }]}
                extra="服务提供方在 frpc 中配置的代理名称"
              >
                <Input placeholder="如 my_secret_service" />
              </Form.Item>

              <Form.Item
                name="bind_addr"
                label="本地监听地址"
                rules={[{ required: true, message: '请输入本地监听地址' }]}
              >
                <Input placeholder="127.0.0.1" />
              </Form.Item>

              <Form.Item
                name="bind_port"
                label="本地监听端口"
                rules={[{ required: true, message: '请输入本地监听端口' }]}
              >
                <InputNumber min={1} max={65535} style={{ width: '100%' }} placeholder="如 9000" />
              </Form.Item>
            </>
          )}
        </Form>
      </Modal>
    </div>
  );
};

export default PortMapping;
