import React from 'react';
import ConfigEditor from '../components/ConfigEditor';

// 客户端配置页：直接渲染 ConfigEditor
const ConfigClient: React.FC = () => {
  return <ConfigEditor type="client" />;
};

export default ConfigClient;
