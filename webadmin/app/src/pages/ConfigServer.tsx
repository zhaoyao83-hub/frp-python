import React from 'react';
import ConfigEditor from '../components/ConfigEditor';

// 服务端配置页：直接渲染 ConfigEditor
const ConfigServer: React.FC = () => {
  return <ConfigEditor type="server" />;
};

export default ConfigServer;
