import React, { useEffect, useState, useCallback } from 'react';
import { Card, Table, Button, Space, Modal, Form, Input, Select, message, Tag, Popconfirm } from 'antd';
import { PlusOutlined, DeleteOutlined, ReloadOutlined } from '@ant-design/icons';
import { listUsers, createUser, deleteUser, type UserInfo } from '../api/auth';
import { useAuthStore } from '../store/auth';

// 用户管理页（仅 admin）
const Users: React.FC = () => {
  const [users, setUsers] = useState<UserInfo[]>([]);
  const [loading, setLoading] = useState(false);
  const [modalOpen, setModalOpen] = useState(false);
  const [form] = Form.useForm();
  const [submitting, setSubmitting] = useState(false);
  const currentUser = useAuthStore((s) => s.user);

  // 加载用户列表
  const loadUsers = useCallback(async () => {
    setLoading(true);
    try {
      const data = await listUsers();
      setUsers(data || []);
    } catch (e) {
      message.error('加载用户列表失败');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadUsers();
  }, [loadUsers]);

  // 新增用户
  const handleCreate = async () => {
    try {
      const values = await form.validateFields();
      setSubmitting(true);
      await createUser(values.username, values.password, values.role);
      message.success('用户创建成功');
      setModalOpen(false);
      form.resetFields();
      loadUsers();
    } catch (e: unknown) {
      const err = e as { response?: { data?: { detail?: string } } };
      if (err?.response?.data?.detail) {
        message.error(err.response.data.detail);
      } else if (!(e instanceof Error)) {
        message.error('创建用户失败');
      }
    } finally {
      setSubmitting(false);
    }
  };

  // 删除用户
  const handleDelete = async (username: string) => {
    try {
      await deleteUser(username);
      message.success('用户已删除');
      loadUsers();
    } catch (e) {
      message.error('删除用户失败');
    }
  };

  const columns = [
    {
      title: '用户名',
      dataIndex: 'username',
      key: 'username',
    },
    {
      title: '角色',
      dataIndex: 'role',
      key: 'role',
      render: (role: string) => (
        <Tag color={role === 'admin' ? 'red' : 'blue'}>{role === 'admin' ? '管理员' : '查看者'}</Tag>
      ),
    },
    {
      title: '操作',
      key: 'action',
      render: (_: unknown, record: UserInfo) => (
        <Popconfirm
          title="确认删除该用户？"
          onConfirm={() => handleDelete(record.username)}
          disabled={record.username === currentUser?.username}
          okText="删除"
          cancelText="取消"
        >
          <Button
            danger
            size="small"
            icon={<DeleteOutlined />}
            disabled={record.username === currentUser?.username}
          >
            删除
          </Button>
        </Popconfirm>
      ),
    },
  ];

  return (
    <Card
      title="用户管理"
      extra={
        <Space>
          <Button icon={<ReloadOutlined />} onClick={loadUsers}>
            刷新
          </Button>
          <Button type="primary" icon={<PlusOutlined />} onClick={() => setModalOpen(true)}>
            新增用户
          </Button>
        </Space>
      }
    >
      <Table
        rowKey="username"
        columns={columns}
        dataSource={users}
        loading={loading}
        pagination={false}
      />
      <Modal
        title="新增用户"
        open={modalOpen}
        onOk={handleCreate}
        onCancel={() => {
          setModalOpen(false);
          form.resetFields();
        }}
        confirmLoading={submitting}
        okText="创建"
        cancelText="取消"
      >
        <Form form={form} layout="vertical">
          <Form.Item
            name="username"
            label="用户名"
            rules={[{ required: true, message: '请输入用户名' }]}
          >
            <Input placeholder="请输入用户名" />
          </Form.Item>
          <Form.Item
            name="password"
            label="密码"
            rules={[
              { required: true, message: '请输入密码' },
              { min: 8, message: '密码至少 8 位' },
            ]}
          >
            <Input.Password placeholder="至少 8 位" />
          </Form.Item>
          <Form.Item name="role" label="角色" rules={[{ required: true, message: '请选择角色' }]}>
            <Select
              placeholder="请选择角色"
              options={[
                { label: '管理员', value: 'admin' },
                { label: '查看者', value: 'viewer' },
              ]}
            />
          </Form.Item>
        </Form>
      </Modal>
    </Card>
  );
};

export default Users;
