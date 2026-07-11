import React from 'react';
import ReactDOM from 'react-dom/client';
import App from './App';

// 应用入口：挂载 React
ReactDOM.createRoot(document.getElementById('root') as HTMLElement).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
);
