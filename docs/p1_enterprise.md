# P1 生产级增强：可安全上线

> **实现状态说明**：P1 已全部实现。实际代码与本文档的设计方案在命名与实现上有以下差异：
> - TLS 配置：文档中 `tls_enable`，实际为 `tls`（bool）；客户端 `tls_insecure_skip_verify` 实际为 `tls_insecure`
> - 访问控制：文档中 `control_allow_ips` / `proxy_allow_ips` / `allow_port_ranges` / `max_conn_per_ip` 等，实际实现为 `ip_whitelist` / `ip_blacklist` / `allow_ports`，由 `access.py` 的 `AccessControl` 类提供 `check_port()` / `check_ip()`；未实现连接速率限制
> - Dashboard：文档中 `metrics_enable` / `metrics_port` + aiohttp `MetricsServer`，实际为 `webapi_port` / `webapi_addr` + frps.py 内嵌 `handle_webapi()`（asyncio 原生 HTTP，仅 `/stats` 接口，无前端）
> - 证书生成脚本 `scripts/gen_cert.py` 未实现，当前使用项目根目录的 `server.crt` / `server.key`
>
> 以下文档保留原始设计方案作为记录，以实际代码为准。

## 1. 概述

### 1.1 目标

在 P0 可用性增强的基础上，补齐安全、可观测性和运维能力，使系统达到**生产级标准**，可安全上线运营。

### 1.2 范围

| 能力 | 说明 |
|------|------|
| TLS 加密 | 控制通道和数据通道的 TLS 加密传输 |
| 访问控制 | IP 白名单、端口范围限制、代理权限控制 |
| 监控统计 | 连接数、流量、延迟等运行指标采集与暴露 |
| 优雅关闭 | 信号处理、连接平滑迁移、资源完整释放 |

### 1.3 前置条件

- P0 全部能力已实现（断线重连、超时清理、Token 认证、错误处理）

---

## 2. TLS 加密

### 2.1 问题分析

当前所有数据明文传输，包括：
- Token 认证信息（可被中间人抓包）
- 代理的业务数据（HTTP 请求、SSH 流量等）
- 控制消息（NEW_CONN、DATA 等）

### 2.2 设计方案

#### 2.2.1 证书体系

```
┌─────────────────────────────────────────────────┐
│                  证书体系                        │
├─────────────────────────────────────────────────┤
│                                                 │
│  方案一：自签名证书（内网/小规模部署）            │
│  ┌─────────────┐         ┌─────────────┐        │
│  │ CA 证书     │──签发──►│ 服务端证书   │        │
│  │ (自签名)    │         │ frps.crt    │        │
│  └─────────────┘         └─────────────┘        │
│         │                                       │
│         └──客户端信任──► 客户端配置 ca.crt       │
│                                                 │
│  方案二：Let's Encrypt（公网部署）               │
│  ┌─────────────┐         ┌─────────────┐        │
│  │ Let's       │──签发──►│ 服务端证书   │        │
│  │ Encrypt CA  │         │ (自动续期)   │        │
│  └─────────────┘         └─────────────┘        │
│                                                 │
└─────────────────────────────────────────────────┘
```

#### 2.2.2 证书生成脚本

```python
# scripts/gen_cert.py
import os
import subprocess
import argparse

def generate_self_signed_cert(output_dir):
    """生成自签名证书"""
    os.makedirs(output_dir, exist_ok=True)

    ca_key = os.path.join(output_dir, "ca.key")
    ca_crt = os.path.join(output_dir, "ca.crt")
    server_key = os.path.join(output_dir, "server.key")
    server_csr = os.path.join(output_dir, "server.csr")
    server_crt = os.path.join(output_dir, "server.crt")

    # 1. 生成 CA 私钥和证书
    subprocess.run([
        "openssl", "req", "-newkey", "rsa:2048",
        "-nodes", "-keyout", ca_key,
        "-x509", "-days", "3650",
        "-out", ca_crt,
        "-subj", "/CN=MyFRP-CA"
    ], check=True)

    # 2. 生成服务端私钥和 CSR
    subprocess.run([
        "openssl", "req", "-newkey", "rsa:2048",
        "-nodes", "-keyout", server_key,
        "-out", server_csr,
        "-subj", "/CN=frps-server"
    ], check=True)

    # 3. 用 CA 签发服务端证书
    subprocess.run([
        "openssl", "x509", "-req",
        "-in", server_csr,
        "-CA", ca_crt, "-CAkey", ca_key,
        "-CAcreateserial",
        "-out", server_crt,
        "-days", "3650"
    ], check=True)

    # 4. 清理 CSR
    os.remove(server_csr)

    print(f"Certificates generated in {output_dir}/")
    print(f"  CA:     {ca_crt}")
    print(f"  Server: {server_crt}, {server_key}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("-o", "--output", default="certs", help="Output directory")
    args = parser.parse_args()
    generate_self_signed_cert(args.output)
```

#### 2.2.3 服务端 TLS 实现

```python
import ssl

class FRPServer:
    def __init__(self, config):
        # ... 已有初始化 ...
        self.tls_config = {
            "enabled": config.get("tls_enable", False),
            "cert_file": config.get("tls_cert_file", ""),
            "key_file": config.get("tls_key_file", ""),
        }
        self.ssl_context = None

    def create_ssl_context(self):
        """创建服务端 SSL 上下文"""
        if not self.tls_config["enabled"]:
            return None

        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(
            certfile=self.tls_config["cert_file"],
            keyfile=self.tls_config["key_file"],
        )
        # 仅允许安全协议
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        return ctx

    async def start(self):
        bind_addr = self.config.get("bind_addr", "0.0.0.0")
        bind_port = self.config.get("bind_port", 7000)

        self.ssl_context = self.create_ssl_context()

        self.control_server = await asyncio.start_server(
            self.handle_control_conn,
            bind_addr,
            bind_port,
            ssl=self.ssl_context,  # 启用 TLS
        )

        self.logger.info(
            f"FRPServer started on {bind_addr}:{bind_port} "
            f"(TLS: {'enabled' if self.ssl_context else 'disabled'})"
        )

        # ... 启动清理任务等 ...
        async with self.control_server:
            await self.control_server.serve_forever()
```

#### 2.2.4 客户端 TLS 实现

```python
class FRPClient:
    def __init__(self, config):
        # ... 已有初始化 ...
        self.tls_config = {
            "enabled": config.get("tls_enable", False),
            "ca_file": config.get("tls_ca_file", ""),
            "server_name": config.get("tls_server_name", ""),
            "insecure_skip_verify": config.get("tls_insecure_skip_verify", False),
        }
        self.ssl_context = None

    def create_ssl_context(self):
        """创建客户端 SSL 上下文"""
        if not self.tls_config["enabled"]:
            return None

        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2

        if self.tls_config["ca_file"]:
            ctx.load_verify_locations(self.tls_config["ca_file"])
        else:
            ctx.load_default_certs()

        if self.tls_config["insecure_skip_verify"]:
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
        else:
            ctx.check_hostname = True
            ctx.verify_mode = ssl.CERT_REQUIRED

        return ctx

    async def connect_and_serve(self):
        server_addr = self.config.get("server_addr", "127.0.0.1")
        server_port = self.config.get("server_port", 7000)

        self.ssl_context = self.create_ssl_context()

        # 建立 TLS 连接
        self.control_reader, self.control_writer = await asyncio.wait_for(
            asyncio.open_connection(
                server_addr,
                server_port,
                ssl=self.ssl_context,
                server_hostname=self.tls_config["server_name"] or server_addr,
            ),
            timeout=10,
        )
        self.logger.info(f"Connected to server {server_addr}:{server_port} (TLS)")

        # ... 认证、注册代理等后续流程 ...
```

#### 2.2.5 配置扩展

服务端配置 (frps.json)：

```json
{
    "bind_port": 7000,
    "bind_addr": "0.0.0.0",
    "tls_enable": true,
    "tls_cert_file": "certs/server.crt",
    "tls_key_file": "certs/server.key",
    "auth_token": "my-secret-token-2026",
    "log_level": "info"
}
```

客户端配置 (frpc.json)：

```json
{
    "server_addr": "127.0.0.1",
    "server_port": 7000,
    "tls_enable": true,
    "tls_ca_file": "certs/ca.crt",
    "tls_server_name": "frps-server",
    "tls_insecure_skip_verify": false,
    "auth_token": "my-secret-token-2026",
    "log_level": "info",
    "proxies": [...]
}
```

| 参数 | 端 | 类型 | 默认值 | 说明 |
|------|-----|------|--------|------|
| tls_enable | 服务端+客户端 | bool | false | 是否启用 TLS |
| tls_cert_file | 服务端 | string | "" | 证书文件路径 |
| tls_key_file | 服务端 | string | "" | 私钥文件路径 |
| tls_ca_file | 客户端 | string | "" | CA 证书路径 |
| tls_server_name | 客户端 | string | "" | 服务端证书名称 |
| tls_insecure_skip_verify | 客户端 | bool | false | 是否跳过证书验证（仅调试用） |

---

## 3. 访问控制

### 3.1 问题分析

当前系统缺乏细粒度的访问控制：
- 任何 IP 都可以连接服务端控制端口
- 代理端口范围没有限制（客户端可注册任意端口）
- 外部用户访问代理端口无限制

### 3.2 设计方案

#### 3.2.1 访问控制架构

```
┌─────────────────────────────────────────────────────────────┐
│                      服务端访问控制                          │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  ┌─────────────────────────────────────────────────────┐   │
│  │ 1. 控制连接访问控制                                  │   │
│  │    - IP 白名单：哪些 IP 可连接控制端口               │   │
│  │    - Token 认证：P0 已实现                           │   │
│  └─────────────────────────────────────────────────────┘   │
│                                                             │
│  ┌─────────────────────────────────────────────────────┐   │
│  │ 2. 代理注册访问控制                                  │   │
│  │    - 端口范围限制：允许注册的 remote_port 范围        │   │
│  │    - 代理数量限制：每个客户端最多注册的代理数         │   │
│  └─────────────────────────────────────────────────────┘   │
│                                                             │
│  ┌─────────────────────────────────────────────────────┐   │
│  │ 3. 代理端口访问控制                                  │   │
│  │    - 外部 IP 白名单：哪些 IP 可访问代理端口          │   │
│  │    - 连接速率限制：防止单 IP 大量连接                │   │
│  └─────────────────────────────────────────────────────┘   │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

#### 3.2.2 控制连接 IP 白名单

```python
import ipaddress

class AccessControl:
    """访问控制管理器"""

    def __init__(self, config):
        # IP 白名单
        self.control_allow_ips = self._parse_ip_list(
            config.get("control_allow_ips", [])
        )
        # 代理端口白名单
        self.proxy_allow_ips = self._parse_ip_list(
            config.get("proxy_allow_ips", [])
        )
        # 允许的端口范围
        self.allow_port_ranges = self._parse_port_ranges(
            config.get("allow_port_ranges", "1000-65535")
        )
        # 每客户端最大代理数
        self.max_proxies_per_client = config.get("max_proxies_per_client", 10)
        # 外部连接速率限制
        self.max_conn_per_ip = config.get("max_conn_per_ip", 100)
        self._conn_counts = {}  # IP -> 连接计数

    def _parse_ip_list(self, ip_list):
        """解析 IP/网段列表"""
        networks = []
        for item in ip_list:
            try:
                if "/" in item:
                    networks.append(ipaddress.ip_network(item, strict=False))
                else:
                    networks.append(ipaddress.ip_network(item + "/32", strict=False))
            except ValueError:
                pass
        return networks

    def _parse_port_ranges(self, ranges_str):
        """解析端口范围配置"""
        ranges = []
        for part in ranges_str.split(","):
            part = part.strip()
            if "-" in part:
                start, end = part.split("-", 1)
                ranges.append((int(start), int(end)))
            else:
                port = int(part)
                ranges.append((port, port))
        return ranges

    def is_control_allowed(self, ip):
        """检查 IP 是否允许连接控制端口"""
        if not self.control_allow_ips:
            return True  # 未配置白名单则允许所有
        return self._ip_in_networks(ip, self.control_allow_ips)

    def is_proxy_access_allowed(self, ip):
        """检查外部 IP 是否允许访问代理端口"""
        if not self.proxy_allow_ips:
            return True
        return self._ip_in_networks(ip, self.proxy_allow_ips)

    def is_port_allowed(self, port):
        """检查端口是否在允许范围内"""
        for start, end in self.allow_port_ranges:
            if start <= port <= end:
                return True
        return False

    def _ip_in_networks(self, ip, networks):
        try:
            addr = ipaddress.ip_address(ip)
            for network in networks:
                if addr in network:
                    return True
        except ValueError:
            pass
        return False

    def check_conn_rate(self, ip):
        """检查连接速率"""
        count = self._conn_counts.get(ip, 0)
        if count >= self.max_conn_per_ip:
            return False
        self._conn_counts[ip] = count + 1
        return True

    def release_conn(self, ip):
        """释放连接计数"""
        count = self._conn_counts.get(ip, 0)
        if count > 0:
            self._conn_counts[ip] = count - 1
```

#### 3.2.3 服务端集成访问控制

```python
class FRPServer:
    def __init__(self, config):
        # ... 已有初始化 ...
        self.access_control = AccessControl(config)

    async def handle_control_conn(self, reader, writer):
        addr = writer.get_extra_info("peername")
        client_ip = addr[0] if addr else "unknown"

        # 1. IP 白名单检查
        if not self.access_control.is_control_allowed(client_ip):
            self.logger.warning(f"Control connection rejected from {client_ip} (not in whitelist)")
            writer.close()
            await writer.wait_closed()
            return

        self.logger.info(f"New control connection from {addr}")
        # ... 后续认证和处理 ...

    async def handle_register(self, message, writer, addr):
        proxy_name = message.payload.get("proxy_name")
        proxy_type = message.payload.get("proxy_type", "tcp")
        remote_port = message.payload.get("remote_port")

        # 2. 端口范围检查
        if not self.access_control.is_port_allowed(remote_port):
            error_msg = Message(
                MessageType.ERROR,
                message=f"Port {remote_port} not allowed"
            )
            writer.write(Protocol.encode(error_msg))
            await writer.drain()
            self.logger.warning(f"Port {remote_port} not allowed for proxy {proxy_name}")
            return

        # 3. 代理数量检查
        client_ip = addr[0] if addr else "unknown"
        current_count = sum(
            1 for p in self.state.proxies.values()
            if p.get("client_addr", (None,))[0] == client_ip
        )
        if current_count >= self.access_control.max_proxies_per_client:
            error_msg = Message(
                MessageType.ERROR,
                message=f"Max proxies ({self.access_control.max_proxies_per_client}) reached"
            )
            writer.write(Protocol.encode(error_msg))
            await writer.drain()
            return

        # ... 正常注册流程 ...

    async def handle_tcp_proxy_conn(self, reader, writer, proxy_name):
        addr = writer.get_extra_info("peername")
        client_ip = addr[0] if addr else "unknown"

        # 4. 代理端口访问控制
        if not self.access_control.is_proxy_access_allowed(client_ip):
            self.logger.warning(f"Proxy access rejected from {client_ip}")
            writer.close()
            await writer.wait_closed()
            return

        # 5. 连接速率限制
        if not self.access_control.check_conn_rate(client_ip):
            self.logger.warning(f"Rate limit exceeded for {client_ip}")
            writer.close()
            await writer.wait_closed()
            return

        try:
            # ... 正常代理处理 ...
        finally:
            self.access_control.release_conn(client_ip)
```

#### 3.2.4 配置扩展

```json
{
    "bind_port": 7000,
    "bind_addr": "0.0.0.0",
    "control_allow_ips": ["192.168.1.0/24", "10.0.0.0/8"],
    "proxy_allow_ips": [],
    "allow_port_ranges": "10000-20000,30000-40000",
    "max_proxies_per_client": 10,
    "max_conn_per_ip": 100,
    "auth_token": "my-secret-token-2026",
    "tls_enable": true,
    "log_level": "info"
}
```

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| control_allow_ips | array | [] | 控制连接 IP 白名单（CIDR），空为允许所有 |
| proxy_allow_ips | array | [] | 代理端口 IP 白名单（CIDR），空为允许所有 |
| allow_port_ranges | string | "1000-65535" | 允许注册的端口范围 |
| max_proxies_per_client | int | 10 | 每客户端最大代理数 |
| max_conn_per_ip | int | 100 | 单 IP 最大并发连接数 |

---

## 4. 监控统计

### 4.1 问题分析

当前系统缺乏运行时可见性：
- 不知道当前有多少活跃连接
- 不知道流量大小
- 不知道延迟情况
- 无法及时发现异常

### 4.2 设计方案

#### 4.2.1 监控指标定义

```python
import time
from collections import defaultdict

class Metrics:
    """运行时监控指标"""

    def __init__(self):
        self.start_time = time.time()

        # 连接统计
        self.total_control_conns = 0       # 控制连接总数
        self.active_control_conns = 0      # 活跃控制连接数
        self.total_proxy_conns = 0         # 代理连接总数
        self.active_proxy_conns = 0        # 活跃代理连接数

        # 流量统计
        self.bytes_sent = 0                # 发送字节总数
        self.bytes_received = 0            # 接收字节总数

        # 代理统计
        self.active_proxies = 0            # 活跃代理数

        # 心跳统计
        self.total_heartbeats = 0          # 心跳总数
        self.last_heartbeat_time = None    # 最后心跳时间

        # 错误统计
        self.total_errors = 0              # 错误总数
        self.errors_by_type = defaultdict(int)  # 按类型分类的错误

        # 延迟统计
        self.heartbeat_latencies = []      # 心跳延迟记录

        # 每代理统计
        self.proxy_stats = defaultdict(lambda: {
            "total_conns": 0,
            "active_conns": 0,
            "bytes_sent": 0,
            "bytes_received": 0,
        })

    def record_conn_open(self, conn_type, proxy_name=None):
        """记录连接打开"""
        if conn_type == "control":
            self.total_control_conns += 1
            self.active_control_conns += 1
        elif conn_type == "proxy":
            self.total_proxy_conns += 1
            self.active_proxy_conns += 1
            if proxy_name:
                self.proxy_stats[proxy_name]["total_conns"] += 1
                self.proxy_stats[proxy_name]["active_conns"] += 1

    def record_conn_close(self, conn_type, proxy_name=None):
        """记录连接关闭"""
        if conn_type == "control":
            self.active_control_conns -= 1
        elif conn_type == "proxy":
            self.active_proxy_conns -= 1
            if proxy_name:
                self.proxy_stats[proxy_name]["active_conns"] -= 1

    def record_traffic(self, direction, bytes_count, proxy_name=None):
        """记录流量"""
        if direction == "sent":
            self.bytes_sent += bytes_count
        else:
            self.bytes_received += bytes_count

        if proxy_name:
            key = "bytes_sent" if direction == "sent" else "bytes_received"
            self.proxy_stats[proxy_name][key] += bytes_count

    def record_heartbeat(self, latency_ms=None):
        """记录心跳"""
        self.total_heartbeats += 1
        self.last_heartbeat_time = time.time()
        if latency_ms is not None:
            self.heartbeat_latencies.append(latency_ms)
            # 只保留最近 100 条
            if len(self.heartbeat_latencies) > 100:
                self.heartbeat_latencies = self.heartbeat_latencies[-100:]

    def record_error(self, error_type):
        """记录错误"""
        self.total_errors += 1
        self.errors_by_type[error_type] += 1

    def get_stats(self):
        """获取统计摘要"""
        uptime = time.time() - self.start_time
        avg_latency = (
            sum(self.heartbeat_latencies) / len(self.heartbeat_latencies)
            if self.heartbeat_latencies else 0
        )

        return {
            "uptime_seconds": round(uptime, 1),
            "connections": {
                "control": {
                    "total": self.total_control_conns,
                    "active": self.active_control_conns,
                },
                "proxy": {
                    "total": self.total_proxy_conns,
                    "active": self.active_proxy_conns,
                },
            },
            "traffic": {
                "bytes_sent": self.bytes_sent,
                "bytes_received": self.bytes_received,
                "sent_mb": round(self.bytes_sent / 1024 / 1024, 2),
                "received_mb": round(self.bytes_received / 1024 / 1024, 2),
            },
            "proxies": {
                "active": self.active_proxies,
                "details": dict(self.proxy_stats),
            },
            "heartbeat": {
                "total": self.total_heartbeats,
                "avg_latency_ms": round(avg_latency, 2),
                "last_time": self.last_heartbeat_time,
            },
            "errors": {
                "total": self.total_errors,
                "by_type": dict(self.errors_by_type),
            },
        }
```

#### 4.2.2 HTTP 监控端点

```python
from aiohttp import web

class MetricsServer:
    """HTTP 监控服务"""

    def __init__(self, metrics, config):
        self.metrics = metrics
        self.port = config.get("metrics_port", 7500)
        self.addr = config.get("metrics_addr", "127.0.0.1")
        self.app = web.Application()
        self.app.router.add_get("/metrics", self.handle_metrics)
        self.app.router.add_get("/health", self.handle_health)
        self.runner = None

    async def start(self):
        self.runner = web.AppRunner(self.app)
        await self.runner.setup()
        site = web.TCPSite(self.runner, self.addr, self.port)
        await site.start()
        logging.getLogger("metrics").info(
            f"Metrics server started on {self.addr}:{self.port}"
        )

    async def stop(self):
        if self.runner:
            await self.runner.cleanup()

    async def handle_metrics(self, request):
        """返回 JSON 格式的监控指标"""
        stats = self.metrics.get_stats()
        return web.json_response(stats)

    async def handle_health(self, request):
        """健康检查端点"""
        stats = self.metrics.get_stats()
        healthy = stats["connections"]["control"]["active"] >= 0
        return web.json_response({
            "status": "healthy" if healthy else "degraded",
            "uptime": stats["uptime_seconds"],
        })
```

#### 4.2.3 服务端集成监控

```python
class FRPServer:
    def __init__(self, config):
        # ... 已有初始化 ...
        self.metrics = Metrics()
        self.metrics_server = None

        if config.get("metrics_enable", False):
            self.metrics_server = MetricsServer(self.metrics, config)

    async def start(self):
        # ... 启动控制服务 ...

        # 启动监控服务
        if self.metrics_server:
            await self.metrics_server.start()

        # ... 其他启动逻辑 ...

    async def handle_register(self, message, writer, addr):
        # ... 注册成功后 ...
        self.metrics.active_proxies += 1

    async def handle_tcp_proxy_conn(self, reader, writer, proxy_name):
        self.metrics.record_conn_open("proxy", proxy_name)
        try:
            # ... 代理处理 ...
        finally:
            self.metrics.record_conn_close("proxy", proxy_name)

    async def forward_proxy_data(self, conn_id):
        # ... 转发数据时 ...
        data = await conn_info["proxy_reader"].read(4096)
        if data:
            self.metrics.record_traffic("received", len(data), proxy_name)
            # ...

    async def handle_ping(self, writer, addr):
        receive_time = time.time()
        # ... 发送 PONG ...
        # 客户端收到 PONG 后可计算往返延迟

    async def stop(self):
        """优雅关闭"""
        if self.metrics_server:
            await self.metrics_server.stop()
        # ... 关闭其他资源 ...
```

#### 4.2.4 客户端心跳延迟测量

```python
class FRPClient:
    async def send_heartbeat(self):
        while True:
            try:
                ping_time = time.time()
                ping_msg = Message(MessageType.PING, timestamp=ping_time)
                self.control_writer.write(Protocol.encode(ping_msg))
                await self.control_writer.drain()
                await asyncio.sleep(30)
            except Exception as e:
                self.logger.error(f"Heartbeat failed: {e}")
                break

    # 客户端收到 PONG 时计算延迟
    async def process_message(self, message):
        # ...
        elif message.type == MessageType.PONG:
            if "timestamp" in message.payload:
                latency = (time.time() - message.payload["timestamp"]) * 1000
                self.logger.debug(f"Heartbeat latency: {latency:.1f}ms")
```

#### 4.2.5 配置扩展

```json
{
    "bind_port": 7000,
    "metrics_enable": true,
    "metrics_port": 7500,
    "metrics_addr": "127.0.0.1",
    "log_level": "info"
}
```

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| metrics_enable | bool | false | 是否启用监控服务 |
| metrics_port | int | 7500 | 监控服务端口 |
| metrics_addr | string | 127.0.0.1 | 监控服务绑定地址 |

#### 4.2.6 监控数据示例

```bash
# 获取监控指标
curl http://127.0.0.1:7500/metrics

# 响应示例
{
    "uptime_seconds": 3600.5,
    "connections": {
        "control": {"total": 3, "active": 1},
        "proxy": {"total": 156, "active": 12}
    },
    "traffic": {
        "bytes_sent": 5242880,
        "bytes_received": 10485760,
        "sent_mb": 5.0,
        "received_mb": 10.0
    },
    "proxies": {
        "active": 2,
        "details": {
            "web": {"total_conns": 150, "active_conns": 10, "bytes_sent": 4194304, "bytes_received": 8388608},
            "ssh": {"total_conns": 6, "active_conns": 2, "bytes_sent": 1048576, "bytes_received": 2097152}
        }
    },
    "heartbeat": {
        "total": 120,
        "avg_latency_ms": 15.3,
        "last_time": 1720612800.5
    },
    "errors": {
        "total": 2,
        "by_type": {"ConnectionRefusedError": 1, "TimeoutError": 1}
    }
}
```

```bash
# 健康检查
curl http://127.0.0.1:7500/health

# 响应示例
{"status": "healthy", "uptime": 3600.5}
```

---

## 5. 优雅关闭

### 5.1 问题分析

当前程序通过 `Ctrl+C` 退出时：
- 没有通知对端，导致对端需要超时检测
- 活跃连接被强制断开，可能导致数据丢失
- 资源（线程、文件句柄、socket）可能未正确释放

### 5.2 设计方案

#### 5.2.1 优雅关闭流程

```
收到 SIGINT/SIGTERM
        │
        ▼
┌───────────────────────┐
│ 1. 标记关闭中          │
│    running = False     │
└───────┬───────────────┘
        │
        ▼
┌───────────────────────┐
│ 2. 停止接受新连接      │
│    server.close()     │
└───────┬───────────────┘
        │
        ▼
┌───────────────────────┐
│ 3. 通知所有对端        │
│    发送 CLOSE 消息     │
└───────┬───────────────┘
        │
        ▼
┌───────────────────────┐
│ 4. 等待活跃连接完成    │
│    超时 30 秒          │
└───────┬───────────────┘
        │
        ▼
┌───────────────────────┐
│ 5. 清理资源            │
│    关闭所有连接        │
│    取消所有任务        │
│    关闭监控服务        │
└───────┬───────────────┘
        │
        ▼
┌───────────────────────┐
│ 6. 退出                │
└───────────────────────┘
```

#### 5.2.2 服务端优雅关闭

```python
import signal

class FRPServer:
    def __init__(self, config):
        # ... 已有初始化 ...
        self.shutdown_event = asyncio.Event()
        self.shutdown_timeout = config.get("shutdown_timeout", 30)

    async def start(self):
        # ... 启动服务 ...

        # 注册信号处理
        loop = asyncio.get_event_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, self._signal_handler)

        # 等待关闭信号
        await self.shutdown_event.wait()

        # 执行优雅关闭
        await self.graceful_shutdown()

    def _signal_handler(self):
        self.logger.info("Shutdown signal received, starting graceful shutdown...")
        self.shutdown_event.set()

    async def graceful_shutdown(self):
        """优雅关闭"""
        self.logger.info("Starting graceful shutdown...")

        # 1. 停止接受新连接
        self.control_server.close()
        await self.control_server.wait_closed()
        self.logger.info("Control server stopped accepting new connections")

        # 2. 关闭所有代理监听
        for proxy_name, server in list(self.proxy_servers.items()):
            try:
                if hasattr(server, 'close'):
                    server.close()
                    await server.wait_closed()
                elif hasattr(server, 'aclose'):
                    await server.aclose()
            except Exception as e:
                self.logger.error(f"Error closing proxy server {proxy_name}: {e}")
        self.proxy_servers.clear()
        self.logger.info("All proxy servers stopped")

        # 3. 通知所有客户端关闭
        for proxy_name, proxy_info in list(self.state.proxies.items()):
            try:
                writer = proxy_info.get("control_writer")
                if writer and not writer.is_closing():
                    close_msg = Message(MessageType.CLOSE, reason="server_shutdown")
                    writer.write(Protocol.encode(close_msg))
                    await writer.drain()
            except Exception:
                pass

        # 4. 等待活跃连接完成（带超时）
        if self.state.conn_pool:
            self.logger.info(f"Waiting for {len(self.state.conn_pool)} active connections to finish...")
            try:
                await asyncio.wait_for(
                    self._wait_connections_close(),
                    timeout=self.shutdown_timeout
                )
            except asyncio.TimeoutError:
                self.logger.warning(f"Shutdown timeout, force closing {len(self.state.conn_pool)} connections")

        # 5. 强制清理残留连接
        for conn_id in list(self.state.conn_pool.keys()):
            self.close_conn(conn_id)

        # 6. 停止清理任务
        if self.cleanup_task:
            self.cleanup_task.cancel()
            try:
                await self.cleanup_task
            except asyncio.CancelledError:
                pass

        # 7. 停止监控服务
        if self.metrics_server:
            await self.metrics_server.stop()

        self.logger.info("Graceful shutdown completed")

    async def _wait_connections_close(self):
        """等待所有活跃连接关闭"""
        while self.state.conn_pool:
            await asyncio.sleep(0.5)
```

#### 5.2.3 客户端优雅关闭

```python
class FRPClient:
    def __init__(self, config):
        # ... 已有初始化 ...
        self.shutdown_event = asyncio.Event()
        self.shutdown_timeout = config.get("shutdown_timeout", 10)

    async def start(self):
        loop = asyncio.get_event_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, self._signal_handler)

        self.running = True
        while self.running and not self.shutdown_event.is_set():
            try:
                await self.connect_and_serve()
            except Exception as e:
                self.logger.error(f"Connection error: {e}")

            if self.shutdown_event.is_set():
                break

            # ... 重连逻辑 ...

        # 优雅关闭
        await self.graceful_shutdown()

    def _signal_handler(self):
        self.logger.info("Shutdown signal received...")
        self.running = False
        self.shutdown_event.set()

    async def graceful_shutdown(self):
        """优雅关闭"""
        self.logger.info("Starting graceful shutdown...")

        # 1. 停止心跳
        if self.heartbeat_task:
            self.heartbeat_task.cancel()
            try:
                await self.heartbeat_task
            except asyncio.CancelledError:
                pass

        # 2. 关闭所有本地连接
        for conn_id in list(self.state.conn_pool.keys()):
            self.close_conn(conn_id)

        # 3. 通知服务端
        if self.control_writer and not self.control_writer.is_closing():
            try:
                close_msg = Message(MessageType.CLOSE, reason="client_shutdown")
                self.control_writer.write(Protocol.encode(close_msg))
                await self.control_writer.drain()
            except Exception:
                pass

            # 4. 关闭控制连接
            try:
                self.control_writer.close()
                await self.control_writer.wait_closed()
            except Exception:
                pass

        self.logger.info("Graceful shutdown completed")

    async def stop(self):
        """外部调用停止"""
        self._signal_handler()
        await self.graceful_shutdown()
```

#### 5.2.4 main 函数改造

```python
# frps.py
def main():
    import argparse

    parser = argparse.ArgumentParser(description="FRP Server")
    parser.add_argument("-c", "--config", default="config/frps.json", help="Config file")
    args = parser.parse_args()

    config = Config("server").load_from_file(args.config)
    server = FRPServer(config)

    try:
        asyncio.run(server.start())
    except KeyboardInterrupt:
        pass  # 信号处理器已处理


# frpc.py
def main():
    import argparse

    parser = argparse.ArgumentParser(description="FRP Client")
    parser.add_argument("-c", "--config", default="config/frpc.json", help="Config file")
    args = parser.parse_args()

    config = Config("client").load_from_file(args.config)
    client = FRPClient(config)

    try:
        asyncio.run(client.start())
    except KeyboardInterrupt:
        pass  # 信号处理器已处理
```

#### 5.2.5 配置扩展

```json
{
    "shutdown_timeout": 30,
    "log_level": "info"
}
```

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| shutdown_timeout | int | 30 (服务端) / 10 (客户端) | 优雅关闭超时时间（秒） |

---

## 6. 配置汇总

### 6.1 服务端完整配置 (frps.json)

```json
{
    "bind_port": 7000,
    "bind_addr": "0.0.0.0",
    "auth_token": "my-secret-token-2026",
    "tls_enable": true,
    "tls_cert_file": "certs/server.crt",
    "tls_key_file": "certs/server.key",
    "control_allow_ips": ["192.168.1.0/24"],
    "proxy_allow_ips": [],
    "allow_port_ranges": "10000-20000,30000-40000",
    "max_proxies_per_client": 10,
    "max_conn_per_ip": 100,
    "conn_timeout": 300,
    "heartbeat_timeout": 90,
    "cleanup_interval": 30,
    "read_timeout": 120,
    "metrics_enable": true,
    "metrics_port": 7500,
    "metrics_addr": "127.0.0.1",
    "shutdown_timeout": 30,
    "log_level": "info",
    "log_file": "frps.log"
}
```

### 6.2 客户端完整配置 (frpc.json)

```json
{
    "server_addr": "127.0.0.1",
    "server_port": 7000,
    "auth_token": "my-secret-token-2026",
    "tls_enable": true,
    "tls_ca_file": "certs/ca.crt",
    "tls_server_name": "frps-server",
    "tls_insecure_skip_verify": false,
    "reconnect_base_delay": 1.0,
    "reconnect_max_delay": 60.0,
    "reconnect_max_retries": -1,
    "shutdown_timeout": 10,
    "log_level": "info",
    "log_file": "frpc.log",
    "proxies": [
        {
            "name": "web",
            "type": "tcp",
            "local_ip": "127.0.0.1",
            "local_port": 8000,
            "remote_port": 10080
        }
    ]
}
```

### 6.3 新增配置项汇总

| 参数 | 端 | 类型 | 默认值 | 说明 |
|------|-----|------|--------|------|
| tls_enable | 服务端+客户端 | bool | false | 启用 TLS |
| tls_cert_file | 服务端 | string | "" | 证书文件 |
| tls_key_file | 服务端 | string | "" | 私钥文件 |
| tls_ca_file | 客户端 | string | "" | CA 证书 |
| tls_server_name | 客户端 | string | "" | 服务端名称 |
| tls_insecure_skip_verify | 客户端 | bool | false | 跳过证书验证 |
| control_allow_ips | 服务端 | array | [] | 控制 IP 白名单 |
| proxy_allow_ips | 服务端 | array | [] | 代理 IP 白名单 |
| allow_port_ranges | 服务端 | string | "1000-65535" | 端口范围 |
| max_proxies_per_client | 服务端 | int | 10 | 最大代理数 |
| max_conn_per_ip | 服务端 | int | 100 | 单 IP 最大连接 |
| metrics_enable | 服务端 | bool | false | 启用监控 |
| metrics_port | 服务端 | int | 7500 | 监控端口 |
| metrics_addr | 服务端 | string | 127.0.0.1 | 监控地址 |
| shutdown_timeout | 服务端+客户端 | int | 30/10 | 关闭超时 |

---

## 7. 测试验证

### 7.1 TLS 加密测试

```bash
# 1. 生成证书
python scripts/gen_cert.py -o certs/

# 2. 启动服务端（配置 tls_enable=true）
python frps.py -c config/frps.json

# 3. 启动客户端（配置 tls_enable=true, tls_ca_file=certs/ca.crt）
python frpc.py -c config/frpc.json

# 4. 验证：用 Wireshark 抓包，确认控制通道为 TLS 加密
```

### 7.2 访问控制测试

```bash
# 1. 配置 control_allow_ips 仅允许 127.0.0.1
# 2. 从其他 IP 连接，预期被拒绝

# 3. 配置 allow_port_ranges 为 "10000-20000"
# 4. 客户端注册 remote_port=8080，预期返回 ERROR
# 5. 客户端注册 remote_port=10080，预期成功
```

### 7.3 监控统计测试

```bash
# 1. 启动服务端（配置 metrics_enable=true）
# 2. 建立代理连接，传输数据
# 3. 查询监控指标
curl http://127.0.0.1:7500/metrics
# 预期：返回 JSON 格式的连接数、流量等指标

# 4. 健康检查
curl http://127.0.0.1:7500/health
# 预期：返回 {"status": "healthy", "uptime": ...}
```

### 7.4 优雅关闭测试

```bash
# 1. 启动服务端和客户端，建立代理连接
# 2. 向服务端发送 SIGTERM
kill -TERM <frps_pid>

# 预期：
# - 日志输出 "Starting graceful shutdown..."
# - 等待活跃连接完成（最多 30 秒）
# - 日志输出 "Graceful shutdown completed"
# - 客户端收到 CLOSE 消息，触发重连

# 3. 向客户端发送 SIGTERM
kill -TERM <frpc_pid>

# 预期：
# - 客户端通知服务端后关闭
# - 服务端清理该客户端的代理
```

---

## 8. 实施计划

| 任务 | 依赖 | 说明 |
|------|------|------|
| 证书生成脚本 | 无 | scripts/gen_cert.py |
| TLS 加密实现 | 证书脚本 | 修改 frps.py, frpc.py |
| AccessControl 模块 | 无 | 新建 access_control.py |
| 访问控制集成 | AccessControl | 修改 frps.py |
| Metrics 模块 | 无 | 新建 metrics.py |
| HTTP 监控端点 | Metrics | 新建 metrics_server.py |
| 监控集成 | Metrics+HTTP | 修改 frps.py, frpc.py |
| 信号处理 | 无 | 修改 frps.py, frpc.py |
| 优雅关闭 | 信号处理 | 修改 frps.py, frpc.py |
| 配置扩展 | 各模块 | 修改 config.py |
| 集成测试 | 所有任务 | 端到端验证 |

---

## 9. 部署检查清单

### 9.1 上线前检查

- [ ] TLS 证书已生成且未过期
- [ ] auth_token 已配置且客户端服务端一致
- [ ] IP 白名单已配置
- [ ] 端口范围已限制
- [ ] 监控服务已启用且可访问
- [ ] 日志文件路径已配置且有写入权限
- [ ] 防火墙规则已配置（仅开放必要端口）
- [ ] 优雅关闭超时时间已合理配置

### 9.2 运行时检查

```bash
# 检查健康状态
curl http://127.0.0.1:7500/health

# 检查连接数
curl http://127.0.0.1:7500/metrics | python -m json.tool | grep active

# 检查错误
curl http://127.0.0.1:7500/metrics | python -m json.tool | grep errors

# 检查日志
tail -f frps.log | grep -E "ERROR|WARNING"
```
