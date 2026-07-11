# NEW_CONN 阶段详细说明

## 1. 概述

NEW_CONN 阶段是 MyFRP 建立通信链路的**核心环节**。当外部用户连接到服务端的代理端口时，服务端通过控制通道向内网客户端发送 NEW_CONN 消息，触发客户端建立到本地服务的连接，从而打通"外部用户 → 服务端 → 客户端 → 本地服务"的完整数据通路。

### 1.1 为什么这个阶段最关键？

- **NAT 穿透的落地点**：服务端无法主动连接内网客户端，但可以通过已有的控制连接发送 NEW_CONN 消息
- **连接映射的建立点**：通过 `conn_id` 建立外部连接与本地连接的映射关系
- **数据通道的启动点**：双方各自启动数据转发任务，开始双向数据传输

### 1.2 涉及的核心问题

| 问题 | 解决方案 |
|------|----------|
| 服务端无法主动连接客户端 | 复用客户端主动建立的控制连接 |
| 多个并发连接如何区分 | 使用 UUID 生成唯一的 `conn_id` |
| 客户端本地连接未就绪时数据丢失 | 使用 `asyncio.Event()` 同步等待 |
| 客户端连接本地服务失败 | 发送 CLOSE 消息通知服务端清理 |

### 1.3 前置条件：LOGIN 与 session 机制

NEW_CONN 阶段并非凭空触发，它建立在 LOGIN 认证与 session 机制之上：

1. **控制连接建立后必须先 LOGIN**：客户端连上服务端 `bind_port`（默认 7000）后，若服务端开启 `auth_token`，必须先发送 `LOGIN(token=...)` 消息；否则服务端拒绝处理任何后续消息。
2. **服务端为通过认证的连接创建 session**：`handle_login()` 生成 `session_id`（UUID），并保存映射 `self.sessions[session_id] = {"id", "control_writer", "data_writer": None, "addr", "proxies": set()}`，随后通过 `LOGIN_RESP` 把 `session_id` 和 `data_port` 返回给客户端。
3. **session_id 是数据通道认证的基础**：客户端拿到 `session_id` 后，若服务端开放 `data_port`，会再建立一条独立的数据连接，并发送 `DATA_AUTH(session_id=...)` 握手；服务端校验 session 存在后，将该数据连接的 writer 绑定到 `session["data_writer"]`，并回复 `DATA_AUTH_RESP(status="ok")`。
4. **session_id 是 `handle_init_conn` 选择 writer 的依据**：服务端处理 INIT_CONN 时，会通过 session 查找数据通道 writer（见 3.11 节），优先把 DATA 流量引到独立的数据通道，控制通道只承载小流量控制消息。

> 因此 NEW_CONN 阶段的所有交互都隐含"已登录 + 已有 session"这一前提，`session_id` 同时是认证凭据和 writer 选择的索引键。

---

## 2. 完整时序图

```
外部客户端              服务端                          内网客户端                        本地服务
    │                    │                                │                              │
    │── TCP连接 ────────►│                                │                              │
    │   (remote_port)    │                                │                              │
    │                    │                                │                              │
    │              ┌─────┴──────┐                         │                              │
    │              │ 0.IP检查    │                         │                              │
    │              │ _optimize_ │                         │                              │
    │              │ socket     │                         │                              │
    │              │ stats.on_  │                         │                              │
    │              │ connect    │                         │                              │
    │              │ 1.生成conn_id│                         │                              │
    │              │ (UUID)      │                         │                              │
    │              └─────┬──────┘                         │                              │
    │                    │                                │                              │
    │                    │── 2.NEW_CONN ─────────────────►│                              │
    │                    │   (proxy_name, conn_id)         │  （先发送，再保存状态）       │
    │                    │   通过控制通道发送               │                              │
    │                    │                                │                              │
    │              ┌─────┴──────┐                  ┌──────┴───────┐                      │
    │              │ 3.保存连接  │                  │ 4.解析消息    │                      │
    │              │ 状态到池中  │                  │ 提取proxy_name│                      │
    │              │ (client_writer=None)           │ 和conn_id    │                      │
    │              │ client_ready=Event()           └──────┬───────┘                      │
    │              │ 4.启动转发  │                         │                              │
    │              │ 任务        │                  ┌──────┴───────┐                      │
    │              │ forward_    │                  │ 5.查找代理配置│                      │
    │              │ proxy_data  │                  │ 获取local_ip │                      │
    │              │             │                  │ 和local_port │                      │
    │              │ ⏳ 等待     │                  └──────┬───────┘                      │
    │              │ client_ready│                  ┌──────┴───────┐                      │
    │              │ Event      │                  │ 6.连接本地服务│                      │
    │              │            │                  │ TCP连接       │── TCP连接 ──────────►│
    │              │            │                  │ _optimize_   │                      │
    │              │            │                  │ socket       │                      │
    │              │            │                  │ (超时5秒)     │                      │
    │              │            │                  └──────┬───────┘◄── 连接成功 ──────────│
    │              │            │                         │                              │
    │              │            │                  ┌──────┴───────┐                      │
    │              │            │                  │ 7.保存连接状态│                      │
    │              │            │                  │ 到conn_pool  │                      │
    │              │            │                  │ (含created_at/│                      │
    │              │            │                  │  last_activity)│                     │
    │              │            │                  └──────┬───────┘                      │
    │              │            │                         │                              │
    │              │            │                  ┌──────┴───────┐                      │
    │              │            │                  │ 8.发送        │                      │
    │              │◄── INIT_CONN ─────────────────│ INIT_CONN    │                      │
    │              │   (conn_id) │                  │ 通知服务端    │                      │
    │              │   通过控制通道 │                │ 本地已就绪    │                      │
    │              │            │                  └──────┬───────┘                      │
    │              │            │                         │                              │
    │              │            │                  ┌──────┴───────┐                      │
    │              │            │                  │ 9.启动转发    │                      │
    │              │            │                  │ forward_     │                      │
    │              │            │                  │ local_data   │                      │
    │              │            │                  │ 任务          │                      │
    │              │            │                  └──────┬───────┘                      │
    │              │ ┌──────────┐                         │                              │
    │              │ │10.收到   │                         │                              │
    │              │ │INIT_CONN │                         │                              │
    │              │ │按session │                         │                              │
    │              │ │选 data_  │                         │                              │
    │              │ │writer 或 │                         │                              │
    │              │ │control   │                         │                              │
    │              │ │writer    │                         │                              │
    │              │ │设置      │                         │                              │
    │              │ │client_   │                         │                              │
    │              │ │ready     │                         │                              │
    │              │ │Event     │                         │                              │
    │              │ └────┬─────┘                         │                              │
    │              │      │                               │                              │
    │              │      ▼                               │                              │
    │              │ client_ready                         │                              │
    │              │ Event 触发                           │                              │
    │              │      │                               │                              │
    │              │      ▼                               │                              │
    │              │ forward_proxy_data                  │                              │
    │              │ 不再阻塞，开始                       │                              │
    │              │ 转发数据                            │                              │
    │              │      │                               │                              │
    │◄─── 数据 ────│◄── DATA（数据通道 data_port）─────────│◄──── 数据 ────────────────────│
    │              │   ↑ 若数据通道未激活则走控制通道       │  forward_local_data          │
    │── 数据 ─────►│── DATA（数据通道 data_port）─────────►│── 数据 ──────────────────────►│
    │              │   ↑ 若数据通道未激活则走控制通道       │  handle_data                 │
    │              │      │                               │                              │
```

> **数据通道说明**：若服务端开放 `data_port` 且客户端已完成 `DATA_AUTH` 握手，DATA 帧通过独立的数据通道（`data_port`）传输；否则 DATA 帧回退到控制通道传输。控制消息（NEW_CONN / INIT_CONN / CLOSE / PING / PONG 等）始终走控制通道。

---

## 3. 阶段分解

### 3.1 阶段1：外部用户连接触发

#### 触发条件

外部用户（如浏览器、curl）连接到服务端的代理端口 `remote_port`。

```bash
# 外部用户访问
curl http://服务端IP:8080
```

#### 服务端接收连接

服务端在代理注册时已经通过 `start_tcp_proxy` 启动了端口监听：

```python
# frps.py - start_tcp_proxy()
server = await asyncio.start_server(
    lambda r, w: self.handle_tcp_proxy_conn(r, w, proxy_name),
    "0.0.0.0",
    remote_port,  # 如 8080
)
```

当外部用户连接到 `remote_port` 时，asyncio 自动调用 `handle_tcp_proxy_conn`，传入 `reader`（外部连接的读取端）和 `writer`（外部连接的写入端）。

`handle_tcp_proxy_conn` 在进入业务逻辑前会先做三件准备工作：

```python
# frps.py - handle_tcp_proxy_conn()
addr = writer.get_extra_info("peername")
ip = addr[0] if addr else "unknown"
_optimize_socket(writer)                       # TCP_NODELAY + 256KB 收发缓冲

if not self.access.check_ip(ip):               # IP 白/黑名单检查
    self.logger.warning(f"Proxy connection from {ip} blocked")
    writer.close()
    await writer.wait_closed()
    return

proxy_info = self.proxies.get(proxy_name)      # 校验代理是否仍存在
if not proxy_info:
    writer.close()
    await writer.wait_closed()
    return
```

随后调用 `self.stats.on_connect(proxy_name, conn_id, proxy_info["type"], proxy_info["remote_port"])` 记录统计指标，再生成 `conn_id` 并进入 NEW_CONN 流程。

**此时状态**：
- `reader`：可用于读取外部用户发来的数据
- `writer`：可用于向外部用户发送数据（已开启 `TCP_NODELAY` 并调大缓冲区）
- `proxy_name`：通过闭包捕获，知道是哪个代理收到连接
- `proxy_info`：从 `self.proxies` 查到的代理元数据（含 `control_writer`、`session_id` 等）

---

### 3.2 阶段2：生成 conn_id

#### 为什么需要 conn_id？

```
场景：多个外部用户同时连接同一个代理

外部用户A ──► 服务端:8080 ──► 客户端 ──► 本地服务:8000
外部用户B ──► 服务端:8080 ──► 客户端 ──► 本地服务:8000
外部用户C ──► 服务端:8080 ──► 客户端 ──► 本地服务:8000

问题：所有数据都通过同一个控制连接传输，如何区分哪个数据属于哪个用户？

解决：为每个连接分配唯一的 conn_id
```

#### 生成方式

```python
# protocol.py
@staticmethod
def generate_conn_id():
    return str(uuid.uuid4())
```

使用 Python 的 `uuid.uuid4()` 生成唯一标识符，格式如：
```
a1b2c3d4-e5f6-7890-abcd-ef1234567890
```

**特点**：
- UUID v4 基于随机数，碰撞概率极低
- 字符串格式，便于在 JSON 消息中传输
- 全局唯一，无需中心化分配

---

### 3.3 阶段3：发送 NEW_CONN 并保存连接状态

> **执行顺序说明**：在 `handle_tcp_proxy_conn` 的实际代码中，服务端**先发送 NEW_CONN，再保存连接状态到 `conn_pool`，最后启动转发任务**，三者位于同一个 `try` 块内。下文为叙述清晰，先说明发送 NEW_CONN（阶段 3.4），再说明保存状态，但实际执行顺序与此处小节顺序一致——发送在前，保存在后。

#### 实际代码顺序

```python
# frps.py - handle_tcp_proxy_conn()（核心片段）
try:
    # 1. 先通过控制通道发送 NEW_CONN
    proxy_info["control_writer"].write(Protocol.encode(new_conn_msg))
    await proxy_info["control_writer"].drain()
    proxy_info["last_activity"] = time.time()

    # 2. 再保存连接状态到 conn_pool
    self.conn_pool[conn_id] = {
        "proxy_name": proxy_name,
        "proxy_reader": reader,
        "proxy_writer": writer,
        "client_writer": None,
        "client_ready": asyncio.Event(),
        "created_at": time.time(),
        "last_activity": time.time(),
    }

    # 3. 最后启动转发任务
    asyncio.create_task(self.forward_proxy_data(conn_id))
except Exception as e:
    self.logger.error(f"Failed to handle proxy conn: {e}")
    writer.close()
    await writer.wait_closed()
```

#### 保存内容

```python
# frps.py - handle_tcp_proxy_conn()
self.conn_pool[conn_id] = {
    "proxy_name": proxy_name,       # 代理名称
    "proxy_reader": reader,         # 外部连接的 reader（读取外部数据）
    "proxy_writer": writer,         # 外部连接的 writer（向外部发送数据）
    "client_writer": None,          # 客户端的 writer（待填充）
    "client_ready": asyncio.Event(),# 同步事件（等待客户端就绪）
    "created_at": time.time(),      # 创建时间（用于空闲超时清理）
    "last_activity": time.time(),   # 最后活跃时间（用于空闲超时清理）
}
```

#### 字段说明

| 字段 | 类型 | 初始值 | 用途 |
|------|------|--------|------|
| proxy_name | str | 代理名称 | 标识属于哪个代理 |
| proxy_reader | StreamReader | 外部连接的读取端 | 读取外部用户数据 |
| proxy_writer | StreamWriter | 外部连接的写入端 | 向外部用户发送数据 |
| client_writer | StreamWriter | None | 客户端的 writer，后续由 `handle_init_conn` 按 session 选择数据通道 writer 或控制通道 writer 填充 |
| client_ready | asyncio.Event | 未触发 | 等待客户端本地连接建立完成 |
| created_at | float | time.time() | 连接创建时间，用于空闲清理 |
| last_activity | float | time.time() | 最后活跃时间，每次读写时更新 |

> 注：服务端代理与连接池直接作为 `FRPServer` 实例属性 `self.proxies` / `self.conn_pool` 管理，无 `state` 中间层。

#### 为什么需要 client_ready？

```
时序问题：

服务端发送 NEW_CONN ──► 客户端
                         │
                         │ 建立本地连接（耗时）
                         │
外部用户数据到达 ──► 服务端尝试转发
                    │
                    ▼
              client_writer = None
              无法转发！数据丢失！
```

`client_ready` 是一个 `asyncio.Event()`，用于确保在客户端本地连接就绪之前，服务端不会尝试转发数据。

---

### 3.4 阶段4：发送 NEW_CONN 消息

#### 消息构造

```python
# frps.py - handle_tcp_proxy_conn()
new_conn_msg = Message(
    MessageType.NEW_CONN,
    proxy_name=proxy_name,   # 代理名称，如 "web"
    conn_id=conn_id,         # 连接ID，如 "a1b2c3d4-..."
)
```

#### 消息编码（8 字节二进制帧头）

`Protocol` 使用统一的 8 字节二进制帧头，按消息类型选择 payload 编码方式：控制消息走 JSON，DATA 消息走二进制（避免 hex 编码开销，零拷贝）。

```python
# protocol.py - Protocol.encode()
MAGIC = 0xAA
VERSION = 0x01
HEADER_SIZE = 8
UUID_SIZE = 16
_HEADER_FMT = ">BBBBI"  # magic, version, type, flags, length

@staticmethod
def encode(message):
    msg_code = _TYPE_TO_CODE.get(message.type)   # 字符串类型 → 数字码
    if message.type == MessageType.DATA:
        # 二进制 payload：conn_id 转 16 字节 UUID + 原始 data bytes
        uuid_bytes = uuid.UUID(message.payload.get("conn_id", "")).bytes
        data_bytes = message.payload.get("data", b"")
        payload_len = UUID_SIZE + len(data_bytes)
        buf = bytearray(HEADER_SIZE + payload_len)
        struct.pack_into(_HEADER_FMT, buf, 0,
                         MAGIC, VERSION, msg_code, 0, payload_len)
        buf[HEADER_SIZE:HEADER_SIZE + UUID_SIZE] = uuid_bytes
        buf[HEADER_SIZE + UUID_SIZE:] = data_bytes
        return buf
    else:
        # 控制消息走 JSON UTF-8
        payload = json.dumps(message.to_dict()).encode("utf-8")
        header = struct.pack(_HEADER_FMT, MAGIC, VERSION, msg_code, 0, len(payload))
        return header + payload
```

帧布局：

```
┌────────┬─────────┬──────┬───────┬──────────────┬──────────────────────────┐
│ Magic  │ Version │ Type │ Flags │ Payload Len  │ Payload                  │
│ 1 byte │ 1 byte  │1 byte│1 byte │ 4 bytes (BE) │ variable                 │
│ 0xAA   │ 0x01    │ code │ 0x00  │              │ JSON 或 UUID+raw bytes   │
└────────┴─────────┴──────┴───────┴──────────────┴──────────────────────────┘
```

NEW_CONN（控制消息）编码后的二进制数据：

```
┌──────────────────────────────────────────────────────────────────────────┐
│ Header (8 bytes)                 │  Body (JSON UTF-8)                   │
│ AA 01 04 00 0000004E (78)        │  {"type":"new_conn","proxy_name":"web",│
│                                  │   "conn_id":"a1b2c3d4-e5f6-7890-abcd-  │
│                                  │    ef1234567890"}                     │
└──────────────────────────────────────────────────────────────────────────┘
```

DATA（二进制消息）编码后的二进制数据：

```
┌──────────────────────────────────────────────────────────────────────────┐
│ Header (8 bytes)                 │ UUID (16 bytes)  │ Raw Data bytes    │
│ AA 01 0A 00 00000020 (32)        │ a1b2c3d4...      │ <二进制 payload>  │
└──────────────────────────────────────────────────────────────────────────┘
```

#### 通过控制通道发送

```python
# frps.py - handle_tcp_proxy_conn()
proxy_info["control_writer"].write(Protocol.encode(new_conn_msg))
await proxy_info["control_writer"].drain()
```

**关键点**：
- `proxy_info["control_writer"]` 是客户端建立控制连接时保存的 writer
- 控制消息（NEW_CONN/INIT_CONN/CLOSE/PING 等）始终走控制通道
- DATA 消息的 writer 由 `handle_init_conn` 按 session 选择（见 3.11 节），优先走数据通道
- 这就是服务端"无法主动连接客户端"的解决方案

---

### 3.5 阶段5：服务端启动转发任务并等待

#### 启动转发任务

```python
# frps.py - handle_tcp_proxy_conn()
asyncio.create_task(self.forward_proxy_data(conn_id))
```

#### 转发任务等待客户端就绪

```python
# frps.py - forward_proxy_data()
async def forward_proxy_data(self, conn_id):
    conn_info = self.conn_pool.get(conn_id)
    if not conn_info:
        return

    # 等待客户端本地连接建立完成（30 秒超时）
    try:
        await asyncio.wait_for(
            conn_info["client_ready"].wait(), timeout=30
        )
    except asyncio.TimeoutError:
        self.logger.warning(f"Client ready timeout for conn_id: {conn_id}")
        self.close_conn(conn_id)
        return

    # 客户端就绪后，开始转发数据
    try:
        while True:
            # 读取外部数据（idle_timeout 超时则关闭）
            try:
                data = await asyncio.wait_for(
                    conn_info["proxy_reader"].read(READ_BUF_SIZE),  # 64KB
                    timeout=self.idle_timeout,
                )
            except asyncio.TimeoutError:
                break

            if not data:
                break
            conn_info["last_activity"] = time.time()
            self.stats.on_data_in(conn_id, len(data))
            # client_writer 由 handle_init_conn 按 session 选择：
            # 数据通道激活时为 data_writer，否则为控制通道 writer
            data_msg = Message(MessageType.DATA, conn_id=conn_id, data=data)  # 原始二进制
            conn_info["client_writer"].write(Protocol.encode(data_msg))
            await conn_info["client_writer"].drain()
    except ConnectionResetError:
        pass
    except Exception as e:
        self.logger.error(f"Error forwarding proxy data: {e}")
    finally:
        self.close_conn(conn_id)
```

**等待机制详解**：

```
时间线:
t0: 服务端发送 NEW_CONN
t1: 服务端创建 forward_proxy_data 任务
t2: forward_proxy_data 执行到 client_ready.wait() → 挂起等待
    │
    │  ... 等待中 ...
    │
t3: 客户端收到 NEW_CONN
t4: 客户端建立本地连接
t5: 客户端发送 INIT_CONN
t6: 服务端收到 INIT_CONN，设置 client_ready.set()
    │
t7: forward_proxy_data 从 wait() 恢复执行
t8: 开始转发数据
```

#### 服务端 handle_data（接收客户端 DATA 并写回外部用户）

反向数据流（客户端 → 服务端 → 外部用户）由 `handle_data` 处理，它同样直接使用二进制 `data`，不进行 hex 解码：

```python
# frps.py - handle_data()
async def handle_data(self, message, writer):
    conn_id = message.payload.get("conn_id")
    data = message.payload.get("data", b"")          # 已是 bytes，无需 fromhex

    conn_info = self.conn_pool.get(conn_id)
    if not conn_info:
        return

    if not conn_info["client_writer"]:
        conn_info["client_writer"] = writer           # 兜底：首次 DATA 到达时补全 writer

    conn_info["last_activity"] = time.time()

    proxy_info = self.proxies.get(conn_info["proxy_name"])
    if proxy_info:
        proxy_info["last_activity"] = time.time()

    self.stats.on_data_out(conn_id, len(data))

    try:
        conn_info["proxy_writer"].write(data)         # 写给外部用户
        await conn_info["proxy_writer"].drain()
    except Exception as e:
        self.logger.debug(f"Error writing to proxy: {e}")
        self.close_conn(conn_id)
```

---

### 3.6 阶段6：客户端接收并解析 NEW_CONN

#### 接收数据

```python
# frpc.py - handle_server_messages()
buffer = b""
try:
    while not self._stop_event.is_set():
        data = await self.control_reader.read(READ_BUF_SIZE)  # 64KB，从控制连接读取
        if not data:
            self.logger.info("Server connection closed")
            break

        buffer += data

        while True:
            message, buffer = Protocol.decode(buffer)  # 解码消息
            if not message:
                break
            await self.process_message(message)  # 处理消息
except asyncio.CancelledError:
    pass
except ConnectionResetError:
    self.logger.info("Connection reset by server")
except Exception as e:
    self.logger.error(f"Message handler error: {e}")
```

#### 粘包处理

由于 TCP 是流式协议，可能发生粘包（多条消息合并）或拆包（一条消息拆分）。`Protocol.decode` 通过 8 字节二进制帧头中的长度前缀解决，并按消息类型分别解析 JSON 或二进制 payload：

```python
# protocol.py - Protocol.decode()
@staticmethod
def decode(data):
    if len(data) < Protocol.HEADER_SIZE:       # 数据不足 8 字节，等待更多数据
        return None, data

    magic, version, msg_code, flags, length = struct.unpack(
        Protocol._HEADER_FMT, data[:Protocol.HEADER_SIZE]   # >BBBBI
    )

    if magic != Protocol.MAGIC:                # magic 不匹配，丢一字节重新同步
        return None, data[1:]

    if len(data) < Protocol.HEADER_SIZE + length:  # 数据不完整，等待更多
        return None, data

    payload = data[Protocol.HEADER_SIZE : Protocol.HEADER_SIZE + length]
    remaining = data[Protocol.HEADER_SIZE + length :]

    msg_type = _CODE_TO_TYPE.get(msg_code)     # 数字码 → 字符串类型
    if msg_type is None:
        return None, remaining

    if msg_type == MessageType.DATA:
        # 二进制 payload：前 16 字节为 UUID，其余为原始 data bytes
        if len(payload) < Protocol.UUID_SIZE:
            return None, remaining
        conn_id = str(uuid.UUID(bytes=payload[:Protocol.UUID_SIZE]))
        data_bytes = payload[Protocol.UUID_SIZE:]
        message = Message(MessageType.DATA, conn_id=conn_id, data=data_bytes)
    else:
        # 控制消息走 JSON
        try:
            d = json.loads(payload.decode("utf-8"))
            message = Message.from_dict(d)
        except (json.JSONDecodeError, ValueError, KeyError):
            return None, remaining

    return message, remaining
```

#### 消息分发

```python
# frpc.py - process_message()
async def process_message(self, message):
    try:
        if message.type == MessageType.NEW_CONN:
            await self.handle_new_conn(message)     # ← 处理 NEW_CONN
        elif message.type == MessageType.PONG:
            pass
        elif message.type == MessageType.DATA:
            # 数据通道激活时 DATA 走数据通道，控制通道跳过，避免重复处理
            if not (self.data_writer and not self.data_writer.is_closing()):
                await self.handle_data(message)
        elif message.type == MessageType.CLOSE:
            # 同上：数据通道激活时 CLOSE 也由数据通道处理
            if not (self.data_writer and not self.data_writer.is_closing()):
                await self.handle_close(message)
        elif message.type == MessageType.ERROR:
            self.logger.error(f"Server error: {message.payload.get('message')}")
        else:
            self.logger.warning(f"Unknown message type: {message.type}")
    except Exception as e:
        self.logger.error(f"Error processing {message.type}: {e}")
```

> **数据通道激活时的跳过逻辑**：当客户端已建立独立数据通道（`self.data_writer` 存在且未关闭）时，DATA/CLOSE 消息会从数据通道到达（由 `handle_data_messages` 处理），控制通道的 `process_message` 主动跳过这两类消息，保证每条连接只被一个读取循环处理，避免重复写入。

---

### 3.7 阶段7：客户端查找代理配置

#### 提取消息字段

```python
# frpc.py - handle_new_conn()
proxy_name = message.payload.get("proxy_name")  # "web"
conn_id = message.payload.get("conn_id")        # "a1b2c3d4-..."
```

#### 查找本地服务配置

```python
# frpc.py - handle_new_conn()
proxy_info = self.state.proxies.get(proxy_name)
if not proxy_info:
    self.logger.error(f"Unknown proxy: {proxy_name}")
    return

local_ip = proxy_info.get("local_ip", "127.0.0.1")  # 本地服务IP
local_port = proxy_info.get("local_port")             # 本地服务端口
```

**代理配置来源**：客户端启动时从配置文件读取并保存：

```python
# frpc.py - register_proxies()
self.state.proxies[proxy.get("name")] = {
    "type": proxy.get("type", "tcp"),
    "local_port": proxy.get("local_port"),
    "local_ip": proxy.get("local_ip", "127.0.0.1"),
}
```

示例配置：
```json
{
    "name": "web",
    "type": "tcp",
    "local_ip": "127.0.0.1",
    "local_port": 8000,
    "remote_port": 8080
}
```

---

### 3.8 阶段8：客户端建立本地连接

`handle_new_conn` 会先校验 `proxy_info`，再按代理 `type` 分支处理（tcp/udp），并对本地连接的 writer 调用 `_optimize_socket` 调优。

#### TCP 连接建立

```python
# frpc.py - handle_new_conn()
if proxy_info.get("type") == "tcp":
    local_reader, local_writer = await asyncio.wait_for(
        asyncio.open_connection(local_ip, local_port), timeout=5
    )
    _optimize_socket(local_writer)   # TCP_NODELAY + 256KB 收发缓冲
```

**关键设计**：
- `asyncio.wait_for(..., timeout=5)`：5秒超时，防止本地服务无响应时永久阻塞
- 连接成功：获得 `local_reader` 和 `local_writer`（已开启 `TCP_NODELAY` 并调大缓冲区）
- 连接失败：抛出异常，进入错误处理

#### 连接失败处理

```python
# frpc.py - handle_new_conn()
except Exception as e:
    self.logger.warning(
        f"Failed to connect to local service {local_ip}:{local_port}: {e}"
    )
    close_msg = Message(MessageType.CLOSE, conn_id=conn_id)
    try:
        writer = self._data_writer_for(conn_id)   # 优先数据通道，回退控制通道
        writer.write(Protocol.encode(close_msg))
        await writer.drain()
    except:
        pass
```

**失败处理流程**：
1. 记录 warning 日志（含本地 `IP:端口`，便于排查）
2. 通过 `_data_writer_for(conn_id)` 选择 writer（数据通道激活时走数据通道，否则走控制通道）发送 CLOSE 消息通知服务端
3. 服务端收到 CLOSE 后调用 `close_conn(conn_id)` 清理连接状态

#### UDP 连接建立

```python
# frpc.py - handle_new_conn()
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
```

---

### 3.9 阶段9：客户端保存连接状态

#### TCP 连接状态保存

```python
# frpc.py - handle_new_conn()
self.state.conn_pool[conn_id] = {
    "proxy_name": proxy_name,
    "local_reader": local_reader,   # 本地服务的读取端
    "local_writer": local_writer,   # 本地服务的写入端
    "created_at": time.time(),      # 创建时间（用于空闲超时清理）
    "last_activity": time.time(),   # 最后活跃时间（每次读写时更新）
}
```

**状态映射**：

```
conn_pool = {
    "a1b2c3d4-...": {
        "proxy_name": "web",
        "local_reader": <StreamReader>,  # 读取本地服务响应
        "local_writer": <StreamWriter>,  # 向本地服务发送数据
        "created_at": 1783699000.0,      # 创建时间
        "last_activity": 1783699005.0,   # 最后活跃时间
    },
    "e5f6g7h8-...": {
        "proxy_name": "ssh",
        "local_reader": <StreamReader>,
        "local_writer": <StreamWriter>,
        "created_at": 1783699010.0,
        "last_activity": 1783699015.0,
    },
}
```

> 注：`created_at` / `last_activity` 字段与服务端 `conn_pool` 保持一致，用于空闲超时清理（见 4.1 节）。

---

### 3.10 阶段10：客户端发送 INIT_CONN

#### 消息构造与发送

```python
# frpc.py - handle_new_conn()
init_msg = Message(MessageType.INIT_CONN, conn_id=conn_id)
self.control_writer.write(Protocol.encode(init_msg))
await self.control_writer.drain()
```

#### INIT_CONN 消息结构

```json
{
    "type": "init_conn",
    "conn_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
}
```

**INIT_CONN 的作用**：
- 通知服务端：客户端的本地连接已建立完毕
- 携带 `conn_id` 让服务端知道是哪个连接就绪了

---

### 3.11 阶段11：服务端处理 INIT_CONN

#### 接收并处理

```python
# frps.py - handle_init_conn()
async def handle_init_conn(self, message, writer, session=None):
    conn_id = message.payload.get("conn_id")
    conn_info = self.conn_pool.get(conn_id)
    if conn_info:
        # 优先使用 session 的数据通道 writer，回退到控制通道 writer
        if session and session.get("data_writer"):
            conn_info["client_writer"] = session["data_writer"]
        else:
            conn_info["client_writer"] = writer
        conn_info["client_ready"].set()            # 触发 Event，解除转发任务的阻塞
        conn_info["last_activity"] = time.time()
    else:
        self.logger.warning(f"Connection not found for INIT_CONN: {conn_id}")
```

#### 关键操作

| 操作 | 说明 |
|------|------|
| `session["data_writer"]` 优先 | 数据通道激活时，`client_writer` 绑定到独立数据连接的 writer，DATA 流量从控制通道剥离 |
| 回退 `writer`（控制通道） | 数据通道未建立或已断开时，`client_writer` 绑定到 INIT_CONN 消息所在的控制通道 writer |
| `conn_info["client_ready"].set()` | 触发 Event，让 `forward_proxy_data` 从 `await client_ready.wait()` 恢复执行 |

> **writer 选择为何依赖 session**：`handle_init_conn` 由 `process_message` 调用时传入当前连接对应的 `session`（见 1.3 节）。服务端通过 `session["data_writer"]` 判断客户端是否已建立数据通道；若已建立，则把 `client_writer` 指向数据通道，使后续 `forward_proxy_data` 发出的 DATA 帧走独立连接，控制通道只承载小流量控制消息。

#### 解除阻塞后的效果

```
forward_proxy_data 任务:
    │
    ├── await conn_info["client_ready"].wait()  ← 阻塞中
    │
    │  （INIT_CONN 到达，client_ready.set()）
    │
    ▼  恢复执行
    while True:
        data = await conn_info["proxy_reader"].read(READ_BUF_SIZE)   # 64KB
        data_msg = Message(MessageType.DATA, conn_id=conn_id, data=data)  # 原始二进制
        conn_info["client_writer"].write(Protocol.encode(data_msg))  ← client_writer 已就绪
        await conn_info["client_writer"].drain()
```

---

### 3.12 阶段12：客户端启动本地数据转发

#### 启动转发任务

```python
# frpc.py - handle_new_conn()
asyncio.create_task(self.forward_local_data(conn_id))
```

#### 转发逻辑

```python
# frpc.py - forward_local_data()
async def forward_local_data(self, conn_id):
    conn_info = self.state.conn_pool.get(conn_id)
    if not conn_info:
        return

    try:
        while True:
            data = await conn_info["local_reader"].read(READ_BUF_SIZE)  # 64KB，读取本地服务响应
            if not data:
                break

            conn_info["last_activity"] = time.time()
            data_msg = Message(
                MessageType.DATA,
                conn_id=conn_id,
                data=data,                                  # 原始二进制 bytes
            )
            writer = self._data_writer_for(conn_id)         # 优先数据通道，回退控制通道
            writer.write(Protocol.encode(data_msg))
            await writer.drain()
    except ConnectionResetError:
        self.logger.debug(f"Local connection reset: {conn_id}")
    except Exception as e:
        self.logger.debug(f"Error forwarding local data: {e}")
    finally:
        self.close_conn(conn_id)
```

#### 客户端 handle_data（接收服务端 DATA 并写入本地）

```python
# frpc.py - handle_data()
async def handle_data(self, message):
    conn_id = message.payload.get("conn_id")
    data = message.payload.get("data", b"")          # 已是 bytes，无需 fromhex

    conn_info = self.state.conn_pool.get(conn_id)
    if not conn_info:
        return

    conn_info["last_activity"] = time.time()

    try:
        if conn_info.get("local_writer"):
            conn_info["local_writer"].write(data)
            await conn_info["local_writer"].drain()
        elif conn_info.get("udp_transport"):
            conn_info["udp_transport"].sendto(data)
    except Exception as e:
        self.logger.debug(f"Error writing to local: {e}")
        self.close_conn(conn_id)
```

> **data 默认值**：服务端/客户端 `handle_data` 中 `data = message.payload.get("data", b"")`，默认值为空 bytes（`b""`），而非空字符串。`Protocol.decode` 对 DATA 帧已解析出 `data_bytes`，调用方直接使用即可，无需 `bytes.fromhex`。

---

## 4. 连接状态全貌

### 4.1 NEW_CONN 阶段结束后的状态

#### 服务端状态

```python
self.conn_pool["a1b2c3d4-..."] = {
    "proxy_name": "web",
    "proxy_reader": <StreamReader>,      # 外部用户的读取端
    "proxy_writer": <StreamWriter>,      # 外部用户的写入端
    "client_writer": <StreamWriter>,     # 由 handle_init_conn 按 session 选择：数据通道 writer 或控制通道 writer
    "client_ready": <Event - 已触发>,     # 已触发
    "created_at": 1783699000.0,          # 创建时间
    "last_activity": 1783699005.0,       # 最后活跃时间
}
```

#### 客户端状态

```python
self.state.conn_pool["a1b2c3d4-..."] = {
    "proxy_name": "web",
    "local_reader": <StreamReader>,      # 本地服务的读取端
    "local_writer": <StreamWriter>,      # 本地服务的写入端
    "created_at": 1783699000.0,          # 创建时间
    "last_activity": 1783699005.0,       # 最后活跃时间
}
```

> 另外客户端还维护 `self.data_writer` / `self.data_reader`（独立数据通道）与 `self.session_id`（LOGIN 后获得），它们不属于 `conn_pool`，但决定了 DATA 帧走数据通道还是控制通道。

### 4.2 数据通路全貌

数据通路分为**控制通道**（`bind_port`，承载 NEW_CONN/INIT_CONN/CLOSE/PING 等小消息）与**数据通道**（`data_port`，承载 DATA 帧，需先完成 `DATA_AUTH` 握手）。数据通道未激活时 DATA 回退到控制通道。

```
外部用户                 服务端                                客户端                    本地服务
    │                     │                                     │                        │
    │   ┌── 控制通道 (bind_port 7000) ────────────────────────┐│                        │
    │   │                 │  control_writer ◄── control_reader ││                        │
    │   │                 │  NEW_CONN / INIT_CONN / CLOSE / PING/PONG                 │
    │   │                 │                                     ││                        │
    │   └── 数据通道 (data_port，DATA_AUTH 握手后激活) ───────┐││                        │
    │                     │  data_writer ◄── data_reader        ││                        │
    │                     │  DATA 帧（二进制 UUID + raw bytes） ││                        │
    │                     │                                     ││                        │
    │── proxy_reader ───►│── client_writer ──────────────────►│── local_writer ───────►│
    │   (读取外部数据)     │   (forward_proxy_data 发 DATA)      │   (handle_data 写本地)  │
    │                     │   ↑ 数据通道激活时走 data_writer     │                        │
    │                     │     未激活时回退 control_writer      │                        │
    │                     │                                     │                        │
    │◄── proxy_writer ───│◄── handle_data 收 DATA ────────────│◄── local_reader ───────│
    │   (写回外部用户)     │   (client → server 方向 DATA)        │   (forward_local_data) │
    │                     │   ↑ 客户端经 _data_writer_for 选    │   读本地响应发 DATA     │
    │                     │     data_writer 或 control_writer   │                        │
```

> **通道选择规则**：
> - 服务端→客户端方向：`forward_proxy_data` 使用 `conn_info["client_writer"]`，该 writer 由 `handle_init_conn` 按 session 绑定（数据通道优先）。
> - 客户端→服务端方向：`forward_local_data` 与连接失败时的 CLOSE 都通过 `_data_writer_for(conn_id)` 选择 writer（数据通道激活时为 `data_writer`，否则为 `control_writer`）。

---

## 5. 异常场景分析

### 5.1 本地服务不可用

```
外部用户 ──► 服务端 ──NEW_CONN──► 客户端
                                    │
                                    │ asyncio.open_connection() 失败
                                    │ (本地服务未启动/端口错误)
                                    │
                                    ▼
                              发送 CLOSE 消息
                                    │
服务端 ◄── CLOSE ───────────────────┘
    │
    ▼
  close_conn(conn_id)
  清理连接状态
  关闭外部用户连接
```

### 5.2 客户端本地连接超时

```
客户端收到 NEW_CONN
    │
    │ asyncio.wait_for(open_connection, timeout=5)
    │
    │ 5秒超时
    │
    ▼
  TimeoutError 异常
    │
    ▼
  发送 CLOSE 消息给服务端
```

### 5.3 控制连接断开

```
服务端发送 NEW_CONN
    │
    │ 控制连接断开
    │
    ▼
  客户端收不到 NEW_CONN
  无任何操作

  服务端:
  handle_control_conn 检测到连接断开（finally 块）
  _remove_session(session)         # 移除 session，使数据通道 writer 失效
  cleanup_client_by_writer(writer) # 按 writer 清理该客户端注册的代理及关联连接
```

> 实际清理由 `handle_control_conn` 的 `finally` 块触发：先调用 `_remove_session(session)` 移除 session（让 `session["data_writer"]` 失效，后续 INIT_CONN 回退到控制通道 writer），再调用 `cleanup_client_by_writer(writer)` 按 writer 匹配并移除该客户端注册的所有代理（`_remove_proxy`），并清理 `conn_pool` 中归属于这些代理的连接。文档原先写的 `cleanup_client()` 方法并不存在。

### 5.4 外部用户在等待期间断开

```
服务端发送 NEW_CONN
    │
    │ 启动 forward_proxy_data 任务（等待 client_ready）
    │
    │ 外部用户断开连接
    │
    ▼
  proxy_reader.read() 返回空数据
  但 forward_proxy_data 还在等待 client_ready
  无法立即检测到外部用户断开

  解决方案：client_ready 超时（30s）后触发清理
  forward_proxy_data 的 except asyncio.TimeoutError 分支
  调用 close_conn(conn_id) 关闭外部连接并移除状态
```

---

## 6. 消息交互总结

### 6.1 消息序列

```
步骤  方向            消息类型        payload                              说明
────────────────────────────────────────────────────────────────────────────
0a   客户端→服务端    LOGIN           token                                控制连接认证（auth_token 开启时）
0b   服务端→客户端    LOGIN_RESP      status, session_id, data_port        返回 session_id 与数据端口
0c   客户端→服务端    DATA_AUTH       session_id                           数据通道握手（走 data_port）
0d   服务端→客户端    DATA_AUTH_RESP  status                               数据通道握手结果
1    服务端→客户端    NEW_CONN        proxy_name, conn_id                  通知有新连接（控制通道）
2    客户端→服务端    INIT_CONN       conn_id                              通知本地连接就绪（控制通道）
3    服务端→客户端    DATA            conn_id, data(binary)                转发外部数据（数据通道优先）
4    客户端→服务端    DATA            conn_id, data(binary)                转发本地响应（数据通道优先）
5    服务端→客户端    CLOSE           conn_id                              通知连接关闭
（或）
5    客户端→服务端    CLOSE           conn_id                              通知本地连接关闭
```

> **DATA 帧说明**：DATA 消息的 payload 为二进制（16 字节 UUID + raw bytes），不再使用 hex 编码。控制消息（NEW_CONN/INIT_CONN/CLOSE/LOGIN 等）仍为 JSON。LOGIN/DATA_AUTH 系列是 NEW_CONN 阶段的前置握手，列于步骤 0 便于对照。

### 6.2 消息格式

#### NEW_CONN

```json
{
    "type": "new_conn",
    "proxy_name": "web",
    "conn_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
}
```

#### INIT_CONN

```json
{
    "type": "init_conn",
    "conn_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
}
```

#### CLOSE

```json
{
    "type": "close",
    "conn_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
}
```

---

## 7. 关键设计总结

### 7.1 同步机制：asyncio.Event

```
服务端:                                    客户端:
                                          
forward_proxy_data()                      handle_new_conn()
    │                                         │
    │ await client_ready.wait()               │ await open_connection(local)
    │ ← 阻塞等待                              │
    │                                         │ 发送 INIT_CONN
    │                                         │
    │ ← client_ready.set() (收到INIT_CONN)    │
    │                                         │
    │ 开始转发数据                             │ 启动 forward_local_data
```

**为什么用 Event 而不是轮询？**
- Event 是协程级别的同步，不消耗 CPU
- 响应即时，`set()` 后等待方立即恢复
- 比 `while client_writer is None` 轮询高效

### 7.2 连接ID：UUID

| 特性 | 说明 |
|------|------|
| 唯一性 | UUID v4 碰撞概率约 $1/2^{122}$，可忽略 |
| 无需协调 | 各端独立生成，无需中心分配 |
| 双重编码 | 控制消息（NEW_CONN/INIT_CONN/CLOSE 等）中以字符串形式出现，便于 JSON 编码与字典键使用；DATA 帧中以 16 字节二进制 UUID 形式出现在 payload 头部，避免字符串解析开销 |

### 7.3 控制通道与数据通道分离

MyFRP 默认把所有消息复用同一条 TCP 控制连接（`bind_port`，默认 7000）；当服务端开放 `data_port` 时，客户端在 LOGIN 后会再建立一条独立的数据连接，把高吞吐的 DATA 帧从控制连接剥离出去。

```
控制连接 (bind_port 7000) —— 始终存在
    │
    ├── LOGIN / LOGIN_RESP
    ├── REGISTER 消息
    ├── NEW_CONN 消息     ← 本阶段
    ├── INIT_CONN 消息    ← 本阶段
    ├── DATA 消息         ← 仅在数据通道未激活时回退到此
    ├── CLOSE 消息        ← 同上
    ├── PING/PONG 消息
    └── ERROR 消息

数据连接 (data_port) —— DATA_AUTH 握手成功后激活
    │
    ├── DATA_AUTH / DATA_AUTH_RESP  握手（用 session_id 绑定）
    └── DATA 帧（二进制 UUID + raw bytes）  ← 数据通道激活时承载所有 DATA
```

**通道选择机制**：
- 服务端→客户端：`handle_init_conn` 根据 `session["data_writer"]` 是否存在，决定 `conn_info["client_writer"]` 绑定到数据通道 writer 还是控制通道 writer。
- 客户端→服务端：`forward_local_data` 与失败时的 CLOSE 通过 `_data_writer_for(conn_id)` 选择 writer（`data_writer` 存在且未关闭时用数据通道，否则回退控制通道）。
- 客户端 `process_message` 在数据通道激活时主动跳过 DATA/CLOSE，避免同一连接被两个读取循环重复处理。

**优势**：
- 控制通道只承载小流量控制消息，避免被大数据流阻塞导致心跳/NEW_CONN 延迟。
- 数据通道可独立调优（`TCP_NODELAY` + 256KB 缓冲），且断开不影响控制通道。
- 数据通道未激活或断开时自动回退到控制通道，兼容旧配置（未设置 `data_port` 时行为与单通道一致），天然穿透 NAT。