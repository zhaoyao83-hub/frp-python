// 格式化工具函数

// 格式化字节数为可读字符串
export function formatBytes(n: number | undefined | null, decimals = 2): string {
  if (n === undefined || n === null || isNaN(n)) return '0 B';
  if (n === 0) return '0 B';
  const k = 1024;
  const sizes = ['B', 'KB', 'MB', 'GB', 'TB', 'PB'];
  const i = Math.floor(Math.log(n) / Math.log(k));
  const index = Math.min(i, sizes.length - 1);
  return parseFloat((n / Math.pow(k, index)).toFixed(decimals)) + ' ' + sizes[index];
}

// 格式化时长（秒 -> 1h 2m 3s）
export function formatDuration(seconds: number | undefined | null): string {
  if (seconds === undefined || seconds === null || isNaN(seconds) || seconds < 0) return '-';
  const s = Math.floor(seconds);
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  const parts: string[] = [];
  if (h > 0) parts.push(`${h}h`);
  if (m > 0) parts.push(`${m}m`);
  if (sec > 0 || parts.length === 0) parts.push(`${sec}s`);
  return parts.join(' ');
}

// 格式化时间戳为 YYYY-MM-DD HH:mm:ss
export function formatTimestamp(ts: number | string | undefined | null): string {
  if (ts === undefined || ts === null || ts === '') return '-';
  let d: Date;
  if (typeof ts === 'number') {
    // 兼容秒级和毫秒级时间戳
    d = ts > 1e12 ? new Date(ts) : new Date(ts * 1000);
  } else {
    d = new Date(ts);
  }
  if (isNaN(d.getTime())) return '-';
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
}
