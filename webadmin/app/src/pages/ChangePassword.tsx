import React, { useState } from 'react';
import { Modal, Form, Input, message } from 'antd';
import { changePassword } from '../api/auth';
import { useAuthStore } from '../store/auth';

interface ChangePasswordProps {
  open: boolean;
  // 是否强制（must_change_password 时不可关闭）
  forced?: boolean;
  onClose: () => void;
}

// 修改密码弹窗：forced=true 时不可关闭
const ChangePassword: React.FC<ChangePasswordProps> = ({ open, forced = false, onClose }) => {
  const [form] = Form.useForm();
  const [loading, setLoading] = useState(false);
  const markPasswordChanged = useAuthStore((s) => s.markPasswordChanged);

  const handleSubmit = async () => {
    try {
      const values = await form.validateFields();
      setLoading(true);
      await changePassword(values.old_password, values.new_password);
      message.success('密码修改成功');
      markPasswordChanged();
      form.resetFields();
      onClose();
    } catch (e: unknown) {
      // validateFields 抛出的异常不提示
      const err = e as { response?: { data?: { detail?: string } } };
      if (err?.response?.data?.detail) {
        message.error(err.response.data.detail);
      } else if (!(e instanceof Error && e.message.includes('validate'))) {
        message.error('密码修改失败');
      }
    } finally {
      setLoading(false);
    }
  };

  return (
    <Modal
      title="修改密码"
      open={open}
      onOk={handleSubmit}
      onCancel={forced ? undefined : onClose}
      closable={!forced}
      maskClosable={!forced}
      keyboard={!forced}
      confirmLoading={loading}
      okText="确认修改"
      cancelText={forced ? undefined : '取消'}
      cancelButtonProps={{ style: forced ? { display: 'none' } : {} }}
    >
      {forced && (
        <p style={{ color: '#faad14' }}>首次登录或密码已重置，请先修改密码再继续使用。</p>
      )}
      <Form form={form} layout="vertical">
        <Form.Item
          name="old_password"
          label="旧密码"
          rules={[{ required: true, message: '请输入旧密码' }]}
        >
          <Input.Password placeholder="请输入旧密码" />
        </Form.Item>
        <Form.Item
          name="new_password"
          label="新密码"
          rules={[
            { required: true, message: '请输入新密码' },
            { min: 8, message: '密码至少 8 位' },
          ]}
        >
          <Input.Password placeholder="至少 8 位" />
        </Form.Item>
        <Form.Item
          name="confirm_password"
          label="确认新密码"
          dependencies={['new_password']}
          rules={[
            { required: true, message: '请确认新密码' },
            ({ getFieldValue }) => ({
              validator(_, value) {
                if (!value || getFieldValue('new_password') === value) {
                  return Promise.resolve();
                }
                return Promise.reject(new Error('两次输入的密码不一致'));
              },
            }),
          ]}
        >
          <Input.Password placeholder="再次输入新密码" />
        </Form.Item>
      </Form>
    </Modal>
  );
};

export default ChangePassword;
