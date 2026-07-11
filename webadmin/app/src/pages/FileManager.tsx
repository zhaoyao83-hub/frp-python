import React, { useState, useEffect, useCallback } from 'react';
import {
  Table,
  Button,
  Space,
  Modal,
  Input,
  message,
  Typography,
  Breadcrumb,
  Tooltip,
  Popconfirm,
  Tag,
  Drawer,
  Form,
  Alert,
  Upload,
} from 'antd';
import {
  FolderOutlined,
  FileOutlined,
  ArrowUpOutlined,
  ReloadOutlined,
  EditOutlined,
  DeleteOutlined,
  DownloadOutlined,
  FolderAddOutlined,
  HomeOutlined,
  FormOutlined,
  UploadOutlined,
} from '@ant-design/icons';
import {
  listFiles,
  getFileContent,
  saveFileContent,
  deleteFile,
  renameFile,
  makeDir,
  uploadFile,
  getDownloadUrl,
  FileItem,
} from '../api/files';

const { Title } = Typography;
const { TextArea } = Input;

const FileManager: React.FC = () => {
  const [currentPath, setCurrentPath] = useState('');
  const [items, setItems] = useState<FileItem[]>([]);
  const [parent, setParent] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [editOpen, setEditOpen] = useState(false);
  const [editingFile, setEditingFile] = useState<FileItem | null>(null);
  const [editContent, setEditContent] = useState('');
  const [editSaving, setEditSaving] = useState(false);
  const [editReadonly, setEditReadonly] = useState(false);
  const [mkdirOpen, setMkdirOpen] = useState(false);
  const [renameOpen, setRenameOpen] = useState(false);
  const [renameTarget, setRenameTarget] = useState<FileItem | null>(null);
  const [uploading, setUploading] = useState(false);

  const fetchList = useCallback(async (path: string) => {
    setLoading(true);
    try {
      const res = await listFiles(path);
      setItems(res.items);
      setParent(res.parent ?? null);
      setCurrentPath(path);
    } catch (err: any) {
      message.error(err.response?.data?.detail || '加载文件列表失败');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchList('');
  }, [fetchList]);

  const handleEnterDir = (item: FileItem) => {
    if (item.is_dir) {
      fetchList(item.path);
    }
  };

  const handleGoUp = () => {
    if (parent !== null) {
      fetchList(parent);
    }
  };

  const handleEditFile = async (item: FileItem) => {
    setEditingFile(item);
    setEditOpen(true);
    setEditReadonly(false);
    try {
      const res = await getFileContent(item.path);
      setEditContent(res.content);
    } catch (err: any) {
      setEditContent('');
      setEditReadonly(true);
      message.warning(err.response?.data?.detail || '无法读取文件内容');
    }
  };

  const handleSaveFile = async () => {
    if (!editingFile) return;
    setEditSaving(true);
    try {
      await saveFileContent(editingFile.path, editContent);
      message.success('文件已保存');
      setEditOpen(false);
      fetchList(currentPath);
    } catch (err: any) {
      message.error(err.response?.data?.detail || '保存失败');
    } finally {
      setEditSaving(false);
    }
  };

  const handleDelete = async (item: FileItem) => {
    try {
      await deleteFile(item.path);
      message.success('已删除');
      fetchList(currentPath);
    } catch (err: any) {
      message.error(err.response?.data?.detail || '删除失败');
    }
  };

  const handleRename = async (newName: string) => {
    if (!renameTarget) return;
    try {
      await renameFile(renameTarget.path, newName);
      message.success('已重命名');
      setRenameOpen(false);
      setRenameTarget(null);
      fetchList(currentPath);
    } catch (err: any) {
      message.error(err.response?.data?.detail || '重命名失败');
    }
  };

  const handleMkdir = async (dirName: string) => {
    try {
      await makeDir(currentPath, dirName);
      message.success('目录已创建');
      setMkdirOpen(false);
      fetchList(currentPath);
    } catch (err: any) {
      message.error(err.response?.data?.detail || '创建目录失败');
    }
  };

  const handleUpload = async (file: File) => {
    if (!currentPath) {
      message.warning('请先进入具体目录再上传文件');
      return false;
    }
    setUploading(true);
    try {
      await uploadFile(currentPath, file);
      message.success(`已上传: ${file.name}`);
      fetchList(currentPath);
    } catch (err: any) {
      message.error(err.response?.data?.detail || '上传失败');
    } finally {
      setUploading(false);
    }
    return false;
  };

  const formatSize = (bytes: number) => {
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
    return `${(bytes / 1024 / 1024).toFixed(2)} MB`;
  };

  const formatTime = (ts: number) => {
    if (!ts) return '-';
    return new Date(ts * 1000).toLocaleString('zh-CN');
  };

  const pathParts = currentPath ? currentPath.split('/') : [];

  const columns = [
    {
      title: '名称',
      dataIndex: 'name',
      key: 'name',
      render: (text: string, record: FileItem) => (
        <Space>
          {record.is_dir ? <FolderOutlined style={{ color: '#1890ff' }} /> : <FileOutlined />}
          <span
            style={{
              cursor: record.is_dir ? 'pointer' : 'default',
              color: record.is_dir ? '#1890ff' : 'inherit',
            }}
            onClick={() => handleEnterDir(record)}
          >
            {text}
          </span>
        </Space>
      ),
    },
    {
      title: '大小',
      dataIndex: 'size',
      key: 'size',
      width: 120,
      render: (size: number, record: FileItem) =>
        record.is_dir ? <Tag color="blue">目录</Tag> : formatSize(size),
    },
    {
      title: '修改时间',
      dataIndex: 'modified_at',
      key: 'modified_at',
      width: 180,
      render: (ts: number) => formatTime(ts),
    },
    {
      title: '操作',
      key: 'actions',
      width: 280,
      render: (_: unknown, record: FileItem) => (
        <Space size="small">
          {!record.is_dir && (
            <>
              <Tooltip title="编辑">
                <Button
                  type="text"
                  size="small"
                  icon={<EditOutlined />}
                  onClick={() => handleEditFile(record)}
                />
              </Tooltip>
              <Tooltip title="下载">
                <Button
                  type="text"
                  size="small"
                  icon={<DownloadOutlined />}
                  onClick={() => window.open(getDownloadUrl(record.path))}
                />
              </Tooltip>
            </>
          )}
          <Tooltip title="重命名">
            <Button
              type="text"
              size="small"
              icon={<FormOutlined />}
              onClick={() => {
                setRenameTarget(record);
                setRenameOpen(true);
              }}
            />
          </Tooltip>
          <Popconfirm
            title={`确定删除 ${record.name}？`}
            description={record.is_dir ? '目录将被递归删除' : ''}
            okText="删除"
            okType="danger"
            cancelText="取消"
            onConfirm={() => handleDelete(record)}
          >
            <Button type="text" size="small" danger icon={<DeleteOutlined />} />
          </Popconfirm>
        </Space>
      ),
    },
  ];

  return (
    <div>
      <Space style={{ marginBottom: 16, justifyContent: 'space-between', width: '100%' }}>
        <Title level={4} style={{ margin: 0 }}>文件管理</Title>
        <Space>
          <Upload
            beforeUpload={handleUpload}
            showUploadList={false}
            multiple
            disabled={!currentPath}
          >
            <Button icon={<UploadOutlined />} loading={uploading} disabled={!currentPath}>
              上传文件
            </Button>
          </Upload>
          <Button icon={<FolderAddOutlined />} onClick={() => setMkdirOpen(true)} disabled={!currentPath}>
            新建目录
          </Button>
          <Button icon={<ReloadOutlined />} onClick={() => fetchList(currentPath)}>
            刷新
          </Button>
        </Space>
      </Space>

      <Alert
        type="info"
        showIcon
        message="仅可访问配置中允许的目录（config、logs），所有操作将永久生效。"
        style={{ marginBottom: 16 }}
      />

      <Breadcrumb style={{ marginBottom: 16 }}>
        <Breadcrumb.Item>
          <Space>
            <HomeOutlined />
            <a onClick={() => fetchList('')}>根目录</a>
          </Space>
        </Breadcrumb.Item>
        {pathParts.map((part, idx) => (
          <Breadcrumb.Item key={idx}>
            <a onClick={() => fetchList(pathParts.slice(0, idx + 1).join('/'))}>{part}</a>
          </Breadcrumb.Item>
        ))}
      </Breadcrumb>

      {parent !== null && (
        <Button
          type="text"
          icon={<ArrowUpOutlined />}
          onClick={handleGoUp}
          style={{ marginBottom: 8 }}
        >
          返回上级
        </Button>
      )}

      <Table
        rowKey={(r) => r.path}
        columns={columns}
        dataSource={items}
        loading={loading}
        pagination={false}
        size="middle"
        onRow={(record) => ({
          onDoubleClick: () => {
            if (record.is_dir) handleEnterDir(record);
            else handleEditFile(record);
          },
        })}
      />

      <Drawer
        title={editingFile?.name || '编辑文件'}
        placement="right"
        width="60%"
        open={editOpen}
        onClose={() => setEditOpen(false)}
        extra={
          !editReadonly && (
            <Button type="primary" loading={editSaving} onClick={handleSaveFile}>
              保存
            </Button>
          )
        }
      >
        {editingFile && (
          <>
            <div style={{ marginBottom: 12, color: '#666' }}>
              路径: {editingFile.path} · 大小: {formatSize(editingFile.size)}
            </div>
            {editReadonly && (
              <Alert type="warning" showIcon message="该文件类型不支持在线编辑，仅可查看或下载" style={{ marginBottom: 12 }} />
            )}
            <TextArea
              value={editContent}
              onChange={(e) => setEditContent(e.target.value)}
              readOnly={editReadonly}
              style={{ fontFamily: 'monospace', fontSize: 13 }}
              autoSize={{ minRows: 30, maxRows: 50 }}
            />
          </>
        )}
      </Drawer>

      <Modal
        title="新建目录"
        open={mkdirOpen}
        onCancel={() => setMkdirOpen(false)}
        onOk={() => {
          const form = document.getElementById('mkdir-form') as HTMLFormElement;
          const input = form?.querySelector('input') as HTMLInputElement;
          if (input?.value) handleMkdir(input.value);
        }}
        okText="创建"
        cancelText="取消"
      >
        <Form id="mkdir-form">
          <Form.Item label="目录名称" rules={[{ required: true }]}>
            <Input placeholder="请输入目录名" />
          </Form.Item>
        </Form>
      </Modal>

      <Modal
        title="重命名"
        open={renameOpen}
        onCancel={() => {
          setRenameOpen(false);
          setRenameTarget(null);
        }}
        onOk={() => {
          const form = document.getElementById('rename-form') as HTMLFormElement;
          const input = form?.querySelector('input') as HTMLInputElement;
          if (input?.value) handleRename(input.value);
        }}
        okText="确定"
        cancelText="取消"
      >
        <Form id="rename-form">
          <Form.Item label="新名称" rules={[{ required: true }]}>
            <Input defaultValue={renameTarget?.name} />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  );
};

export default FileManager;
