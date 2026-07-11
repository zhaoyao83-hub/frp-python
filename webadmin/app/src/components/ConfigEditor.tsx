import React, { useEffect, useState, useMemo } from 'react';
import {
  Card,
  Segmented,
  Form,
  Input,
  InputNumber,
  Switch,
  Select,
  Button,
  Space,
  Spin,
  message,
  Modal,
  Alert,
} from 'antd';
import {
  CheckCircleOutlined,
  ReloadOutlined,
} from '@ant-design/icons';
import CodeMirror from '@uiw/react-codemirror';
import { json as jsonLang } from '@codemirror/lang-json';
import {
  getConfig,
  saveConfig,
  validateConfig,
  type ConfigType,
  type ConfigSchemaField,
} from '../api/config';
import { restartService, type ServiceName } from '../api/service';

interface ConfigEditorProps {
  type: ConfigType;
}

type Mode = 'form' | 'json';

// 配置编辑器：表单模式 / JSON 原文模式双模
const ConfigEditor: React.FC<ConfigEditorProps> = ({ type }) => {
  const [form] = Form.useForm();
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [mode, setMode] = useState<Mode>('form');
  const [schema, setSchema] = useState<ConfigSchemaField[]>([]);
  const [jsonText, setJsonText] = useState('');
  const [jsonError, setJsonError] = useState<string | null>(null);

  // type 对应的服务名
  const serviceName: ServiceName = type === 'server' ? 'frps' : 'frpc';

  // 加载配置和 schema
  const loadConfig = async () => {
    setLoading(true);
    try {
      const resp = await getConfig(type);
      setSchema(resp.schema || []);
      const text = JSON.stringify(resp.config || {}, null, 2);
      setJsonText(text);
      form.setFieldsValue(resp.config || {});
    } catch (e) {
      message.error('加载配置失败');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadConfig();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [type]);

  // 切换模式时同步状态
  const handleModeChange = (value: Mode) => {
    if (value === 'json') {
      // 表单 -> JSON：用当前表单值生成 JSON
      const values = form.getFieldsValue();
      setJsonText(JSON.stringify(values, null, 2));
      setJsonError(null);
    } else {
      // JSON -> 表单：尝试解析 JSON 写回表单
      try {
        const parsed = JSON.parse(jsonText);
        form.setFieldsValue(parsed);
        setJsonError(null);
      } catch (e) {
        setJsonError('JSON 解析失败，无法切换到表单模式');
        return;
      }
    }
    setMode(value);
  };

  // 获取当前配置对象
  const getCurrentConfig = (): Record<string, unknown> | null => {
    if (mode === 'json') {
      try {
        const parsed = JSON.parse(jsonText);
        setJsonError(null);
        return parsed;
      } catch (e) {
        setJsonError('JSON 格式错误，请检查');
        return null;
      }
    }
    return form.getFieldsValue();
  };

  // 保存：先校验再保存
  const handleSave = async () => {
    const cfg = getCurrentConfig();
    if (!cfg) {
      message.error(jsonError || '配置解析失败');
      return;
    }
    setSaving(true);
    try {
      // 先校验
      const validateResp = await validateConfig(type, cfg);
      if (!validateResp.valid) {
        Modal.error({
          title: '配置校验失败',
          content: (
            <ul style={{ paddingLeft: 20 }}>
              {validateResp.errors.map((err, i) => (
                <li key={i}>{err}</li>
              ))}
            </ul>
          ),
        });
        return;
      }
      // 再保存
      const saveResp = await saveConfig(type, cfg);
      // 同步 JSON 文本
      setJsonText(JSON.stringify(cfg, null, 2));
      message.success(saveResp.message || '保存成功');
      // 提示是否重启
      if (saveResp.need_restart) {
        showRestartModal();
      }
    } catch (e) {
      message.error('保存配置失败');
    } finally {
      setSaving(false);
    }
  };

  // 重启确认弹窗
  const showRestartModal = () => {
    Modal.confirm({
      title: '配置已保存，需要重启服务生效',
      content: `是否立即重启 ${serviceName} 服务？`,
      okText: '立即重启',
      cancelText: '稍后',
      onOk: async () => {
        try {
          await restartService(serviceName);
          message.success(`${serviceName} 已重启`);
        } catch (e) {
          message.error(`重启 ${serviceName} 失败`);
        }
      },
    });
  };

  // 根据 schema 字段渲染表单项
  const renderFormField = (field: ConfigSchemaField) => {
    const label = (
      <span>
        {field.key}
        {field.description && (
          <span style={{ color: '#999', marginLeft: 8, fontWeight: 400, fontSize: 12 }}>
            {field.description}
          </span>
        )}
      </span>
    );
    let control: React.ReactNode;
    switch (field.type) {
      case 'number':
        control = <InputNumber style={{ width: '100%' }} />;
        break;
      case 'boolean':
        control = <Switch />;
        break;
      case 'select':
        control = (
          <Select
            allowClear
            options={(field.options || []).map((opt) => ({ label: opt, value: opt }))}
          />
        );
        break;
      case 'array':
        control = <Input placeholder="逗号分隔，如 8080,9000-9100" />;
        break;
      case 'string':
      default:
        control = <Input />;
        break;
    }
    return (
      <Form.Item key={field.key} name={field.key} label={label} valuePropName={field.type === 'boolean' ? 'checked' : 'value'}>
        {control}
      </Form.Item>
    );
  };

  // 表单模式字段
  const formFields = useMemo(() => schema.map(renderFormField), [schema]);

  return (
    <Card
      title={`${type === 'server' ? '服务端' : '客户端'}配置`}
      extra={
        <Space>
          <Segmented
            value={mode}
            onChange={(v) => handleModeChange(v as Mode)}
            options={[
              { label: '表单模式', value: 'form' },
              { label: 'JSON 模式', value: 'json' },
            ]}
          />
          <Button icon={<ReloadOutlined />} onClick={loadConfig} disabled={loading}>
            刷新
          </Button>
          <Button icon={<CheckCircleOutlined />} onClick={handleSave} disabled={saving} loading={saving}>
            校验并保存
          </Button>
        </Space>
      }
    >
      <Spin spinning={loading}>
        {jsonError && mode === 'json' && (
          <Alert type="error" message={jsonError} style={{ marginBottom: 12 }} showIcon />
        )}
        {mode === 'form' ? (
          <Form form={form} layout="vertical" preserve={false}>
            {formFields}
          </Form>
        ) : (
          <div style={{ border: '1px solid #d9d9d9', borderRadius: 6 }}>
            <CodeMirror
              value={jsonText}
              onChange={(val) => setJsonText(val)}
              extensions={[jsonLang()]}
              height="520px"
            />
          </div>
        )}
      </Spin>
    </Card>
  );
};

export default ConfigEditor;
