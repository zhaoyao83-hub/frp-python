import React, { useEffect, useRef } from 'react';

interface LogViewerProps {
  lines: string[];
  // 是否锁定自动滚动到底部（默认 true）
  autoScroll?: boolean;
  // 容器高度（默认 480px）
  height?: number | string;
}

// 日志查看器：终端风格（黑底绿字）、等宽字体、自动滚动到底部
const LogViewer: React.FC<LogViewerProps> = ({ lines, autoScroll = true, height = 480 }) => {
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (autoScroll && containerRef.current) {
      containerRef.current.scrollTop = containerRef.current.scrollHeight;
    }
  }, [lines, autoScroll]);

  return (
    <div
      ref={containerRef}
      style={{
        height,
        overflow: 'auto',
        backgroundColor: '#0a0a0a',
        color: '#33ff33',
        fontFamily: '"Menlo", "Monaco", "Consolas", "Courier New", monospace',
        fontSize: 13,
        lineHeight: 1.5,
        padding: 12,
        margin: 0,
        border: '1px solid #303030',
        borderRadius: 4,
        whiteSpace: 'pre-wrap',
        wordBreak: 'break-all',
      }}
    >
      {lines.length === 0 ? (
        <span style={{ color: '#666' }}>暂无日志</span>
      ) : (
        lines.map((line, idx) => (
          <div key={idx} style={{ minHeight: '1.5em' }}>
            {line || ' '}
          </div>
        ))
      )}
    </div>
  );
};

export default LogViewer;
