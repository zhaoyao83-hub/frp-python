# MyFRP 扩展方案：分离数据通道

> **实现状态说明**：P2 数据通道分离已实现。实际实现采用**会话复用模式**（每个客户端一条数据连接，所有 conn_id 共享），而非本文档下述的每连接独立通道方案。关键消息为 `DATA_AUTH`/`DATA_AUTH_RESP`（而非文档中的 `NEW_DATA_CONN`/`DATA_CONN_READY`/`ASSIGN_CHANNEL`）。
>
> **已实现的核心流程**：
> 1. 客户端登录后获得 `session_id` 和 `data_port`
> 2. 客户端连接数据端口，发送 `DATA_AUTH`（携带 `session_id`）
> 3. 服务端校验 session 并关联数据连接，回复 `DATA_AUTH_RESP`
> 4. `INIT_CONN` 时服务端将 `client_writer` 指向 session 的 `data_writer`
> 5. 后续 DATA/CLOSE 通过数据通道传输
> 6. 未配置 `data_port` 时自动回退到控制通道复用（向后兼容）
>
> **配置项差异**：
> - `data_bind_addr`：未实现，数据服务复用 `bind_addr` 绑定地址
> - `max_data_channels`：未实现，会话复用模式下每客户端固定一条数据连接
> - `use_data_channel`：未实现，只要 `data_port` 存在即自动启用数据通道
> - 协议层已升级为二进制帧格式（8 字节头：magic 0xAA + version + type + flags + 4B BE length），非文档正文中的纯 JSON 方案
>
> 本文档下述内容为最初设计规划，保留作为参考。

## 1. 概述

### 1.1 当前架构问题

当前实现将控制消息和数据传输复用同一连接，存在以下局限性：

| 问题 | 说明 | 影响 |
|------|------|------|
| **带宽竞争** | 控制消息和数据共用同一连接带宽 | 高数据流量时，心跳和控制消息可能延迟 |
| **性能瓶颈** | 单连接串行处理，无法利用多核 | 高并发场景下性能受限 |
| **连接阻塞** | 数据传输阻塞时影响控制通道 | 连接稳定性受影响 |
| **流量混杂** | 控制消息和数据混在一起传输 | 不利于流量监控和调试 |

### 1.2 扩展目标

- **提高吞吐量**：数据通道独立，充分利用网络带宽
- **降低延迟**：控制消息不受数据传输影响
- **提高并发**：支持多数据通道并行处理
- **便于监控**：控制流量和数据流量分离，便于统计和分析

### 1.3 方案选择

采用**分离通道架构**：控制通道和数据通道独立，客户端主动建立数据通道连接。

---

## 2. 扩展架构设计

### 2.1 整体架构

```
┌─────────────────────────────────────────────────────────────────────┐
│                         客户端 (Client)                              │
│                                                                     │
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────┐          │
│  │ 本地服务 A   │    │ 本地服务 B   │    │ 本地服务 C   │          │
│  │ 127.0.0.1:80 │    │ 127.0.0.1:22 │    │ 127.0.0.1:53 │          │
│  └──────┬───────┘    └──────┬───────┘    └──────┬───────┘          │
│         │                   │                   │                  │
│         └──────────┬────────┴───────────────────┘                  │
│                    │                                               │
│              ┌─────┴─────┐                                          │
│              │ FRPClient │                                          │
│              │           │                                          │
│              │ 控制通道   │                                          │
│              │ (TCP:7000)│                                          │
│              └─────┬─────┘                                          │
│                    │                                                │
│         ┌──────────┼──────────┐                                     │
│         ▼          ▼          ▼                                     │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐                             │
│  │ 数据通道1 │ │ 数据通道2 │ │ 数据通道3 │                             │
│  │(TCP:7001)│ │(TCP:7001)│ │(TCP:7001)│                             │
│  └─────┬────┘ └─────┬────┘ └─────┬────┘                             │
│        │            │            │                                  │
└────────┼────────────┼────────────┼──────────────────────────────────┘
         │            │            │
         │            │            │
         │            │            │ TCP
         ▼            ▼            ▼
┌─────────────────────────────────────────────────────────────────────┐
│                         服务端 (Server)                              │
│                                                                     │
│                    ┌─────────────┐                                  │
│                    │ FRPServer   │                                  │
│                    │             │                                  │
│                    │ 控制服务    │                                  │
│                    │ (TCP:7000)  │                                  │
│                    └──────┬──────┘                                  │
│                           │                                         │
│         ┌─────────────────┼─────────────────┐                       │
│         ▼                 ▼                 ▼                       │
│  ┌───────────┐      ┌───────────┐      ┌───────────┐                │
│  │ 数据服务   │      │ 数据服务   │      │ 数据服务   │                │
│  │ (TCP:7001)│      │ (TCP:7001)│      │ (TCP:7001)│                │
│  └──────┬────┘      └──────┬────┘      └──────┬────┘                │
│         │                  │                  │                     │
│         └──────────────────┼──────────────────┘                     │
│                            │                                        │
│   ┌───────────┬────────────┼────────────┬───────────┐               │
│   ▼           ▼            ▼            ▼           ▼               │
│ ┌──────┐   ┌──────┐    ┌──────┐    ┌──────┐   ┌──────┐             │
│ │ 8080 │   │ 8081 │    │ 8082 │    │ 8083 │   │ 8084 │             │
│ │ TCP  │   │ TCP  │    │ UDP  │    │ TCP  │   │ UDP  │             │
│ └──┬───┘   └──┬───┘    └──┬───┘    └──┬───┘   └──┬───┘             │
│    │          │           │           │          │                  │
│    └──────────┴───────────┴───────────┴──────────┘                  │
│                    外部访问入口                                       │
└─────────────────────────────────────────────────────────────────────┘
```

### 2.2 架构特点

| 特点 | 说明 |
|------|------|
| **双通道分离** | 控制通道和数据通道独立运行 |
| **客户端主动连接** | 数据通道由客户端主动建立，穿透NAT |
| **多通道支持** | 支持多个数据通道并行传输 |
| **动态分配** | 数据通道根据需要动态建立和释放 |

---

## 3. 扩展协议设计

### 3.1 新增消息类型

| 消息类型 | 方向 | 说明 |
|----------|------|------|
| NEW_DATA_CONN | 服务端 → 客户端 | 通知客户端建立新的数据通道 |
| DATA_CONN_READY | 客户端 → 服务端 | 客户端通知服务端数据通道已就绪 |
| ASSIGN_CHANNEL | 服务端 → 客户端 | 分配数据通道给特定连接 |

### 3.2 消息结构

#### NEW_DATA_CONN

```python
Message(
    type="new_data_conn",
    payload={
        "conn_id": "uuid-xxx",       # 连接ID
        "proxy_name": "web",         # 代理名称
        "data_port": 7001,           # 数据服务端口
        "token": "abc-123"           # 通道认证token
    }
)
```

#### DATA_CONN_READY

```python
Message(
    type="data_conn_ready",
    payload={
        "conn_id": "uuid-xxx",       # 连接ID
        "channel_id": "ch-001"       # 数据通道ID
    }
)
```

#### ASSIGN_CHANNEL

```python
Message(
    type="assign_channel",
    payload={
        "conn_id": "uuid-xxx",       # 连接ID
        "channel_id": "ch-001"       # 分配的数据通道ID
    }
)
```

### 3.3 消息流向

```
控制通道:
  REGISTER    → 客户端注册代理
  NEW_CONN    → 服务端通知新连接
  NEW_DATA_CONN → 服务端请求建立数据通道
  DATA_CONN_READY → 客户端确认数据通道就绪
  ASSIGN_CHANNEL → 服务端分配数据通道
  PING/PONG   → 心跳
  CLOSE       → 关闭连接
  ERROR       → 错误信息

数据通道:
  DATA        → 数据传输（仅包含 conn_id 和 data）
```

---

## 4. 扩展流程设计

### 4.1 数据通道建立流程

```
1. 控制连接建立（同当前实现）
   客户端 ──TCP──► 服务端 (控制端口 7000)

2. 代理注册（同当前实现）
   客户端 ──REGISTER──► 服务端

3. 外部客户端连接代理端口
   外部客户端 ──TCP──► 服务端 (代理端口 8080)

4. 服务端发送 NEW_DATA_CONN
   服务端 ──NEW_DATA_CONN(conn_id, data_port, token)──► 客户端

5. 客户端建立数据通道
   客户端 ──TCP──► 服务端 (数据端口 7001)
   携带 token 进行认证

6. 客户端发送 DATA_CONN_READY
   客户端 ──DATA_CONN_READY(conn_id, channel_id)──► 服务端 (通过控制通道)

7. 服务端分配数据通道
   服务端 ──ASSIGN_CHANNEL(conn_id, channel_id)──► 客户端 (通过控制通道)

8. 客户端建立本地连接
   客户端 ──TCP──► 本地服务

9. 数据传输（通过数据通道）
   外部客户端 ──► 服务端 ──DATA──► 客户端 ──► 本地服务
   本地服务 ──► 客户端 ──DATA──► 服务端 ──► 外部客户端
```

### 4.2 时序图

```
外部客户端        服务端          客户端          本地服务
    │              │              │              │
    │              │              │              │
    │              │◄──控制连接───│              │
    │              │              │              │
    │              │◄──REGISTER──│              │
    │              │              │              │
    │──连接代理端口─►│              │              │
    │              │              │              │
    │              │──NEW_DATA_CONN─►│              │
    │              │              │              │
    │              │◄──数据通道───│              │
    │              │              │              │
    │              │◄──DATA_CONN_READY──│              │
    │              │              │              │
    │              │──ASSIGN_CHANNEL─►│              │
    │              │              │              │
    │              │              │──连接本地服务─►│
    │              │              │              │
    │◄──DATA──────│◄──DATA──────│              │
    │              │              │◄──DATA──────│
    │              │              │              │
```

---

## 5. 扩展配置设计

### 5.1 服务端配置扩展

```json
{
    "bind_port": 7000,
    "bind_addr": "0.0.0.0",
    "data_port": 7001,
    "data_bind_addr": "0.0.0.0",
    "max_data_channels": 100,
    "log_level": "info"
}
```

| 新增参数 | 类型 | 默认值 | 说明 |
|----------|------|--------|------|
| data_port | int | 7001 | 数据服务监听端口 |
| data_bind_addr | string | 0.0.0.0 | 数据服务绑定地址 |
| max_data_channels | int | 100 | 最大数据通道数 |

### 5.2 客户端配置扩展

```json
{
    "server_addr": "127.0.0.1",
    "server_port": 7000,
    "data_port": 7001,
    "use_data_channel": true,
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

| 新增参数 | 类型 | 默认值 | 说明 |
|----------|------|--------|------|
| data_port | int | 7001 | 服务端数据端口 |
| use_data_channel | bool | true | 是否使用独立数据通道 |

---

## 6. 扩展代码设计

### 6.1 服务端扩展 (frps.py)

#### 6.1.1 新增数据服务启动

```python
async def start(self):
    bind_addr = self.config.get("bind_addr", "0.0.0.0")
    bind_port = self.config.get("bind_port", 7000)
    data_bind_addr = self.config.get("data_bind_addr", "0.0.0.0")
    data_port = self.config.get("data_port", 7001)

    self.control_server = await asyncio.start_server(
        self.handle_control_conn, bind_addr, bind_port
    )
    
    self.data_server = await asyncio.start_server(
        self.handle_data_conn, data_bind_addr, data_port
    )

    self.logger.info(f"FRPServer started on {bind_addr}:{bind_port}")
    self.logger.info(f"Data Server started on {data_bind_addr}:{data_port}")

    async with asyncio.TaskGroup() as tg:
        tg.create_task(self.control_server.serve_forever())
        tg.create_task(self.data_server.serve_forever())
```

#### 6.1.2 数据通道处理

```python
async def handle_data_conn(self, reader, writer):
    addr = writer.get_extra_info("peername")
    self.logger.info(f"New data connection from {addr}")
    
    buffer = b""
    channel_id = None
    
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
                    
                await self.process_data_message(message, writer)
                
    except Exception as e:
        self.logger.error(f"Data connection error: {e}")
    finally:
        self.cleanup_channel(channel_id)
        writer.close()
        await writer.wait_closed()

async def process_data_message(self, message, writer):
    if message.type == MessageType.DATA:
        conn_id = message.payload.get("conn_id")
        data = message.payload.get("data", "")
        
        conn_info = self.state.conn_pool.get(conn_id)
        if conn_info:
            try:
                conn_info["proxy_writer"].write(bytes.fromhex(data))
                await conn_info["proxy_writer"].drain()
            except Exception as e:
                self.logger.error(f"Error writing to proxy: {e}")
                self.close_conn(conn_id)
```

#### 6.1.3 发送 NEW_DATA_CONN

```python
async def handle_tcp_proxy_conn(self, reader, writer, proxy_name):
    proxy_info = self.state.proxies.get(proxy_name)
    conn_id = Protocol.generate_conn_id()
    
    token = self.generate_token()
    
    new_data_conn_msg = Message(
        MessageType.NEW_DATA_CONN,
        conn_id=conn_id,
        proxy_name=proxy_name,
        data_port=self.config.get("data_port", 7001),
        token=token,
    )
    
    proxy_info["control_writer"].write(Protocol.encode(new_data_conn_msg))
    await proxy_info["control_writer"].drain()
    
    self.state.pending_conns[conn_id] = {
        "proxy_name": proxy_name,
        "proxy_reader": reader,
        "proxy_writer": writer,
        "token": token,
        "channel_id": None,
    }
```

### 6.2 客户端扩展 (frpc.py)

#### 6.2.1 数据通道管理

```python
class ClientState:
    def __init__(self):
        self.proxies = {}
        self.conn_pool = {}
        self.data_channels = {}      # 新增：数据通道池
        self.pending_data_conns = {} # 新增：待建立的数据连接
```

#### 6.2.2 处理 NEW_DATA_CONN

```python
async def process_message(self, message):
    if message.type == MessageType.NEW_CONN:
        await self.handle_new_conn(message)
    elif message.type == MessageType.NEW_DATA_CONN:
        await self.handle_new_data_conn(message)
    elif message.type == MessageType.PONG:
        pass
    elif message.type == MessageType.DATA:
        await self.handle_data(message)
    elif message.type == MessageType.CLOSE:
        await self.handle_close(message)
    elif message.type == MessageType.ERROR:
        self.logger.error(f"Server error: {message.payload.get('message')}")

async def handle_new_data_conn(self, message):
    conn_id = message.payload.get("conn_id")
    proxy_name = message.payload.get("proxy_name")
    data_port = message.payload.get("data_port", 7001)
    token = message.payload.get("token")
    
    server_addr = self.config.get("server_addr", "127.0.0.1")
    
    try:
        data_reader, data_writer = await asyncio.open_connection(
            server_addr, data_port
        )
        
        auth_msg = Message(
            MessageType.DATA_CONN_AUTH,
            conn_id=conn_id,
            token=token,
        )
        data_writer.write(Protocol.encode(auth_msg))
        await data_writer.drain()
        
        channel_id = Protocol.generate_conn_id()
        
        self.state.data_channels[channel_id] = {
            "conn_id": conn_id,
            "reader": data_reader,
            "writer": data_writer,
        }
        
        ready_msg = Message(
            MessageType.DATA_CONN_READY,
            conn_id=conn_id,
            channel_id=channel_id,
        )
        self.control_writer.write(Protocol.encode(ready_msg))
        await self.control_writer.drain()
        
        asyncio.create_task(self.handle_data_channel(channel_id))
        
        self.logger.info(f"Data channel established: {channel_id}")
        
    except Exception as e:
        self.logger.error(f"Failed to establish data channel: {e}")
```

#### 6.2.3 数据通道转发

```python
async def handle_data_channel(self, channel_id):
    channel_info = self.state.data_channels.get(channel_id)
    if not channel_info:
        return
        
    buffer = b""
    
    try:
        while True:
            data = await channel_info["reader"].read(4096)
            if not data:
                break
                
            buffer += data
            
            while True:
                message, buffer = Protocol.decode(buffer)
                if not message:
                    break
                    
                await self.process_data_message(message, channel_id)
                
    except Exception as e:
        self.logger.error(f"Data channel error: {e}")
    finally:
        self.close_data_channel(channel_id)

async def process_data_message(self, message, channel_id):
    if message.type == MessageType.DATA:
        conn_id = message.payload.get("conn_id")
        data = message.payload.get("data", "")
        
        conn_info = self.state.conn_pool.get(conn_id)
        if conn_info:
            try:
                conn_info["local_writer"].write(bytes.fromhex(data))
                await conn_info["local_writer"].drain()
            except Exception as e:
                self.logger.error(f"Error writing to local: {e}")
                self.close_conn(conn_id)
```

### 6.3 协议扩展 (protocol.py)

```python
class MessageType:
    REGISTER = "register"
    NEW_CONN = "new_conn"
    NEW_DATA_CONN = "new_data_conn"
    DATA_CONN_READY = "data_conn_ready"
    DATA_CONN_AUTH = "data_conn_auth"
    ASSIGN_CHANNEL = "assign_channel"
    PING = "ping"
    PONG = "pong"
    ERROR = "error"
    CLOSE = "close"
    DATA = "data"
```

---

## 7. 扩展性设计

### 7.1 多数据通道支持

```python
# 配置最大通道数
max_data_channels = self.config.get("max_data_channels", 100)

# 通道负载均衡
async def get_available_channel(self):
    available = [
        ch_id for ch_id, ch_info in self.state.data_channels.items()
        if len(ch_info["active_conns"]) < 100
    ]
    if available:
        return available[0]
    return None
```

### 7.2 通道复用

```python
# 同一客户端的多个连接可以复用数据通道
async def reuse_data_channel(self, conn_id):
    for channel_id, channel_info in self.state.data_channels.items():
        if channel_info["client_id"] == self.client_id:
            channel_info["active_conns"].add(conn_id)
            return channel_id
    return None
```

### 7.3 通道池管理

```python
class DataChannelPool:
    def __init__(self, max_channels=100):
        self.max_channels = max_channels
        self.channels = {}
        self.free_channels = asyncio.Queue()
        
    async def get_channel(self):
        if not self.free_channels.empty():
            return await self.free_channels.get()
        
        if len(self.channels) < self.max_channels:
            channel_id = Protocol.generate_conn_id()
            self.channels[channel_id] = {"active": False}
            return channel_id
        
        raise Exception("Max data channels reached")
    
    async def release_channel(self, channel_id):
        await self.free_channels.put(channel_id)
```

---

## 8. 性能对比

### 8.1 单通道 vs 双通道

| 指标 | 单通道（当前） | 双通道（扩展） |
|------|--------------|---------------|
| 带宽利用率 | 受限（控制+数据） | 充分利用 |
| 控制消息延迟 | 受数据影响 | 独立不受影响 |
| 并发连接数 | 受限 | 大幅提升 |
| 内存占用 | 低 | 中等 |
| CPU利用率 | 单线程 | 可多线程 |

### 8.2 预期性能提升

- **吞吐量提升**：预计提升 30-50%
- **并发连接数**：支持数倍增长
- **延迟降低**：控制消息延迟降低 80% 以上

---

## 9. 兼容性设计

### 9.1 向后兼容

- 服务端支持同时启动控制服务和数据服务
- 客户端通过 `use_data_channel` 配置决定是否使用数据通道
- 不使用数据通道时，行为与当前实现一致

### 9.2 版本协商

```python
# 客户端连接时发送版本信息
hello_msg = Message(
    MessageType.HELLO,
    version="2.0",
    capabilities=["data_channel"]
)
```

---

## 10. 部署与迁移

### 10.1 服务端部署

```bash
# 修改配置文件
# 添加 data_port 参数

# 启动服务（同时启动控制服务和数据服务）
python frps.py -c frps.json

# 防火墙配置
# 开放控制端口 7000
# 开放数据端口 7001
# 开放所有代理端口
```

### 10.2 客户端部署

```bash
# 修改配置文件
# 添加 data_port 和 use_data_channel 参数

# 启动客户端
python frpc.py -c frpc.json
```

### 10.3 灰度迁移

1. 更新服务端，同时启动控制服务和数据服务
2. 部分客户端升级，启用数据通道
3. 观察性能和稳定性
4. 逐步推广到所有客户端

---

## 11. 安全性考虑

### 11.1 数据通道认证

```python
# 使用临时token认证
def generate_token(self):
    return str(uuid.uuid4()) + "-" + str(time.time())

# 验证token
async def authenticate_data_conn(self, token):
    for conn_id, conn_info in self.state.pending_conns.items():
        if conn_info.get("token") == token:
            return conn_id
    return None
```

### 11.2 通道加密

- 建议对数据通道启用 TLS/SSL 加密
- 可以使用自签名证书或 Let's Encrypt 证书

### 11.3 访问控制

- 数据通道仅允许已认证的客户端连接
- 支持 IP 白名单限制

---

## 12. 监控与运维

### 12.1 通道状态监控

```python
async def get_channel_stats(self):
    stats = {
        "total_channels": len(self.state.data_channels),
        "active_channels": sum(
            1 for ch in self.state.data_channels.values()
            if ch.get("active")
        ),
        "pending_conns": len(self.state.pending_conns),
    }
    return stats
```

### 12.2 日志增强

```python
# 数据通道日志
self.logger.info(f"Data channel {channel_id}: {bytes_transferred} bytes transferred")

# 连接统计日志
self.logger.info(f"Connection {conn_id}: latency={latency}ms")
```

---

## 13. 总结

### 13.1 扩展收益

- **性能提升**：分离通道后吞吐量和并发能力大幅提升
- **稳定性增强**：控制消息不受数据传输影响
- **可扩展性好**：支持多通道、通道复用等高级特性
- **兼容性强**：支持灰度迁移，不影响现有业务

### 13.2 实施建议

1. **第一阶段**：实现基本的双通道分离
2. **第二阶段**：添加通道认证和加密
3. **第三阶段**：实现通道复用和负载均衡
4. **第四阶段**：添加监控和运维能力

### 13.3 风险评估

| 风险 | 影响 | 缓解措施 |
|------|------|----------|
| 复杂度增加 | 开发和维护成本上升 | 模块化设计，充分测试 |
| 兼容性问题 | 部分客户端可能不支持 | 向后兼容设计 |
| 资源占用 | 内存和CPU占用增加 | 配置合理的通道数上限 |