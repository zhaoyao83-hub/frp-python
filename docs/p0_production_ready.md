# P0 可用性增强：稳定跑通基础场景

> **实现状态说明**：P0 已全部实现。实际代码与本文档的设计方案在命名上有以下差异：
> - 消息类型：文档中 `AUTH` / `AUTH_OK`，实际实现为 `LOGIN` / `LOGIN_RESP`
> - 重连策略：文档中独立 `ReconnectPolicy` 类，实际实现在 `FRPClient.start()` 中内联（指数退避 `delay = min(base_delay * 2^(retry-1), max_delay)`，无随机抖动）
> - 配置项：实际 `reconnect_max_retries=0` 表示无限重试（非文档的 -1）
> - 超时清理：实际使用 `idle_timeout`（默认 300 秒）+ `idle_cleanup_loop`（每 60 秒），无独立的 `heartbeat_timeout`
> - 服务端状态：实际无 `ServerState` 类，直接用 `self.proxies` / `self.conn_pool`
>
> 以下文档保留原始设计方案作为记录，以实际代码为准。

## 1. 概述

### 1.1 目标

在当前最小可用实现的基础上，补齐基础可用性能力，使系统能**稳定跑通基础场景**，不因网络抖动、异常断开、配置错误等问题崩溃或泄漏资源。

### 1.2 范围

| 能力 | 说明 |
|------|------|
| 断线重连 | 客户端检测到控制连接断开后自动重连 |
| 超时清理 | 服务端清理超时空闲连接，防止资源泄漏 |
| Token 认证 | 客户端连接时携带 Token，服务端校验 |
| 错误处理 | 全链路异常捕获与恢复 |

### 1.3 非目标（P1 处理）

- TLS 加密传输
- 访问控制（IP 白名单、端口范围限制）
- 监控统计
- 优雅关闭

---

## 2. 断线重连

### 2.1 问题分析

当前客户端在控制连接断开后直接退出：

```python
# frpc.py - handle_server_messages()
data = await self.control_reader.read(4096)
if not data:
    self.logger.info("Server connection closed")
    break  # ← 直接退出，不重连
```

**问题**：网络抖动、服务端重启、NAT 超时等都会导致连接断开，客户端应自动重连。

### 2.2 设计方案

#### 2.2.1 重连策略

```
连接断开
    │
    ▼
等待 backoff 时间
    │
    ▼
尝试重连 ──失败──► 等待更长时间 ──► 再试
    │
    成功
    │
    ▼
重新注册所有代理
    │
    ▼
恢复心跳
```

#### 2.2.2 指数退避算法

```python
import random

class ReconnectPolicy:
    def __init__(self, base_delay=1.0, max_delay=60.0, max_retries=-1):
        self.base_delay = base_delay      # 初始延迟 1秒
        self.max_delay = max_delay        # 最大延迟 60秒
        self.max_retries = max_retries    # -1 表示无限重试
        self.retries = 0

    def get_delay(self):
        """指数退避 + 随机抖动"""
        delay = min(self.base_delay * (2 ** self.retries), self.max_delay)
        jitter = random.uniform(0, delay * 0.1)  # 10% 随机抖动
        return delay + jitter

    def next(self):
        self.retries += 1
        if self.max_retries >= 0 and self.retries > self.max_retries:
            return None  # 超过最大重试次数
        return self.get_delay()

    def reset(self):
        self.retries = 0
```

**退避时间示例**：

| 重试次数 | 基础延迟 | 抖动范围 | 实际延迟 |
|----------|----------|----------|----------|
| 1 | 1s | 0-0.1s | 1.0-1.1s |
| 2 | 2s | 0-0.2s | 2.0-2.2s |
| 3 | 4s | 0-0.4s | 4.0-4.4s |
| 4 | 8s | 0-0.8s | 8.0-8.8s |
| 5 | 16s | 0-1.6s | 16.0-17.6s |
| 6 | 32s | 0-3.2s | 32.0-35.2s |
| 7+ | 60s | 0-6.0s | 60.0-66.0s |

#### 2.2.3 客户端改造

```python
class FRPClient:
    def __init__(self, config):
        self.config = config
        self.logger = get_logger("frpc", config.get("log_level"), config.get("log_file"))
        self.state = ClientState()
        self.control_reader = None
        self.control_writer = None
        self.heartbeat_task = None
        self.reconnect_policy = ReconnectPolicy(
            base_delay=config.get("reconnect_base_delay", 1.0),
            max_delay=config.get("reconnect_max_delay", 60.0),
            max_retries=config.get("reconnect_max_retries", -1),
        )
        self.running = False  # 控制主循环退出

    async def start(self):
        self.running = True
        while self.running:
            try:
                await self.connect_and_serve()
            except Exception as e:
                self.logger.error(f"Connection error: {e}")

            if not self.running:
                break

            delay = self.reconnect_policy.next()
            if delay is None:
                self.logger.error("Max reconnect retries reached, exiting")
                break

            self.logger.info(f"Reconnecting in {delay:.1f}s...")
            await asyncio.sleep(delay)

    async def connect_and_serve(self):
        """单次连接生命周期"""
        server_addr = self.config.get("server_addr", "127.0.0.1")
        server_port = self.config.get("server_port", 7000)

        # 1. 建立连接
        self.control_reader, self.control_writer = await asyncio.wait_for(
            asyncio.open_connection(server_addr, server_port), timeout=10
        )
        self.logger.info(f"Connected to server {server_addr}:{server_port}")

        # 2. 重置退避计数
        self.reconnect_policy.reset()

        # 3. 发送认证（见第4节）
        await self.authenticate()

        # 4. 重新注册所有代理
        await self.register_proxies()

        # 5. 启动心跳
        self.heartbeat_task = asyncio.create_task(self.send_heartbeat())

        # 6. 处理服务端消息
        try:
            await self.handle_server_messages()
        finally:
            await self.cleanup_connection()

    async def cleanup_connection(self):
        """清理当前连接相关资源"""
        if self.heartbeat_task:
            self.heartbeat_task.cancel()
            try:
                await self.heartbeat_task
            except asyncio.CancelledError:
                pass

        # 关闭所有本地连接
        for conn_id in list(self.state.conn_pool.keys()):
            self.close_conn(conn_id)

        # 关闭控制连接
        if self.control_writer:
            try:
                self.control_writer.close()
                await self.control_writer.wait_closed()
            except Exception:
                pass

        self.logger.info("Connection cleaned up")
```

#### 2.2.4 配置扩展

```json
{
    "server_addr": "127.0.0.1",
    "server_port": 7000,
    "reconnect_base_delay": 1.0,
    "reconnect_max_delay": 60.0,
    "reconnect_max_retries": -1,
    "log_level": "info"
}
```

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| reconnect_base_delay | float | 1.0 | 初始重连延迟（秒） |
| reconnect_max_delay | float | 60.0 | 最大重连延迟（秒） |
| reconnect_max_retries | int | -1 | 最大重试次数，-1 为无限 |

---

## 3. 超时清理

### 3.1 问题分析

当前实现存在以下资源泄漏风险：

| 场景 | 当前行为 | 问题 |
|------|----------|------|
| 外部用户连接后不发送数据 | conn_pool 中保留连接状态 | 内存泄漏 |
| 客户端本地连接建立失败 | 发送 CLOSE，但若 CLOSE 丢失 | 服务端连接状态残留 |
| client_ready 永远不被 set | forward_proxy_data 永久阻塞 | 任务泄漏 |
| 心跳超时（客户端崩溃） | 服务端不检测 | 代理状态残留 |

### 3.2 设计方案

#### 3.2.1 服务端连接超时清理

```python
import time

class ServerState:
    def __init__(self):
        self.proxies = {}
        self.clients = {}
        self.conn_pool = {}

class FRPServer:
    def __init__(self, config):
        # ... 已有初始化 ...
        self.conn_timeout = config.get("conn_timeout", 300)  # 连接超时 5分钟
        self.heartbeat_timeout = config.get("heartbeat_timeout", 90)  # 心跳超时 90秒
        self.cleanup_task = None

    async def start(self):
        # ... 启动控制服务 ...

        # 启动定期清理任务
        self.cleanup_task = asyncio.create_task(self.periodic_cleanup())

        async with self.control_server:
            await self.control_server.serve_forever()

    async def periodic_cleanup(self):
        """定期清理超时连接"""
        while True:
            await asyncio.sleep(30)  # 每30秒清理一次
            await self.cleanup_timeout_conns()
            await self.cleanup_dead_clients()

    async def cleanup_timeout_conns(self):
        """清理超时空闲连接"""
        now = time.time()
        expired = []

        for conn_id, conn_info in self.state.conn_pool.items():
            created_at = conn_info.get("created_at", now)
            if now - created_at > self.conn_timeout:
                expired.append(conn_id)

        for conn_id in expired:
            self.logger.warning(f"Cleaning up timeout connection: {conn_id}")
            self.close_conn(conn_id)

        if expired:
            self.logger.info(f"Cleaned up {len(expired)} timeout connections")

    async def cleanup_dead_clients(self):
        """清理心跳超时的客户端"""
        now = time.time()
        dead_clients = []

        for client_addr, client_info in self.state.clients.items():
            last_heartbeat = client_info.get("last_heartbeat", now)
            if now - last_heartbeat > self.heartbeat_timeout:
                dead_clients.append(client_addr)

        for client_addr in dead_clients:
            self.logger.warning(f"Cleaning up dead client: {client_addr}")
            self.cleanup_client(client_addr)
```

#### 3.2.2 连接创建时记录时间

```python
# frps.py - handle_tcp_proxy_conn()
self.state.conn_pool[conn_id] = {
    "proxy_name": proxy_name,
    "proxy_reader": reader,
    "proxy_writer": writer,
    "client_writer": None,
    "client_ready": asyncio.Event(),
    "created_at": time.time(),          # 新增：创建时间
    "last_active": time.time(),         # 新增：最后活跃时间
}
```

#### 3.2.3 client_ready 超时机制

```python
# frps.py - forward_proxy_data()
async def forward_proxy_data(self, conn_id):
    conn_info = self.state.conn_pool.get(conn_id)
    if not conn_info:
        return

    try:
        # 等待客户端就绪，超时 10 秒
        try:
            await asyncio.wait_for(
                conn_info["client_ready"].wait(),
                timeout=10
            )
        except asyncio.TimeoutError:
            self.logger.error(f"Client not ready within 10s for conn_id: {conn_id}")
            self.close_conn(conn_id)
            return

        # 客户端就绪后开始转发
        while True:
            data = await conn_info["proxy_reader"].read(4096)
            if not data:
                break
            conn_info["last_active"] = time.time()  # 更新活跃时间
            conn_info["client_writer"].write(data)
            await conn_info["client_writer"].drain()

    except Exception as e:
        self.logger.error(f"Error forwarding proxy data: {e}")
    finally:
        self.close_conn(conn_id)
```

#### 3.2.4 心跳超时检测

```python
# frps.py - handle_ping()
async def handle_ping(self, writer, addr):
    # 更新心跳时间
    if addr in self.state.clients:
        self.state.clients[addr]["last_heartbeat"] = time.time()

    pong_msg = Message(MessageType.PONG)
    writer.write(Protocol.encode(pong_msg))
    await writer.drain()

# frps.py - handle_control_conn()
async def handle_control_conn(self, reader, writer):
    addr = writer.get_extra_info("peername")
    self.logger.info(f"New control connection from {addr}")

    # 记录客户端信息
    self.state.clients[addr] = {
        "writer": writer,
        "last_heartbeat": time.time(),
    }

    # ... 后续处理 ...
```

#### 3.2.5 配置扩展

```json
{
    "bind_port": 7000,
    "bind_addr": "0.0.0.0",
    "conn_timeout": 300,
    "heartbeat_timeout": 90,
    "cleanup_interval": 30,
    "log_level": "info"
}
```

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| conn_timeout | int | 300 | 连接最大空闲时间（秒） |
| heartbeat_timeout | int | 90 | 心跳超时时间（秒），3个心跳周期 |
| cleanup_interval | int | 30 | 清理任务执行间隔（秒） |

---

## 4. Token 认证

### 4.1 问题分析

当前任何客户端都可以连接服务端并注册代理，没有身份验证机制。

### 4.2 设计方案

#### 4.2.1 认证流程

```
客户端                              服务端
    │                                  │
    │── TCP连接 ──────────────────────►│
    │                                  │
    │── AUTH(token) ──────────────────►│
    │                                  │
    │                          ┌───────┴───────┐
    │                          │ 校验 Token     │
    │                          │ token == 配置? │
    │                          └───────┬───────┘
    │                                  │
    │                  ┌───────────────┼───────────────┐
    │                  ▼                               ▼
    │           Token 正确                       Token 错误
    │                  │                               │
    │◄── AUTH_OK ─────│                  ┌─────────────┴─────────────┐
    │                  │                  │ 返回 ERROR 消息            │
    │                  │                  │ 关闭连接                   │
    │                  │                  └───────────────────────────┘
    │                  │
    │── REGISTER ─────│
    │                  │
    │── 正常通信 ─────│
    │                  │
```

#### 4.2.2 协议扩展

```python
# protocol.py
class MessageType:
    REGISTER = "register"
    NEW_CONN = "new_conn"
    INIT_CONN = "init_conn"
    AUTH = "auth"           # 新增：认证请求
    AUTH_OK = "auth_ok"     # 新增：认证成功
    PING = "ping"
    PONG = "pong"
    ERROR = "error"
    CLOSE = "close"
    DATA = "data"
```

#### 4.2.3 服务端实现

```python
# frps.py
class FRPServer:
    def __init__(self, config):
        # ... 已有初始化 ...
        self.auth_token = config.get("auth_token", "")  # 空字符串表示不启用认证

    async def handle_control_conn(self, reader, writer):
        addr = writer.get_extra_info("peername")
        self.logger.info(f"New control connection from {addr}")

        buffer = b""
        client_id = None
        authenticated = not self.auth_token  # 未配置 token 则无需认证

        try:
            while True:
                data = await reader.read(4096)
                if not data:
                    break

                buffer += data

                while True:
                    message, buffer = Protocol.decode(buffer)
                    if not message:
                        break

                    # 未认证时只处理 AUTH 消息
                    if not authenticated:
                        if message.type == MessageType.AUTH:
                            authenticated = await self.handle_auth(message, writer, addr)
                            if not authenticated:
                                self.logger.warning(f"Authentication failed from {addr}")
                                writer.close()
                                await writer.wait_closed()
                                return
                        else:
                            self.logger.warning(f"Unauthenticated message from {addr}, closing")
                            writer.close()
                            await writer.wait_closed()
                            return
                        continue

                    # 已认证，正常处理
                    await self.process_message(message, writer, addr)

        except asyncio.CancelledError:
            pass
        except Exception as e:
            self.logger.error(f"Control connection error: {e}")
        finally:
            self.cleanup_client(client_id)
            writer.close()
            await writer.wait_closed()
            self.logger.info(f"Control connection closed: {addr}")

    async def handle_auth(self, message, writer, addr):
        token = message.payload.get("token", "")
        if token == self.auth_token:
            auth_ok_msg = Message(MessageType.AUTH_OK)
            writer.write(Protocol.encode(auth_ok_msg))
            await writer.drain()
            self.logger.info(f"Authentication successful from {addr}")
            return True
        else:
            error_msg = Message(MessageType.ERROR, message="Invalid token")
            writer.write(Protocol.encode(error_msg))
            await writer.drain()
            return False
```

#### 4.2.4 客户端实现

```python
# frpc.py
class FRPClient:
    def __init__(self, config):
        # ... 已有初始化 ...
        self.auth_token = config.get("auth_token", "")

    async def authenticate(self):
        """发送认证请求并等待响应"""
        if not self.auth_token:
            return  # 未配置 token，跳过认证

        auth_msg = Message(MessageType.AUTH, token=self.auth_token)
        self.control_writer.write(Protocol.encode(auth_msg))
        await self.control_writer.drain()

        # 等待 AUTH_OK 响应（超时 10 秒）
        try:
            data = await asyncio.wait_for(self.control_reader.read(4096), timeout=10)
            message, _ = Protocol.decode(data)
            if message and message.type == MessageType.AUTH_OK:
                self.logger.info("Authentication successful")
                return
            elif message and message.type == MessageType.ERROR:
                raise Exception(f"Authentication failed: {message.payload.get('message')}")
            else:
                raise Exception(f"Unexpected response: {message}")
        except asyncio.TimeoutError:
            raise Exception("Authentication timeout")

    async def connect_and_serve(self):
        server_addr = self.config.get("server_addr", "127.0.0.1")
        server_port = self.config.get("server_port", 7000)

        # 1. 建立连接
        self.control_reader, self.control_writer = await asyncio.wait_for(
            asyncio.open_connection(server_addr, server_port), timeout=10
        )
        self.logger.info(f"Connected to server {server_addr}:{server_port}")

        # 2. 认证
        await self.authenticate()

        # 3. 重置退避计数
        self.reconnect_policy.reset()

        # 4. 重新注册所有代理
        await self.register_proxies()

        # 5. 启动心跳
        self.heartbeat_task = asyncio.create_task(self.send_heartbeat())

        # 6. 处理服务端消息
        try:
            await self.handle_server_messages()
        finally:
            await self.cleanup_connection()
```

#### 4.2.5 配置扩展

服务端配置 (frps.json)：

```json
{
    "bind_port": 7000,
    "bind_addr": "0.0.0.0",
    "auth_token": "my-secret-token-2026",
    "log_level": "info"
}
```

客户端配置 (frpc.json)：

```json
{
    "server_addr": "127.0.0.1",
    "server_port": 7000,
    "auth_token": "my-secret-token-2026",
    "log_level": "info",
    "proxies": [...]
}
```

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| auth_token | string | "" | 认证 Token，空字符串表示不启用认证 |

---

## 5. 错误处理

### 5.1 问题分析

当前代码存在以下错误处理缺陷：

| 场景 | 当前行为 | 问题 |
|------|----------|------|
| 连接本地服务失败 | 发送 CLOSE | 正确，但缺少重试 |
| Protocol.decode 解析失败 | 返回 None，丢弃数据 | 应记录日志 |
| proxy_writer 写入失败 | 记录日志，关闭连接 | 正确，但可能残留状态 |
| register_proxies 部分失败 | 继续注册下一个 | 正确，但应汇总错误 |
| 控制连接读取超时 | 无超时 | 可能永久阻塞 |

### 5.2 设计方案

#### 5.2.1 统一异常分类

```python
# errors.py
class FRPError(Exception):
    """FRP 基础异常"""
    pass

class ConnectionError(FRPError):
    """连接相关异常"""
    pass

class AuthenticationError(FRPError):
    """认证异常"""
    pass

class ProtocolError(FRPError):
    """协议解析异常"""
    pass

class ProxyError(FRPError):
    """代理配置异常"""
    pass
```

#### 5.2.2 服务端错误处理增强

```python
# frps.py - handle_control_conn()
async def handle_control_conn(self, reader, writer):
    addr = writer.get_extra_info("peername")
    self.logger.info(f"New control connection from {addr}")

    buffer = b""
    client_id = None
    authenticated = not self.auth_token

    try:
        while True:
            # 新增：读取超时
            try:
                data = await asyncio.wait_for(reader.read(4096), timeout=self.read_timeout)
            except asyncio.TimeoutError:
                if not authenticated:
                    self.logger.warning(f"Auth timeout from {addr}")
                    break
                # 已认证的连接超时不关闭（可能在等数据）
                continue

            if not data:
                break

            buffer += data

            while True:
                try:
                    message, buffer = Protocol.decode(buffer)
                except Exception as e:
                    self.logger.error(f"Protocol decode error from {addr}: {e}")
                    buffer = b""  # 清空缓冲区，跳过损坏数据
                    break

                if not message:
                    break

                try:
                    # 认证检查
                    if not authenticated:
                        if message.type == MessageType.AUTH:
                            authenticated = await self.handle_auth(message, writer, addr)
                            if not authenticated:
                                return
                        else:
                            self.logger.warning(f"Unauthenticated message from {addr}")
                            return
                        continue

                    # 消息处理
                    await self.process_message(message, writer, addr)

                except Exception as e:
                    self.logger.error(f"Error processing message from {addr}: {e}")
                    # 发送 ERROR 消息给客户端
                    try:
                        error_msg = Message(MessageType.ERROR, message=str(e))
                        writer.write(Protocol.encode(error_msg))
                        await writer.drain()
                    except Exception:
                        pass

    except asyncio.CancelledError:
        pass
    except Exception as e:
        self.logger.error(f"Control connection error from {addr}: {e}", exc_info=True)
    finally:
        self.cleanup_client(client_id)
        try:
            writer.close()
            await writer.wait_closed()
        except Exception:
            pass
        self.logger.info(f"Control connection closed: {addr}")
```

#### 5.2.3 连接关闭增强

```python
# frps.py
def close_conn(self, conn_id):
    conn_info = self.state.conn_pool.pop(conn_id, None)
    if conn_info:
        try:
            writer = conn_info.get("proxy_writer")
            if writer:
                writer.close()
                asyncio.create_task(self._safe_wait_closed(writer))
        except Exception as e:
            self.logger.error(f"Error closing proxy_writer for {conn_id}: {e}")

        # 通知客户端关闭
        try:
            client_writer = conn_info.get("client_writer")
            if client_writer and not client_writer.is_closing():
                close_msg = Message(MessageType.CLOSE, conn_id=conn_id)
                client_writer.write(Protocol.encode(close_msg))
                asyncio.create_task(client_writer.drain())
        except Exception as e:
            self.logger.error(f"Error notifying client of close for {conn_id}: {e}")

async def _safe_wait_closed(self, writer):
    try:
        await writer.wait_closed()
    except Exception:
        pass
```

#### 5.2.4 客户端错误处理增强

```python
# frpc.py - handle_new_conn()
async def handle_new_conn(self, message):
    proxy_name = message.payload.get("proxy_name")
    conn_id = message.payload.get("conn_id")
    self.logger.info(f"Received NEW_CONN for proxy: {proxy_name}, conn_id: {conn_id}")

    proxy_info = self.state.proxies.get(proxy_name)
    if not proxy_info:
        self.logger.error(f"Unknown proxy: {proxy_name}")
        # 通知服务端关闭
        close_msg = Message(MessageType.CLOSE, conn_id=conn_id)
        self.control_writer.write(Protocol.encode(close_msg))
        await self.control_writer.drain()
        return

    local_ip = proxy_info.get("local_ip", "127.0.0.1")
    local_port = proxy_info.get("local_port")

    try:
        if proxy_info.get("type") == "tcp":
            local_reader, local_writer = await asyncio.wait_for(
                asyncio.open_connection(local_ip, local_port), timeout=5
            )

            self.state.conn_pool[conn_id] = {
                "proxy_name": proxy_name,
                "local_reader": local_reader,
                "local_writer": local_writer,
            }

            init_msg = Message(MessageType.INIT_CONN, conn_id=conn_id)
            self.control_writer.write(Protocol.encode(init_msg))
            await self.control_writer.drain()

            asyncio.create_task(self.forward_local_data(conn_id))
            self.logger.info(f"Connected to local service {local_ip}:{local_port}")

        elif proxy_info.get("type") == "udp":
            # ... UDP 处理 ...

    except asyncio.TimeoutError:
        self.logger.error(f"Timeout connecting to local service {local_ip}:{local_port}")
        close_msg = Message(MessageType.CLOSE, conn_id=conn_id)
        self.control_writer.write(Protocol.encode(close_msg))
        await self.control_writer.drain()

    except ConnectionRefusedError:
        self.logger.error(f"Local service refused connection at {local_ip}:{local_port}")
        close_msg = Message(MessageType.CLOSE, conn_id=conn_id)
        self.control_writer.write(Protocol.encode(close_msg))
        await self.control_writer.drain()

    except Exception as e:
        self.logger.error(f"Failed to connect to local service: {e}", exc_info=True)
        close_msg = Message(MessageType.CLOSE, conn_id=conn_id)
        try:
            self.control_writer.write(Protocol.encode(close_msg))
            await self.control_writer.drain()
        except Exception as send_err:
            self.logger.error(f"Failed to send CLOSE: {send_err}")
```

#### 5.2.5 协议解码增强

```python
# protocol.py
@staticmethod
def decode(data):
    if len(data) < Protocol.HEADER_SIZE:
        return None, data

    length = struct.unpack(">I", data[:Protocol.HEADER_SIZE])[0]

    # 防止异常大的 length 值
    if length > 10 * 1024 * 1024:  # 10MB 上限
        raise ValueError(f"Message too large: {length} bytes")

    if len(data) < Protocol.HEADER_SIZE + length:
        return None, data

    body = data[Protocol.HEADER_SIZE : Protocol.HEADER_SIZE + length]
    remaining = data[Protocol.HEADER_SIZE + length :]

    try:
        message = Message.from_dict(json.loads(body.decode("utf-8")))
        return message, remaining
    except (json.JSONDecodeError, ValueError, UnicodeDecodeError) as e:
        # 不再静默丢弃，抛出异常让调用方处理
        raise ValueError(f"Failed to decode message: {e}")
```

---

## 6. 配置汇总

### 6.1 服务端配置 (frps.json)

```json
{
    "bind_port": 7000,
    "bind_addr": "0.0.0.0",
    "auth_token": "my-secret-token-2026",
    "conn_timeout": 300,
    "heartbeat_timeout": 90,
    "cleanup_interval": 30,
    "read_timeout": 120,
    "log_level": "info"
}
```

### 6.2 客户端配置 (frpc.json)

```json
{
    "server_addr": "127.0.0.1",
    "server_port": 7000,
    "auth_token": "my-secret-token-2026",
    "reconnect_base_delay": 1.0,
    "reconnect_max_delay": 60.0,
    "reconnect_max_retries": -1,
    "log_level": "info",
    "proxies": [
        {
            "name": "web",
            "type": "tcp",
            "local_ip": "127.0.0.1",
            "local_port": 8000,
            "remote_port": 8080
        }
    ]
}
```

### 6.3 新增配置项汇总

| 参数 | 端 | 类型 | 默认值 | 说明 |
|------|-----|------|--------|------|
| auth_token | 服务端+客户端 | string | "" | 认证 Token |
| conn_timeout | 服务端 | int | 300 | 连接最大空闲时间（秒） |
| heartbeat_timeout | 服务端 | int | 90 | 心跳超时时间（秒） |
| cleanup_interval | 服务端 | int | 30 | 清理任务间隔（秒） |
| read_timeout | 服务端 | int | 120 | 读取超时（秒） |
| reconnect_base_delay | 客户端 | float | 1.0 | 初始重连延迟（秒） |
| reconnect_max_delay | 客户端 | float | 60.0 | 最大重连延迟（秒） |
| reconnect_max_retries | 客户端 | int | -1 | 最大重试次数 |

---

## 7. 测试验证

### 7.1 断线重连测试

```bash
# 1. 启动服务端
python frps.py -c frps.json

# 2. 启动客户端
python frpc.py -c frpc.json

# 3. 杀掉服务端，观察客户端重连日志
# 预期：客户端输出 "Reconnecting in 1.0s..."，指数退避

# 4. 重启服务端
# 预期：客户端自动重连成功，重新注册代理
```

### 7.2 超时清理测试

```bash
# 1. 启动服务端和客户端
# 2. 外部用户连接代理端口但不发送数据
nc 服务端IP 8080

# 3. 等待 conn_timeout 秒
# 预期：服务端日志输出 "Cleaning up timeout connection"
```

### 7.3 Token 认证测试

```bash
# 1. 服务端配置 auth_token
# 2. 客户端配置正确的 auth_token
# 预期：连接成功，日志输出 "Authentication successful"

# 3. 客户端配置错误的 auth_token
# 预期：连接被拒绝，日志输出 "Authentication failed"
```

### 7.4 错误处理测试

```bash
# 1. 客户端配置一个不存在的本地端口
# 2. 外部用户连接代理端口
# 预期：客户端日志输出错误，发送 CLOSE 消息，服务端清理连接
```

---

## 8. 实施计划

| 任务 | 依赖 | 说明 |
|------|------|------|
| 协议扩展（新增 AUTH 消息类型） | 无 | 修改 protocol.py |
| Token 认证实现 | 协议扩展 | 修改 frps.py, frpc.py |
| 超时清理实现 | 无 | 修改 frps.py |
| client_ready 超时 | 无 | 修改 frps.py forward_proxy_data |
| 断线重连实现 | 无 | 修改 frpc.py |
| 错误处理增强 | 无 | 修改所有文件 |
| 配置扩展 | 各模块 | 修改 config.py |
| 集成测试 | 所有任务 | 端到端验证 |
