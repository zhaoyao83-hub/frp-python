"""正向代理（Forward Proxy）模块。

支持两种模式：
1. CONNECT 方法 — HTTPS 隧道，建立 TCP 双向透传
2. 普通 HTTP 请求 — 解析绝对 URI，代替客户端请求目标服务器

用法：在 frps.json 中配置 forward_proxy_port 即可启用。
手机/浏览器设置 HTTP 代理指向 frps 的 forward_proxy_port。
"""

from __future__ import annotations

import asyncio
import socket
from typing import Optional
from urllib.parse import urlsplit

from log import get_logger

READ_BUF_SIZE = 65536

logger = get_logger("forward_proxy", "info")


def _optimize_socket(writer):
    """设置 TCP_NODELAY 和更大的缓冲区。"""
    sock = writer.get_extra_info("socket")
    if sock is None:
        return
    try:
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 256 * 1024)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 256 * 1024)
    except (OSError, AttributeError):
        pass


class ForwardProxy:
    """正向代理服务器。"""

    def __init__(
        self,
        access_control=None,
        auth_user: Optional[str] = None,
        auth_pass: Optional[str] = None,
    ):
        self.access = access_control
        self.auth_user = auth_user
        self.auth_pass = auth_pass
        self._active_conns = 0

    async def handle_conn(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        """处理客户端连接。"""
        addr = writer.get_extra_info("peername")
        ip = addr[0] if addr else "unknown"
        _optimize_socket(writer)

        if self.access and not self.access.check_ip(ip):
            writer.close()
            await writer.wait_closed()
            return

        self._active_conns += 1
        try:
            # 读取请求行
            try:
                request_line = await asyncio.wait_for(reader.readline(), timeout=30)
            except asyncio.TimeoutError:
                writer.close()
                await writer.wait_closed()
                return

            if not request_line:
                writer.close()
                await writer.wait_closed()
                return

            request_line_str = request_line.decode("ascii", errors="replace").strip()

            # 读取 headers
            headers_raw = b""
            while True:
                line = await asyncio.wait_for(reader.readline(), timeout=10)
                if not line or line == b"\r\n":
                    break
                headers_raw += line
                if len(headers_raw) > 16384:
                    break

            # 认证检查
            if self.auth_user and self.auth_pass:
                if not self._check_auth(headers_raw):
                    resp = (
                        b"HTTP/1.1 407 Proxy Authentication Required\r\n"
                        b"Proxy-Authenticate: Basic realm=\"Forward Proxy\"\r\n"
                        b"Content-Length: 0\r\n"
                        b"\r\n"
                    )
                    writer.write(resp)
                    await writer.drain()
                    writer.close()
                    await writer.wait_closed()
                    return

            # 分发：CONNECT 或普通 HTTP
            parts = request_line_str.split()
            if len(parts) < 3:
                writer.write(b"HTTP/1.1 400 Bad Request\r\nContent-Length: 0\r\n\r\n")
                await writer.drain()
                writer.close()
                await writer.wait_closed()
                return

            method = parts[0].upper()

            if method == "CONNECT":
                await self._handle_connect(parts[1], reader, writer)
            else:
                await self._handle_http(request_line, headers_raw, reader, writer)
        except Exception as e:
            logger.error(f"Forward proxy error from {ip}: {e}")
        finally:
            self._active_conns -= 1
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass

    def _check_auth(self, headers_raw: bytes) -> bool:
        """检查 Proxy-Authorization 头。"""
        import base64
        for line in headers_raw.split(b"\r\n"):
            if line.lower().startswith(b"proxy-authorization:"):
                value = line.split(b":", 1)[1].strip()
                if value.lower().startswith(b"basic "):
                    encoded = value[6:].strip()
                    try:
                        decoded = base64.b64decode(encoded).decode("utf-8")
                        user, _, passwd = decoded.partition(":")
                        return user == self.auth_user and passwd == self.auth_pass
                    except Exception:
                        return False
        return False

    async def _handle_connect(self, target: str, reader, writer):
        """处理 CONNECT 方法（HTTPS 隧道）。

        target 格式：host:port
        """
        if ":" in target:
            host, port_str = target.rsplit(":", 1)
            try:
                port = int(port_str)
            except ValueError:
                writer.write(b"HTTP/1.1 400 Bad Request\r\nContent-Length: 0\r\n\r\n")
                await writer.drain()
                return
        else:
            host = target
            port = 443

        logger.info(f"CONNECT {host}:{port}")

        # 连接目标服务器
        try:
            target_reader, target_writer = await asyncio.wait_for(
                asyncio.open_connection(host, port), timeout=10
            )
        except asyncio.TimeoutError:
            writer.write(b"HTTP/1.1 504 Gateway Timeout\r\nContent-Length: 0\r\n\r\n")
            await writer.drain()
            return
        except Exception as e:
            msg = str(e).encode("ascii", errors="replace")
            resp = (
                b"HTTP/1.1 502 Bad Gateway\r\n"
                b"Content-Type: text/plain\r\n"
                b"Content-Length: " + str(len(msg)).encode() + b"\r\n"
                b"\r\n" + msg
            )
            writer.write(resp)
            await writer.drain()
            return

        # 连接成功，回复 200
        writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
        await writer.drain()

        # 双向透传
        await self._tunnel(reader, writer, target_reader, target_writer)

    async def _handle_http(self, request_line: bytes, headers_raw: bytes, reader, writer):
        """处理普通 HTTP 请求（非 CONNECT）。

        解析绝对 URI，代替客户端请求目标服务器。
        """
        request_line_str = request_line.decode("ascii", errors="replace").strip()
        parts = request_line_str.split()
        if len(parts) < 3:
            writer.write(b"HTTP/1.1 400 Bad Request\r\nContent-Length: 0\r\n\r\n")
            await writer.drain()
            return

        method = parts[0]
        uri = parts[1]
        version = parts[2]

        # 解析绝对 URI: http://host:port/path
        parsed = urlsplit(uri)
        if not parsed.hostname:
            writer.write(
                b"HTTP/1.1 400 Bad Request\r\n"
                b"Content-Type: text/plain\r\n"
                b"Content-Length: 11\r\n\r\n"
                b"Bad Request"
            )
            await writer.drain()
            return

        host = parsed.hostname
        port = parsed.port or 80
        path = parsed.path or "/"
        if parsed.query:
            path += "?" + parsed.query

        logger.info(f"HTTP {method} {host}:{port}{path}")

        # 连接目标服务器
        try:
            target_reader, target_writer = await asyncio.wait_for(
                asyncio.open_connection(host, port), timeout=10
            )
        except asyncio.TimeoutError:
            writer.write(b"HTTP/1.1 504 Gateway Timeout\r\nContent-Length: 0\r\n\r\n")
            await writer.drain()
            return
        except Exception as e:
            msg = str(e).encode("ascii", errors="replace")
            resp = (
                b"HTTP/1.1 502 Bad Gateway\r\n"
                b"Content-Type: text/plain\r\n"
                b"Content-Length: " + str(len(msg)).encode() + b"\r\n"
                b"\r\n" + msg
            )
            writer.write(resp)
            await writer.drain()
            return

        # 重写请求行：绝对 URI → 相对路径
        new_request_line = f"{method} {path} {version}\r\n".encode("ascii")

        # 重写 headers：移除 Proxy-Authorization，确保 Host 正确
        new_headers = self._rewrite_headers(headers_raw, host, port)

        # 发送给目标服务器
        target_writer.write(new_request_line + new_headers)
        await target_writer.drain()

        # 读取请求 body（如有 Content-Length），转发给目标
        content_length = self._get_content_length(headers_raw)
        if content_length and content_length > 0:
            body_remaining = content_length
            while body_remaining > 0:
                chunk = await reader.read(min(READ_BUF_SIZE, body_remaining))
                if not chunk:
                    break
                target_writer.write(chunk)
                await target_writer.drain()
                body_remaining -= len(chunk)

        # 双向转发：目标响应 → 客户端
        await self._tunnel(target_reader, writer, reader, target_writer, reverse=True)

    def _rewrite_headers(self, headers_raw: bytes, host: str, port: int) -> bytes:
        """重写请求头：替换 Host，移除代理相关头。"""
        lines = headers_raw.split(b"\r\n")
        result = []
        has_host = False
        skip_headers = {b"proxy-authorization", b"proxy-connection"}

        for line in lines:
            if not line:
                continue
            lower = line.lower()
            # 跳过代理相关头
            skip = False
            for sh in skip_headers:
                if lower.startswith(sh):
                    skip = True
                    break
            if skip:
                continue

            if lower.startswith(b"host:"):
                if port != 80:
                    result.append(f"Host: {host}:{port}\r\n".encode("ascii"))
                else:
                    result.append(f"Host: {host}\r\n".encode("ascii"))
                has_host = True
            else:
                result.append(line + b"\r\n")

        if not has_host:
            if port != 80:
                result.append(f"Host: {host}:{port}\r\n".encode("ascii"))
            else:
                result.append(f"Host: {host}\r\n".encode("ascii"))

        return b"".join(result)

    def _get_content_length(self, headers_raw: bytes) -> int:
        """从 headers 中解析 Content-Length。"""
        for line in headers_raw.split(b"\r\n"):
            if line.lower().startswith(b"content-length:"):
                try:
                    return int(line.split(b":", 1)[1].strip())
                except ValueError:
                    return 0
        return 0

    async def _tunnel(self, r1, w1, r2, w2, reverse=False):
        """双向隧道透传。

        r1/w1 ↔ r2/w2
        """
        async def pipe(src_reader, dst_writer):
            try:
                while True:
                    data = await src_reader.read(READ_BUF_SIZE)
                    if not data:
                        break
                    dst_writer.write(data)
                    await dst_writer.drain()
            except (ConnectionError, OSError, asyncio.IncompleteReadError):
                pass
            finally:
                try:
                    dst_writer.close()
                except Exception:
                    pass

        if reverse:
            # r2→w1 (响应方向), r1→w2 (请求方向)
            await asyncio.gather(
                pipe(r2, w1),
                pipe(r1, w2),
                return_exceptions=True,
            )
        else:
            # r1→w2, r2→w1
            await asyncio.gather(
                pipe(r1, w2),
                pipe(r2, w1),
                return_exceptions=True,
            )
