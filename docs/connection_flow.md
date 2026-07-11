# 用户发起连接后的完整流程

## 1. 概述

本文档详细描述外部用户（客户端）发起连接后，MyFRP 系统的完整处理流程，包括连接建立、数据转发和连接关闭的全过程。

> 注：本文档反映 P0/P1 实现后的实际代码。控制通道可选 TLS 加密，建立后须先完成 LOGIN/LOGIN_RESP 认证才能注册代理。

## 2. 流程总览

### 2.1 整体流程图

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                          用户发起连接完整流程                                 │
└─────────────────────────────────────────────────────────────────────────────┘

外部客户端                              服务端                              内网客户端                              本地服务
    │                                    │                                    │                                    │
    │                                    │                                    │                                    │
    │────────────────────────────────────────────────────────────────────────│
    │                         阶段1: 控制通道建立                              │
    │────────────────────────────────────────────────────────────────────────│
    │                                    │                                    │                                    │
    │                                    │◄──────────── TCP连接 ───────────────│
    │                                    │         (7000端口，可选 TLS)          │                                    │
    │                                    │                                    │                                    │
    │                                    │◄────────── LOGIN(token) ───────────│
    │                                    │── LOGIN_RESP(ok, session_id, ───►│ (Token 认证)                       │
    │                                    │              data_port)             │                                    │
    │                                    │                                    │                                    │
    │                                    │◄────────── REGISTER ───────────────│
    │                                    │    (注册代理配置)                     │                                    │
    │                                    │                                    │                                    │
    │                                    │───── 启动代理监听 ─────────────────►│ (记录代理信息)                      │
    │                                    │   (remote_port)                     │                                    │
    │                                    │                                    │                                    │
    │────────────────────────────────────────────────────────────────────────│
    │                         阶段1.5: 数据通道建立（可选，优先）                │
    │────────────────────────────────────────────────────────────────────────│
    │                                    │                                    │                                    │
    │                                    │◄──────────── TCP连接 ───────────────│ (data_port，可选 TLS)
    │                                    │                                    │                                    │
    │                                    │◄──────── DATA_AUTH(session_id) ────│ (绑定会话)                          │
    │                                    │──────── DATA_AUTH_RESP(ok) ──────►│                                    │
    │                                    │                                    │                                    │
    │────────────────────────────────────────────────────────────────────────│
    │                         阶段2: 用户发起连接                               │
    │────────────────────────────────────────────────────────────────────────│
    │                                    │                                    │                                    │
    │─────── TCP连接 ──────────────────►│                                    │                                    │
    │      (remote_port=8080)           │                                    │                                    │
    │                                    │                                    │                                    │
    │────────────────────────────────────────────────────────────────────────│
    │                         阶段3: 通信链路建立                               │
    │────────────────────────────────────────────────────────────────────────│
    │                                    │                                    │                                    │
    │                                    │─────── NEW_CONN ──────────────────►│                                    │
    │                                    │  (proxy_name, conn_id)              │                                    │
    │                                    │                                    │                                    │
    │                                    │                                    │─────── TCP连接 ──────────────────►│
    │                                    │                                    │    (local_port=8000)               │
    │                                    │                                    │                                    │
    │                                    │◄──────── INIT_CONN ────────────────│ (优先绑定 data_writer)              │
    │                                    │                                    │                                    │
    │────────────────────────────────────────────────────────────────────────│
    │                         阶段4: 数据双向转发                               │
    │             （优先走数据通道，无数据通道时回退控制通道）                     │
    │────────────────────────────────────────────────────────────────────────│
    │                                    │                                    │                                    │
    │─────── 数据 ─────────────────────►│                                    │                                    │
    │                                    │─────── DATA ─────────────────────►│                                    │
    │                                    │  (conn_id, 原始 bytes)              │                                    │
    │                                    │                                    │─────── 数据 ─────────────────────►│
    │                                    │                                    │                                    │
    │◄────── 响应 ──────────────────────│◄─────── DATA ──────────────────────│◄─────── 响应 ──────────────────────│
    │                                    │  (conn_id, 原始 bytes)              │                                    │
    │                                    │                                    │                                    │
    │────────────────────────────────────────────────────────────────────────│
    │                         阶段5: 连接关闭                                  │
    │────────────────────────────────────────────────────────────────────────│
    │                                    │                                    │                                    │
    │─────── 关闭 ─────────────────────►│                                    │                                    │
    │                                    │─────── CLOSE ─────────────────────►│                                    │
    │                                    │  (conn_id)                          │                                    │
    │                                    │                                    │─────── 关闭 ─────────────────────►│
    │                                    │                                    │                                    │
    │                                    │◄─────── PING ──────────────────────│                                    │
    │                                    │─────── PONG ──────────────────────►│                                    │
    │                                    │                                    │                                    │
└─────────────────────────────────────────────────────────────────────────────┘
```

## 3. 详细流程说明

### 3.1 阶段1：控制通道建立

#### 3.1.1 客户端启动

```python
# frpc.py - main()
config = Config("client").load_from_file(args.config)
client = FRPClient(config)
asyncio.run(client.start())
```

#### 3.1.2 建立控制连接

```python
# frpc.py - FRPClient._connect_and_run()
server_addr = self.config.get("server_addr", "127.0.0.1")
server_port = self.config.get("server_port", 7000)

self.control_reader, self.control_writer = await asyncio.wait_for(
    asyncio.open_connection(
        server_addr, server_port, ssl=self._ssl_ctx
    ),
    timeout=10,
)
```

**关键要点**：
- 客户端主动发起连接（穿透NAT）
- 服务端监听 `bind_port`（默认7000）
- 若 `tls=true`，连接在 TLS 之上建立（`self._ssl_ctx` 由 `_init_ssl()` 创建）

#### 3.1.3 Token 认证

```python
# frpc.py - FRPClient._login()
login_msg = Message(MessageType.LOGIN, token=self.auth_token)
self.control_writer.write(Protocol.encode(login_msg))
await self.control_writer.drain()

# 等待 LOGIN_RESP（10 秒超时，循环读取直至拿到完整帧）
buffer = b""
while True:
    data = await asyncio.wait_for(
        self.control_reader.read(READ_BUF_SIZE), timeout=10
    )
    if not data:
        raise ConnectionError("Server closed connection during login")

    buffer += data
    message, buffer = Protocol.decode(buffer)
    if message and message.type == MessageType.LOGIN_RESP:
        if message.payload.get("status") == "ok":
            self.session_id = message.payload.get("session_id")
            server_data_port = message.payload.get("data_port")
            # 若客户端未显式配置 data_port，则采用服务端通告的端口
            if server_data_port and not self.data_port:
                self.data_port = server_data_port
            return  # 认证成功
        else:
            error_msg = message.payload.get("message", "Unknown error")
            raise PermissionError(f"Authentication failed: {error_msg}")
    elif message and message.type == MessageType.ERROR:
        raise PermissionError(
            f"Authentication error: {message.payload.get('message')}"
        )
```

```python
# frps.py - FRPServer.handle_login(self, message, writer, addr)
token = message.payload.get("token", "")
if token == self.auth_token:
    import uuid as _uuid
    session_id = str(_uuid.uuid4())
    session = {
        "id": session_id,
        "control_writer": writer,
        "data_writer": None,
        "addr": addr,
        "proxies": set(),
    }
    self.sessions[session_id] = session
    resp = Message(
        MessageType.LOGIN_RESP, status="ok", session_id=session_id,
        data_port=self.data_port,
    )
    writer.write(Protocol.encode(resp))
    await writer.drain()
    return True, session
else:
    resp = Message(
        MessageType.LOGIN_RESP, status="error", message="Invalid token"
    )
    writer.write(Protocol.encode(resp))
    await writer.drain()
    return False, None
```

**关键要点**：
- 未配置 `auth_token` 时服务端跳过认证
- 认证失败的服务端会关闭连接
- 认证成功后服务端生成 `session_id`（UUID）并创建会话存入 `self.sessions`，连同 `data_port` 一并通过 LOGIN_RESP 返回
- 客户端保存 `session_id` 并据此建立独立数据通道（见 3.1.6 节）

#### 3.1.4 注册代理

```python
# frpc.py - FRPClient.register_proxies()
for proxy in proxies:
    register_msg = Message(
        MessageType.REGISTER,
        proxy_name=proxy.get("name"),
        proxy_type=proxy.get("type", "tcp"),
        local_port=proxy.get("local_port"),
        remote_port=proxy.get("remote_port"),
        local_ip=proxy.get("local_ip", "127.0.0.1"),
    )
    self.control_writer.write(Protocol.encode(register_msg))
    await self.control_writer.drain()
```

#### 3.1.5 服务端处理注册

```python
# frps.py - FRPServer.handle_register()
proxy_name = message.payload.get("proxy_name")
proxy_type = message.payload.get("proxy_type", "tcp")
remote_port = message.payload.get("remote_port")

# 端口白名单校验
if not self.access.check_port(remote_port):
    # 返回 ERROR 消息
    ...

if proxy_type == "tcp":
    server_socket = await self.start_tcp_proxy(proxy_name, remote_port)
elif proxy_type == "udp":
    server_socket = await self.start_udp_proxy(proxy_name, remote_port)

self.proxies[proxy_name] = {
    "type": proxy_type,
    "remote_port": remote_port,
    "control_writer": writer,
    "server_socket": server_socket,
    "client_addr": addr,
    "created_at": time.time(),
    "last_activity": time.time(),
}
self.stats.on_proxy_register(proxy_name, proxy_type, remote_port)
```

**关键要点**：
- 服务端通过 `self.access.check_port()` 校验端口白名单
- 服务端为每个代理启动独立的监听服务
- 记录控制连接的 writer，用于后续通信
- 代理信息直接存于 `self.proxies`（无 `state` 中间层）

#### 3.1.6 建立数据通道（可选，优先）

注册代理后，若服务端通告了 `data_port` 且客户端已获得 `session_id`，则客户端额外建立一条独立数据连接，用于承载 DATA/CLOSE 消息，避免与控制消息竞争。

```python
# frpc.py - FRPClient._connect_and_run()
if self.data_port and self.session_id:
    try:
        await self._connect_data_channel(server_addr)
    except Exception as e:
        self.logger.warning(
            f"Data channel setup failed, using control channel: {e}"
        )
```

```python
# frpc.py - FRPClient._connect_data_channel()
self.data_reader, self.data_writer = await asyncio.wait_for(
    asyncio.open_connection(
        server_addr, self.data_port, ssl=self._ssl_ctx
    ),
    timeout=10,
)

# 发送 DATA_AUTH 绑定会话
auth_msg = Message(MessageType.DATA_AUTH, session_id=self.session_id)
self.data_writer.write(Protocol.encode(auth_msg))
await self.data_writer.drain()

# 等待 DATA_AUTH_RESP
buffer = b""
while True:
    data = await asyncio.wait_for(
        self.data_reader.read(READ_BUF_SIZE), timeout=10
    )
    if not data:
        raise ConnectionError("Data channel closed during auth")
    buffer += data
    message, buffer = Protocol.decode(buffer)
    if message and message.type == MessageType.DATA_AUTH_RESP:
        if message.payload.get("status") == "ok":
            break
        else:
            raise ConnectionError(
                f"Data auth failed: {message.payload.get('message')}"
            )

# 启动数据通道消息处理任务
self.data_task = asyncio.create_task(self.handle_data_messages())
```

**关键要点**：
- 数据通道与控制通道相互独立，DATA/CLOSE 优先走数据通道；数据通道不可用时回退到控制通道
- 服务端 `handle_data_conn` 收到 DATA_AUTH 后将 `session["data_writer"]` 指向该连接，后续 INIT_CONN 即优先使用此 writer
- 建立失败不影响控制通道正常工作，仅退化为控制通道复用模式

#### 3.1.7 启动心跳任务

```python
# frpc.py - FRPClient._connect_and_run()
self.heartbeat_task = asyncio.create_task(self.send_heartbeat())
self.reader_task = asyncio.create_task(self.handle_server_messages())
```

```python
# frpc.py - FRPClient.send_heartbeat()
while not self._stop_event.is_set():
    ping_msg = Message(MessageType.PING)
    self.control_writer.write(Protocol.encode(ping_msg))
    await self.control_writer.drain()
    await asyncio.sleep(30)
```

### 3.2 阶段2：用户发起连接

#### 3.2.1 外部客户端连接代理端口

```bash
# 外部用户访问
curl http://服务端IP:8080
```

#### 3.2.2 服务端代理服务接收连接

```python
# frps.py - FRPServer.start_tcp_proxy()
server = await asyncio.start_server(
    lambda r, w: self.handle_tcp_proxy_conn(r, w, proxy_name),
    "0.0.0.0",
    remote_port,
)
```

当外部客户端连接到 `remote_port` 时，`handle_tcp_proxy_conn` 被调用。

### 3.3 阶段3：通信链路建立

#### 3.3.1 服务端生成连接ID

```python
# frps.py - FRPServer.handle_tcp_proxy_conn()
proxy_info = self.proxies.get(proxy_name)
conn_id = Protocol.generate_conn_id()  # UUID
self.stats.on_connect(proxy_name, conn_id, proxy_info["type"], proxy_info["remote_port"])
```

#### 3.3.2 服务端发送 NEW_CONN 消息

```python
# frps.py - FRPServer.handle_tcp_proxy_conn()
new_conn_msg = Message(
    MessageType.NEW_CONN,
    proxy_name=proxy_name,
    conn_id=conn_id,
)

proxy_info["control_writer"].write(Protocol.encode(new_conn_msg))
await proxy_info["control_writer"].drain()
```

**关键要点**：
- 通过已有的控制连接发送消息（复用连接）
- 携带 `proxy_name` 和 `conn_id`

#### 3.3.3 服务端保存连接状态

```python
# frps.py - FRPServer.handle_tcp_proxy_conn()
self.conn_pool[conn_id] = {
    "proxy_name": proxy_name,
    "proxy_reader": reader,         # 外部客户端的 reader
    "proxy_writer": writer,         # 外部客户端的 writer
    "client_writer": None,          # 客户端的 writer（待填充）
    "client_ready": asyncio.Event(),# 等待客户端 INIT_CONN
    "created_at": time.time(),
    "last_activity": time.time(),
}
asyncio.create_task(self.forward_proxy_data(conn_id))
```

**关键要点**：
- `client_ready` 事件用于阻塞 `forward_proxy_data`，直到收到客户端的 INIT_CONN
- `forward_proxy_data` 中有 30 秒的 client_ready 超时，避免永久阻塞

#### 3.3.4 客户端接收 NEW_CONN 消息

```python
# frpc.py - FRPClient.handle_server_messages()
buffer = b""
try:
    while not self._stop_event.is_set():
        data = await self.control_reader.read(READ_BUF_SIZE)
        if not data:
            self.logger.info("Server connection closed")
            break

        buffer += data

        while True:
            message, buffer = Protocol.decode(buffer)
            if not message:
                break

            await self.process_message(message)
except asyncio.CancelledError:
    pass
except ConnectionResetError:
    self.logger.info("Connection reset by server")
except Exception as e:
    self.logger.error(f"Message handler error: {e}")
```

#### 3.3.5 客户端处理 NEW_CONN

```python
# frpc.py - FRPClient.process_message()
if message.type == MessageType.NEW_CONN:
    await self.handle_new_conn(message)
```

```python
# frpc.py - FRPClient.handle_new_conn()
proxy_name = message.payload.get("proxy_name")
conn_id = message.payload.get("conn_id")

proxy_info = self.state.proxies.get(proxy_name)
if not proxy_info:
    self.logger.error(f"Unknown proxy: {proxy_name}")
    return

local_ip = proxy_info.get("local_ip", "127.0.0.1")
local_port = proxy_info.get("local_port")

try:
    if proxy_info.get("type") == "tcp":
        # 建立到本地服务的连接（5 秒超时）
        local_reader, local_writer = await asyncio.wait_for(
            asyncio.open_connection(local_ip, local_port), timeout=5
        )
        _optimize_socket(local_writer)

        # 保存连接状态
        self.state.conn_pool[conn_id] = {
            "proxy_name": proxy_name,
            "local_reader": local_reader,
            "local_writer": local_writer,
            "created_at": time.time(),
            "last_activity": time.time(),
        }

        # 通知服务端本地已就绪
        init_msg = Message(MessageType.INIT_CONN, conn_id=conn_id)
        self.control_writer.write(Protocol.encode(init_msg))
        await self.control_writer.drain()

        # 启动数据转发任务
        asyncio.create_task(self.forward_local_data(conn_id))

    elif proxy_info.get("type") == "udp":
        loop = asyncio.get_event_loop()
        transport, protocol = await loop.create_datagram_endpoint(
            lambda: UDPLocalProtocol(self, conn_id),
            remote_addr=(local_ip, local_port),
        )

        self.state.conn_pool[conn_id] = {
            "proxy_name": proxy_name,
            "udp_transport": transport,
            "created_at": time.time(),
            "last_activity": time.time(),
        }

        init_msg = Message(MessageType.INIT_CONN, conn_id=conn_id)
        self.control_writer.write(Protocol.encode(init_msg))
        await self.control_writer.drain()

except Exception as e:
    self.logger.warning(
        f"Failed to connect to local service {local_ip}:{local_port}: {e}"
    )
    # 连接失败则通过数据通道（或回退控制通道）发送 CLOSE 通知服务端清理
    close_msg = Message(MessageType.CLOSE, conn_id=conn_id)
    try:
        writer = self._data_writer_for(conn_id)
        writer.write(Protocol.encode(close_msg))
        await writer.drain()
    except:
        pass
```

**关键要点**：
- 客户端主动连接本地服务（内网可访问），5 秒超时
- `proxy_info` 不存在时直接返回，避免处理未知代理
- 按 `type` 分支处理 TCP / UDP 两种本地服务，UDP 使用 `UDPLocalProtocol`
- TCP 本地连接建立后调用 `_optimize_socket` 设置 TCP_NODELAY 和大缓冲
- 连接成功后发送 INIT_CONN 通知服务端（触发 client_ready 事件）
- 连接失败则通过 `_data_writer_for`（优先数据通道，回退控制通道）发送 CLOSE 通知服务端清理
- 保存连接状态，建立 conn_id 到本地连接的映射

### 3.4 阶段4：数据双向转发

#### 3.4.1 方向1：外部客户端 → 本地服务

##### 步骤1：服务端接收外部数据

```python
# frps.py - FRPServer.forward_proxy_data()
async def forward_proxy_data(self, conn_id):
    conn_info = self.conn_pool.get(conn_id)
    if not conn_info:
        return

    # 等待客户端 INIT_CONN（30 秒超时）
    try:
        await asyncio.wait_for(
            conn_info["client_ready"].wait(), timeout=30
        )
    except asyncio.TimeoutError:
        self.logger.warning(f"Client ready timeout for conn_id: {conn_id}")
        self.close_conn(conn_id)
        return

    try:
        while True:
            # 从外部客户端读取（idle_timeout 超时则关闭）
            try:
                data = await asyncio.wait_for(
                    conn_info["proxy_reader"].read(READ_BUF_SIZE),
                    timeout=self.idle_timeout,
                )
            except asyncio.TimeoutError:
                break

            if not data:
                break

            conn_info["last_activity"] = time.time()
            self.stats.on_data_in(conn_id, len(data))

            # 发送到客户端（client_writer 可能是数据通道 writer 或控制 writer）
            data_msg = Message(
                MessageType.DATA,
                conn_id=conn_id,
                data=data,
            )
            conn_info["client_writer"].write(Protocol.encode(data_msg))
            await conn_info["client_writer"].drain()
    except ConnectionResetError:
        pass
    except Exception as e:
        self.logger.error(f"Error forwarding proxy data: {e}")
    finally:
        self.close_conn(conn_id)
```

##### 步骤2：服务端接收客户端的 INIT_CONN 并设置 client_writer

```python
# frps.py - FRPServer.handle_init_conn(self, message, writer, session=None)
async def handle_init_conn(self, message, writer, session=None):
    conn_id = message.payload.get("conn_id")
    conn_info = self.conn_pool.get(conn_id)
    if conn_info:
        # 优先使用数据通道 writer，回退到控制 writer
        if session and session.get("data_writer"):
            conn_info["client_writer"] = session["data_writer"]
        else:
            conn_info["client_writer"] = writer
        conn_info["client_ready"].set()  # 触发 forward_proxy_data 继续
        conn_info["last_activity"] = time.time()
    else:
        self.logger.warning(f"Connection not found for INIT_CONN: {conn_id}")
```

##### 步骤3：客户端接收数据并写入本地

```python
# frpc.py - FRPClient.process_message()
elif message.type == MessageType.DATA:
    # 数据通道活跃时 DATA 由数据通道处理，控制通道忽略
    if not (self.data_writer and not self.data_writer.is_closing()):
        await self.handle_data(message)
```

```python
# frpc.py - FRPClient.handle_data()
async def handle_data(self, message):
    conn_id = message.payload.get("conn_id")
    data = message.payload.get("data", b"")

    conn_info = self.state.conn_pool.get(conn_id)
    if not conn_info:
        return

    conn_info["last_activity"] = time.time()

    try:
        if conn_info.get("local_writer"):
            conn_info["local_writer"].write(data)  # 写入本地服务（原始 bytes）
            await conn_info["local_writer"].drain()
        elif conn_info.get("udp_transport"):
            conn_info["udp_transport"].sendto(data)
    except Exception as e:
        self.logger.debug(f"Error writing to local: {e}")
        self.close_conn(conn_id)
```

#### 3.4.2 方向2：本地服务 → 外部客户端

##### 步骤1：客户端读取本地数据

```python
# frpc.py - FRPClient.forward_local_data()
async def forward_local_data(self, conn_id):
    conn_info = self.state.conn_pool.get(conn_id)
    if not conn_info:
        return

    try:
        while True:
            data = await conn_info["local_reader"].read(READ_BUF_SIZE)  # 从本地服务读取
            if not data:
                break

            conn_info["last_activity"] = time.time()
            # 发送到服务端（优先数据通道，回退控制通道）
            data_msg = Message(
                MessageType.DATA,
                conn_id=conn_id,
                data=data,
            )
            writer = self._data_writer_for(conn_id)
            writer.write(Protocol.encode(data_msg))
            await writer.drain()
    except ConnectionResetError:
        self.logger.debug(f"Local connection reset: {conn_id}")
    except Exception as e:
        self.logger.debug(f"Error forwarding local data: {e}")
    finally:
        self.close_conn(conn_id)
```

##### 步骤2：服务端接收数据并写入外部客户端

```python
# frps.py - FRPServer.handle_data()
async def handle_data(self, message, writer):
    conn_id = message.payload.get("conn_id")
    data = message.payload.get("data", b"")

    conn_info = self.conn_pool.get(conn_id)
    if not conn_info:
        return

    if not conn_info["client_writer"]:
        conn_info["client_writer"] = writer

    conn_info["last_activity"] = time.time()

    proxy_info = self.proxies.get(conn_info["proxy_name"])
    if proxy_info:
        proxy_info["last_activity"] = time.time()

    self.stats.on_data_out(conn_id, len(data))

    try:
        conn_info["proxy_writer"].write(data)  # 写入外部客户端（原始 bytes）
        await conn_info["proxy_writer"].drain()
    except Exception as e:
        self.logger.debug(f"Error writing to proxy: {e}")
        self.close_conn(conn_id)
```

### 3.5 阶段5：连接关闭

#### 3.5.1 外部客户端断开连接

```python
# frps.py - FRPServer.forward_proxy_data()
while True:
    try:
        data = await asyncio.wait_for(
            conn_info["proxy_reader"].read(READ_BUF_SIZE),
            timeout=self.idle_timeout,
        )
    except asyncio.TimeoutError:
        break  # 空闲超时
    if not data:
        break  # 连接关闭
```

#### 3.5.2 服务端关闭连接

```python
# frps.py - FRPServer.close_conn()
def close_conn(self, conn_id):
    conn_info = self.conn_pool.pop(conn_id, None)
    if conn_info:
        self.stats.on_disconnect(conn_id)
        if conn_info.get("proxy_writer"):
            conn_info["proxy_writer"].close()
            asyncio.create_task(self._safe_wait_closed(conn_info["proxy_writer"]))
```

#### 3.5.3 客户端关闭连接

```python
# frpc.py - FRPClient.close_conn()
def close_conn(self, conn_id):
    conn_info = self.state.conn_pool.pop(conn_id, None)
    if conn_info:
        if conn_info.get("local_writer"):
            conn_info["local_writer"].close()
            asyncio.create_task(self._safe_wait_closed(conn_info["local_writer"]))
        elif conn_info.get("udp_transport"):
            conn_info["udp_transport"].close()
```

## 4. 消息流转表

### 4.1 消息类型汇总

| 消息类型 | 发送方 | 接收方 | 触发时机 | 作用 |
|----------|--------|--------|----------|------|
| LOGIN | 客户端 | 服务端 | 控制连接建立后 | 携带 token 进行认证 |
| LOGIN_RESP | 服务端 | 客户端 | 收到 LOGIN | 返回认证结果及 session_id、data_port |
| DATA_AUTH | 客户端 | 服务端 | 数据通道建立后 | 携带 session_id 绑定数据连接到会话 |
| DATA_AUTH_RESP | 服务端 | 客户端 | 收到 DATA_AUTH | 返回数据通道认证结果（ok/error） |
| REGISTER | 客户端 | 服务端 | 认证成功后 | 注册代理配置 |
| NEW_CONN | 服务端 | 客户端 | 外部用户连接 | 通知客户端建立本地连接 |
| INIT_CONN | 客户端 | 服务端 | 本地连接建立成功 | 通知服务端可开始转发 |
| DATA | 服务端 | 客户端 | 外部用户发送数据 | 转发数据到本地（二进制帧） |
| DATA | 客户端 | 服务端 | 本地服务响应 | 转发响应到外部（二进制帧） |
| CLOSE | 客户端/服务端 | 对端 | 连接失败/断开 | 通知关闭连接 |
| PING | 客户端 | 服务端 | 每30秒 | 心跳检测 |
| PONG | 服务端 | 客户端 | 收到 PING | 心跳响应 |
| ERROR | 服务端 | 客户端 | 注册失败/端口不允许等 | 错误通知 |

### 4.2 消息格式详解

#### LOGIN

```json
{
    "type": "login",
    "token": "my-secret-token-123"
}
```

#### LOGIN_RESP

成功时：

```json
{
    "type": "login_resp",
    "status": "ok",
    "session_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
    "data_port": 7001
}
```

失败时：

```json
{
    "type": "login_resp",
    "status": "error",
    "message": "Invalid token"
}
```

> 注：`session_id` 用于建立数据通道时的 DATA_AUTH 握手；`data_port` 为服务端通告的独立数据通道端口（未配置时缺省）。

#### INIT_CONN

```json
{
    "type": "init_conn",
    "conn_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
}
```

#### REGISTER

```json
{
    "type": "register",
    "proxy_name": "web",
    "proxy_type": "tcp",
    "local_port": 8000,
    "remote_port": 8080,
    "local_ip": "127.0.0.1"
}
```

#### NEW_CONN

```json
{
    "type": "new_conn",
    "proxy_name": "web",
    "conn_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
}
```

#### DATA_AUTH

客户端建立数据通道后发送，用于将这条数据连接绑定到已有会话。

```json
{
    "type": "data_auth",
    "session_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
}
```

#### DATA_AUTH_RESP

服务端校验 session_id 后返回，成功时将 `session["data_writer"]` 指向该数据连接。

成功时：

```json
{
    "type": "data_auth_resp",
    "status": "ok"
}
```

失败时：

```json
{
    "type": "data_auth_resp",
    "status": "error",
    "message": "Invalid session"
}
```

#### DATA

DATA 采用**二进制帧**编码（非 JSON、非 hex），以实现零拷贝传输。帧布局如下：

```
┌────────┬─────────┬──────┬───────┬──────────────┬──────────────────┬───────────────┐
│ Magic  │ Version │ Type │ Flags │ Payload Len  │ conn_id (UUID)   │ raw data      │
│ 1 byte │ 1 byte  │1 byte│1 byte │ 4 bytes (BE) │ 16 bytes         │ variable      │
└────────┴─────────┴──────┴───────┴──────────────┴──────────────────┴───────────────┘
```

- **8 字节头部**：`MAGIC(0xAA)` + `VERSION(0x01)` + `Type(0x0A)` + `Flags(0x00)` + `Payload Len`（大端 4 字节）
- **Payload**：前 16 字节为 `conn_id` 的二进制 UUID，其后紧跟原始数据 bytes（不经 hex 编码）
- 读取时 `Protocol.decode` 将 `conn_id` 还原为字符串、`data` 还原为 `bytes`

> 注：其他消息类型（LOGIN/REGISTER/NEW_CONN 等）仍使用 JSON UTF-8 作为 payload，仅 DATA 走二进制帧以减少编解码与内存开销。

#### CLOSE

```json
{
    "type": "close",
    "conn_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
}
```

## 5. 连接状态管理

### 5.1 服务端状态

服务端状态直接作为 `FRPServer` 实例属性，无独立状态类：

```python
class FRPServer:
    def __init__(self, config):
        self.proxies = {}          # {proxy_name: {type, remote_port, control_writer, server_socket, client_addr, session_id, ...}}
        self.conn_pool = {}        # {conn_id: {proxy_name, proxy_reader, proxy_writer, client_writer, client_ready, created_at, last_activity}}
        self.sessions = {}         # {session_id: {id, control_writer, data_writer, addr, proxies}}
        self.total_connections = 0
        self.stats = Stats()       # 流量统计
        self.access = AccessControl(self.logger)
        self.data_port = config.get("data_port")  # 独立数据通道端口（可选）
```

### 5.2 客户端状态

```python
class ClientState:
    def __init__(self):
        self.proxies = {}      # {proxy_name: {type, local_port, local_ip}}
        self.conn_pool = {}    # {conn_id: {proxy_name, local_reader, local_writer, created_at, last_activity}}
```

数据通道相关状态直接保存在 `FRPClient` 实例上：

```python
class FRPClient:
    def __init__(self, config):
        self.control_reader = None    # 控制通道 reader
        self.control_writer = None    # 控制通道 writer
        self.data_reader = None       # 数据通道 reader（可选）
        self.data_writer = None       # 数据通道 writer（可选）
        self.data_task = None         # 数据通道消息处理任务
        self.session_id = None        # LOGIN_RESP 返回的会话 ID，用于 DATA_AUTH
        self.data_port = config.get("data_port")  # 数据通道端口
```

### 5.3 状态流转图

```
服务端:
  ┌─────────────────────────────────────────────────────────────────┐
  │ 控制连接建立                                                    │
  │     │                                                          │
  │     ▼                                                          │
  │  REGISTER ─────► proxies[proxy_name] = {...}                   │
  │                     │                                          │
  │                     ▼                                          │
  │  外部用户连接 ──► conn_pool[conn_id] = {...}                   │
  │                     │                                          │
  │                     ▼                                          │
  │  NEW_CONN ─────► 等待客户端响应                                 │
  │                     │                                          │
  │                     ▼                                          │
  │  DATA ────────► 数据转发中                                      │
  │                     │                                          │
  │                     ▼                                          │
  │  CLOSE ───────► conn_pool.pop(conn_id)                        │
  └─────────────────────────────────────────────────────────────────┘

客户端:
  ┌─────────────────────────────────────────────────────────────────┐
  │ 控制连接建立                                                    │
  │     │                                                          │
  │     ▼                                                          │
  │  REGISTER ─────► proxies[proxy_name] = {...}                   │
  │                     │                                          │
  │                     ▼                                          │
  │  NEW_CONN ─────► conn_pool[conn_id] = {...}                   │
  │                     │                                          │
  │                     ▼                                          │
  │  本地连接建立 ──► 数据转发中                                    │
  │                     │                                          │
  │                     ▼                                          │
  │  CLOSE ───────► conn_pool.pop(conn_id)                        │
  └─────────────────────────────────────────────────────────────────┘
```

## 6. 关键设计要点

### 6.1 连接ID的作用

- **唯一标识**：每个通信链路有唯一的 conn_id
- **映射关系**：服务端和客户端都用 conn_id 建立连接映射
- **数据路由**：通过 conn_id 将数据路由到正确的连接

### 6.2 控制连接复用

- **减少连接开销**：控制消息（LOGIN/REGISTER/NEW_CONN/INIT_CONN/PING 等）统一走控制通道；当服务端配置了 `data_port` 时，DATA/CLOSE 优先走独立数据通道，无数据通道时回退到控制通道
- **数据通道分离**：独立数据通道避免大数据帧与控制消息竞争，降低控制消息延迟
- **穿透NAT**：客户端主动建立连接，服务端无需主动连接客户端
- **简化部署**：只需开放控制端口和代理端口；启用数据通道时额外开放 data_port

### 6.3 异步处理

- **高并发**：基于 asyncio，单线程处理大量并发连接
- **非阻塞**：数据转发不阻塞其他连接
- **独立任务**：每个连接有独立的转发任务

### 6.4 心跳机制

- **连接保活**：每30秒发送一次心跳
- **故障检测**：心跳失败时断开连接
- **资源释放**：断开连接时清理相关资源

## 7. 异常处理

### 7.1 服务端异常

| 异常场景 | 处理方式 |
|----------|----------|
| 控制连接断开 | 清理该客户端的所有代理，关闭代理监听 |
| 外部连接断开 | 发送 CLOSE 消息给客户端，清理连接状态 |
| 数据转发失败 | 关闭连接，记录错误日志 |
| 端口占用 | 返回 ERROR 消息，跳过该代理 |

### 7.2 客户端异常

| 异常场景 | 处理方式 |
|----------|----------|
| 控制连接断开 | 关闭所有本地连接，按指数退避自动重连 |
| 认证失败 | 抛出 PermissionError，触发重连（达到最大次数则退出） |
| 本地连接失败 | 发送 CLOSE 消息给服务端，记录错误 |
| 数据转发失败 | 关闭连接，记录错误日志 |
| 心跳失败 | 断开连接，触发重连 |
| 重连达到最大次数 | 退出程序 |

## 8. 性能特性

### 8.1 并发模型

- **异步IO**：使用 asyncio 实现非阻塞IO
- **单线程**：单线程处理所有连接，避免线程切换开销
- **事件驱动**：基于事件循环，高效处理大量并发连接

### 8.2 数据传输

- **流式传输**：数据以 `READ_BUF_SIZE`（65536 字节，64KB）为单位分块读取，相比 4KB 显著减少系统调用次数
- **原始二进制 bytes**：DATA 消息使用二进制帧（8 字节头 + 16 字节 UUID + 原始 bytes），直接传输原始 bytes，不经过 hex 编码，避免双倍内存与编解码开销
- **零拷贝优化**：`Protocol.encode` 对 DATA 帧预分配单个 `bytearray` 一次性写入头部、UUID 与数据，避免中间字节串拼接；解码时直接切片返回 `bytes` 视图
- **TCP 调优**：`_optimize_socket` 对每条 TCP 连接设置 `TCP_NODELAY` 禁用 Nagle，并将 `SO_RCVBUF`/`SO_SNDBUF` 提升至 256KB，降低小包延迟并提升吞吐
- **缓冲区管理**：使用缓冲区处理粘包和拆包问题，基于帧头长度字段精确切分

### 8.3 资源占用

- **内存**：仅维护必要的连接状态，内存占用低
- **CPU**：异步处理，CPU 利用率高
- **网络**：复用控制连接，减少网络开销

## 9. 总结

### 9.1 流程核心

1. **客户端主动连接**：穿透NAT，建立控制通道（可选 TLS）
2. **Token 认证**：客户端发送 LOGIN，服务端校验后返回 LOGIN_RESP（含 session_id、data_port）
3. **代理注册**：客户端告知服务端代理配置（经端口白名单校验）
4. **服务端监听**：为每个代理启动独立的监听服务
5. **数据通道建立**：若服务端通告 data_port，客户端额外建立数据连接并完成 DATA_AUTH 握手；失败则回退控制通道复用
6. **外部用户连接**：触发通信链路建立
7. **NEW_CONN 通知**：服务端通过控制通道通知客户端
8. **本地连接建立**：客户端主动连接本地服务，成功后发送 INIT_CONN（服务端优先绑定数据通道 writer）
9. **数据转发**：双向数据优先走独立数据通道，无数据通道时回退控制通道；DATA 使用二进制帧零拷贝传输
10. **连接关闭**：清理相关资源，客户端断线后自动重连

### 9.2 设计优势

- **简单高效**：架构简单，易于理解和维护
- **NAT穿透**：天然支持内网穿透
- **高并发**：基于 asyncio，支持大量并发连接
- **可靠稳定**：心跳机制保证连接稳定性
- **易于扩展**：模块化设计，便于功能扩展