# P2 高性能增强：支撑高并发

> **实现状态说明**：P2 已完成二进制协议、数据通道分离、零拷贝优化三项。多进程架构尚未实现。
>
> | 能力 | 状态 | 实际实现 |
> |------|------|----------|
> | 二进制协议 | ✅ 已实现 | 8 字节头（magic 0xAA + version + type + flags + 4B BE length），DATA 用二进制 UUID + 原始字节，控制消息用 JSON |
> | 数据通道分离 | ✅ 已实现 | 会话复用模式（每客户端一条数据连接），DATA_AUTH/DATA_AUTH_RESP 握手，向后兼容 |
> | 零拷贝优化 | ✅ 已实现 | bytearray 预分配编码、64KB 读缓冲、TCP_NODELAY、256KB socket 缓冲 |
> | 多进程架构 | ❌ 未实现 | 规划中的 SO_REUSEPORT + Worker 模型 |
>
> **实测性能**（localhost，TLS + 数据通道）：
> - 5MB 文件传输吞吐量：~243 MB/s（1.95 Gbps）
> - 单请求延迟：~4ms
> - MD5 校验：通过

## 1. 概述

### 1.1 目标

在 P1 生产级能力的基础上，进行深度性能优化，使系统能够支撑**高并发场景**，在大规模流量下保持稳定的吞吐量和低延迟。

### 1.2 范围

| 能力 | 说明 |
|------|------|
| 二进制协议 | 替换 JSON 为二进制格式，减少序列化开销和网络带宽 |
| 数据通道分离 | 控制通道和数据通道独立，避免控制消息被数据阻塞 |
| 零拷贝优化 | 使用 sendfile/splice 等技术减少用户态/内核态数据拷贝 |
| 多进程架构 | 多进程模型，充分利用多核 CPU |

### 1.3 性能目标

| 指标 | P1（基线） | P2（目标） |
|------|-----------|-----------|
| 单端吞吐量 | ~100 Mbps | ~1 Gbps |
| 并发连接数 | ~1000 | ~10000 |
| 延迟（ping） | ~5ms | ~2ms |
| CPU 利用率（同流量） | 高 | 降低 50%+ |

### 1.4 前置条件

- P0 全部能力（断线重连、超时清理、Token 认证、错误处理）
- P1 全部能力（TLS 加密、访问控制、监控统计、优雅关闭）

---

## 2. 二进制协议

### 2.1 问题分析

当前协议使用 JSON 格式，存在以下性能问题：

| 问题 | 影响 |
|------|------|
| 序列化/反序列化慢 | CPU 开销大，高并发时成为瓶颈 |
| 数据体积大 | JSON 的 key 重复，带宽浪费 30-50% |
| hex 编码数据 | 二进制数据转 hex，体积膨胀 2 倍 |
| 字符串解析 | JSON 解析需要遍历字符串，效率低 |

### 2.2 设计方案

#### 2.2.1 协议格式对比

```
当前 JSON 格式:
┌──────────────────────────────────────────────────────────────────┐
│ Header (4 bytes)  │  Body (JSON)                                  │
│ 0x00000080 (128)   │  {"type":"data","conn_id":"a1b2c3d4-...",   │
│                    │   "data":"485454502f312e31..."}             │
└──────────────────────────────────────────────────────────────────┘

二进制格式:
┌──────────────────────────────────────────────────────────────────┐
│  魔数(1)  │ 版本(1) │ 类型(1)  │ 标志(1)  │ 长度(4)              │
│  0xAA     │  0x01   │  0x0A    │  0x00    │  payload 长度(BE)    │
│           │         │  (DATA)  │          │                      │
├──────────┴──────────┴──────────┴──────────┴──────────────────────┤
│                      Payload (变长)                              │
│  DATA:        conn_id(16) │ data(N)                             │
│  控制消息:    JSON UTF-8（小体积、低频）                          │
└──────────────────────────────────────────────────────────────────┘
```

**体积对比**（DATA 消息，1000字节数据）：
- JSON + hex: 约 4000 字节（JSON key + hex 膨胀）
- 二进制: 约 1024 字节（1 + 1 + 1 + 1 + 4 + 16 + 1000）
- **节省约 74% 带宽**

#### 2.2.2 消息类型编码

| 消息类型 | 值 | 说明 |
|----------|-----|------|
| LOGIN | 0x01 | 客户端登录（Token 认证） |
| LOGIN_RESP | 0x02 | 登录响应 |
| REGISTER | 0x03 | 注册代理 |
| NEW_CONN | 0x04 | 新连接通知 |
| INIT_CONN | 0x05 | 连接就绪 |
| PING | 0x06 | 心跳请求 |
| PONG | 0x07 | 心跳响应 |
| ERROR | 0x08 | 错误 |
| CLOSE | 0x09 | 关闭连接 |
| DATA | 0x0A | 数据传输 |
| DATA_AUTH | 0x0B | 数据通道认证请求 |
| DATA_AUTH_RESP | 0x0C | 数据通道认证响应 |

#### 2.2.3 固定头格式

```python
import struct
import uuid

# 协议常量
MAGIC = 0xAA            # 魔数（1 字节）
VERSION = 0x01          # 协议版本
HEADER_SIZE = 8         # 头部总长度
UUID_SIZE = 16          # UUID 二进制长度

# 头部结构: B (magic) B (version) B (type) B (flags) I (length, 大端)
_HEADER_FMT = ">BBBBI"
```

| 字段 | 大小 | 说明 |
|------|------|------|
| magic | 1 byte | 魔数 0xAA，用于快速校验 |
| version | 1 byte | 协议版本 |
| type | 1 byte | 消息类型 |
| flags | 1 byte | 标志位（当前未使用，预留） |
| length | 4 bytes | payload 长度（大端序） |

#### 2.2.4 各消息 Payload 格式

**DATA (0x0A)** —— 二进制 payload

```
┌───────────────────────────────────────────────────────────────┐
│ conn_id(16)                                                   │
├───────────────────────────────────────────────────────────────┤
│ data(N)                                                       │
└───────────────────────────────────────────────────────────────┘
```

conn_id 使用 16 字节 UUID 二进制表示；data 为原始字节（非 hex 编码）。
数据长度 N 由 header 中的 length 减去 UUID_SIZE（16）隐含得出，无独立 data_len 字段。

**控制消息（LOGIN / LOGIN_RESP / REGISTER / NEW_CONN / INIT_CONN / CLOSE / PING / PONG / ERROR / DATA_AUTH / DATA_AUTH_RESP）—— JSON payload**

控制消息 Payload 统一使用 JSON UTF-8 编码，字段与原 JSON 协议保持一致，例如：

- LOGIN：`{"type":"login","token":"..."}`
- LOGIN_RESP：`{"type":"login_resp","status":"ok","session_id":"...","data_port":7001}`
- REGISTER：`{"type":"register","name":"...","type":"...","remote_port":...,"local_ip":"...","local_port":...}`
- NEW_CONN：`{"type":"new_conn","name":"...","conn_id":"..."}`（conn_id 为 UUID 字符串）
- INIT_CONN：`{"type":"init_conn","conn_id":"..."}`
- CLOSE：`{"type":"close","conn_id":"...","reason":"..."}`
- PING/PONG：`{"type":"ping","timestamp":...}` / `{"type":"pong","timestamp":...}`
- DATA_AUTH：`{"type":"data_auth","session_id":"..."}`
- DATA_AUTH_RESP：`{"type":"data_auth_resp","status":"ok"}`

控制消息体积小、频率低，JSON 编码开销可忽略；DATA 走二进制以最大化吞吐。

#### 2.2.5 二进制协议实现

二进制编解码直接在 `protocol.py` 的 `Protocol` 类中实现，无独立 `protocol_binary.py` 文件。控制消息 payload 走 JSON，DATA 消息 payload 走二进制（UUID + 原始字节）。

```python
# protocol.py
import json
import struct
import uuid


class MessageType:
    LOGIN = "login"
    LOGIN_RESP = "login_resp"
    REGISTER = "register"
    NEW_CONN = "new_conn"
    INIT_CONN = "init_conn"
    PING = "ping"
    PONG = "pong"
    ERROR = "error"
    CLOSE = "close"
    DATA = "data"
    DATA_AUTH = "data_auth"
    DATA_AUTH_RESP = "data_auth_resp"


# String type <-> numeric code mapping for binary framing
_TYPE_TO_CODE = {
    MessageType.LOGIN: 0x01,
    MessageType.LOGIN_RESP: 0x02,
    MessageType.REGISTER: 0x03,
    MessageType.NEW_CONN: 0x04,
    MessageType.INIT_CONN: 0x05,
    MessageType.PING: 0x06,
    MessageType.PONG: 0x07,
    MessageType.ERROR: 0x08,
    MessageType.CLOSE: 0x09,
    MessageType.DATA: 0x0A,
    MessageType.DATA_AUTH: 0x0B,
    MessageType.DATA_AUTH_RESP: 0x0C,
}
_CODE_TO_TYPE = {v: k for k, v in _TYPE_TO_CODE.items()}


class Protocol:
    """Binary-framed protocol.

    Frame layout (8-byte header + payload):
      ┌────────┬─────────┬──────┬───────┬──────────────┬───────────────┐
      │ Magic  │ Version │ Type │ Flags │ Payload Len  │ Payload       │
      │ 1 byte │ 1 byte  │1 byte│1 byte │ 4 bytes (BE) │ variable      │
      └────────┴─────────┴──────┴───────┴──────────────┴───────────────┘

    Payload encoding:
      - DATA:  binary UUID (16 bytes) + raw data bytes (zero-copy, no hex)
      - others: JSON UTF-8 (small, infrequent control messages)
    """

    MAGIC = 0xAA
    VERSION = 0x01
    HEADER_SIZE = 8
    UUID_SIZE = 16
    _HEADER_FMT = ">BBBBI"  # magic, version, type, flags, length

    @staticmethod
    def encode(message):
        msg_code = _TYPE_TO_CODE.get(message.type)
        if msg_code is None:
            raise ValueError(f"Unknown message type: {message.type}")

        if message.type == MessageType.DATA:
            # Binary payload: conn_id as 16-byte UUID + raw data
            conn_id = message.payload.get("conn_id", "")
            try:
                uuid_bytes = uuid.UUID(conn_id).bytes
            except (ValueError, AttributeError):
                uuid_bytes = uuid.UUID(int=0).bytes
            data_bytes = message.payload.get("data", b"")
            if isinstance(data_bytes, str):
                # Backward-compatible: accept hex string
                data_bytes = bytes.fromhex(data_bytes) if data_bytes else b""
            # Zero-copy: pre-allocate single buffer, avoid intermediate concat
            payload_len = Protocol.UUID_SIZE + len(data_bytes)
            buf = bytearray(Protocol.HEADER_SIZE + payload_len)
            struct.pack_into(
                Protocol._HEADER_FMT, buf, 0,
                Protocol.MAGIC, Protocol.VERSION, msg_code, 0, payload_len,
            )
            buf[Protocol.HEADER_SIZE:Protocol.HEADER_SIZE + Protocol.UUID_SIZE] = uuid_bytes
            buf[Protocol.HEADER_SIZE + Protocol.UUID_SIZE:] = data_bytes
            return buf
        else:
            # JSON payload for control messages
            payload = json.dumps(message.to_dict()).encode("utf-8")
            header = struct.pack(
                Protocol._HEADER_FMT,
                Protocol.MAGIC,
                Protocol.VERSION,
                msg_code,
                0,
                len(payload),
            )
            return header + payload

    @staticmethod
    def decode(data):
        if len(data) < Protocol.HEADER_SIZE:
            return None, data

        magic, version, msg_code, flags, length = struct.unpack(
            Protocol._HEADER_FMT, data[:Protocol.HEADER_SIZE]
        )

        if magic != Protocol.MAGIC:
            # Not a valid frame start; drop one byte and resync
            return None, data[1:]

        if len(data) < Protocol.HEADER_SIZE + length:
            return None, data

        payload = data[Protocol.HEADER_SIZE : Protocol.HEADER_SIZE + length]
        remaining = data[Protocol.HEADER_SIZE + length :]

        msg_type = _CODE_TO_TYPE.get(msg_code)
        if msg_type is None:
            return None, remaining

        if msg_type == MessageType.DATA:
            if len(payload) < Protocol.UUID_SIZE:
                return None, remaining
            conn_id = str(uuid.UUID(bytes=payload[:Protocol.UUID_SIZE]))
            data_bytes = payload[Protocol.UUID_SIZE:]
            message = Message(MessageType.DATA, conn_id=conn_id, data=data_bytes)
        else:
            try:
                d = json.loads(payload.decode("utf-8"))
                message = Message.from_dict(d)
            except (json.JSONDecodeError, ValueError, KeyError):
                return None, remaining

        return message, remaining
```

**关键实现要点**：
- DATA 消息使用 `bytearray` 预分配整块缓冲，避免 `header + conn_id + data` 多次拼接产生的中间对象（零拷贝优化）。
- 控制消息 payload 走 JSON，保持与历史协议字段一致，便于调试和兼容。
- 解码时若 magic 不匹配，丢弃 1 字节后尝试重新同步，提升容错能力。
- `conn_id` 在 DATA 帧中以 16 字节 UUID 二进制传输，控制消息中仍以字符串形式出现在 JSON 内。

#### 2.2.6 性能对比

| 操作 | JSON 协议 | 二进制协议 | 提升 |
|------|-----------|-----------|------|
| DATA 编码（1KB） | ~5 μs | ~0.5 μs | 10x |
| DATA 解码（1KB） | ~8 μs | ~0.8 μs | 10x |
| 消息体积（1KB数据） | ~4KB | ~1.03KB | -74% |
| 10K QPS 序列化 CPU | ~80% | ~10% | -87% |

---

## 3. 数据通道分离

### 3.1 问题分析

当前所有数据都通过控制连接传输，存在以下问题：

| 问题 | 影响 |
|------|------|
| 控制消息被数据阻塞 | 心跳、NEW_CONN 等控制消息延迟，影响稳定性 |
| 单连接带宽瓶颈 | 所有代理共享一个 TCP 连接，总带宽受限于单连接 |
| 队头阻塞 | 某个代理的大数据传输会阻塞其他代理的消息 |
| 无法利用多路径 | 单连接无法利用多链路/多网卡 |

### 3.2 设计方案

#### 3.2.1 整体架构

```
┌─────────────────────────────────────────────────────────────────────┐
│                              客户端                                  │
│                                                                     │
│  本地服务A  本地服务B  本地服务C                                     │
│      │          │          │                                        │
│      └────┬─────┘          │                                        │
│           │                │                                        │
│     ┌─────┴──────┐         │                                        │
│     │  FRPClient │         │                                        │
│     │            │         │                                        │
│     │ 控制通道   │         │   ← 控制消息：REGISTER, NEW_CONN, 等    │
│     │ (TCP 7000) │         │                                        │
│     └─────┬──────┘         │                                        │
│           │                │                                        │
│    ┌──────┴──────┐  ┌──────┴──────┐  ┌──────────────┐              │
│    │ 数据通道1   │  │ 数据通道2   │  │ 数据通道N   │                │
│    │ (TCP 7001)  │  │ (TCP 7001)  │  │ (TCP 7001)  │                │
│    └──────┬──────┘  └──────┬──────┘  └──────┬──────┘               │
│           │                │                │                        │
└───────────┼────────────────┼────────────────┼────────────────────────┘
            │                │                │
            │ TCP (客户端主动连接)            │
            ▼                ▼                ▼
┌─────────────────────────────────────────────────────────────────────┐
│                              服务端                                  │
│                                                                     │
│     ┌──────────┐  ┌──────────┐  ┌──────────┐                        │
│     │ 数据通道1│  │ 数据通道2│  │ 数据通道N│                        │
│     │ (7001)   │  │ (7001)   │  │ (7001)   │                        │
│     └─────┬────┘  └─────┬────┘  └─────┬────┘                        │
│           │             │             │                             │
│           └──────┬──────┘             │                             │
│                  │                    │                             │
│          ┌───────┴─────────┐          │                             │
│          │  FRPServer      │          │                             │
│          │                 │          │                             │
│          │ 控制服务 (7000) │          │                             │
│          └───────┬─────────┘          │                             │
│                  │                    │                             │
│    ┌─────────────┼─────────────┐      │                             │
│    ▼             ▼             ▼      ▼                             │
│  代理端口A    代理端口B    代理端口C   ...                            │
│  (8080)       (2222)       (9090)                                  │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘
```

#### 3.2.2 通道建立流程

实际采用「会话复用」模式：每个客户端建立一条独立的数据连接，所有代理连接的 DATA 帧复用该数据连接，按 `conn_id` 区分。数据连接在客户端登录成功后建立一次，后续所有代理连接共享。

```
客户端                              服务端
    │                                  │
    │── 1. 控制通道连接（7000）──────►│
    │── 2. LOGIN (token) ──────────►│  ← 控制通道 Token 认证
    │  ◄── LOGIN_RESP (session_id,  │
    │         data_port) ────────────│
    │── 3. REGISTER (代理A/B/C) ───►│
    │                                  │
    │── 4. 建立数据连接（data_port）►│  ← 客户端主动连接 data_port
    │── 5. DATA_AUTH (session_id) ─►│  ← 在数据连接上发送会话认证
    │  ◄── DATA_AUTH_RESP (ok) ─────│  ← 服务端校验 session，通道就绪
    │                                  │
    │  （等待外部请求）                  │
    │                                  │
    │  ◄── 6. NEW_CONN (conn_id) ───│  ← 外部用户连接代理端口（经控制通道）
    │── 7. INIT_CONN (conn_id) ────►│  ← 通过控制通道通知本地连接就绪
    │                                  │
    │        ===== 数据传输 =====       │
    │  ◄────────── DATA ──────────────│  ← 外部数据（复用数据连接）
    │  ────────── DATA ──────────────►│  → 本地响应
    │                                  │
```

数据连接只在登录成功后建立一次，后续所有代理连接的 DATA 帧都复用该连接；控制消息（NEW_CONN/INIT_CONN/CLOSE/PING 等）仍走控制通道，避免被数据阻塞。若服务端未配置 `data_port`，DATA 帧退化为走控制连接，保证向后兼容。

#### 3.2.3 会话管理

实际不存在 `DataChannelPool` / `DataChannel` 类，也无 `data_channel.py` 文件。数据通道采用「会话复用」模式：服务端为每个客户端维护一个 session 字典，每客户端仅一条数据连接，所有代理连接按 `conn_id` 复用该连接。

```python
# frps.py —— 会话管理示意（内联实现，无独立类）
# 服务端持有：
#   self.sessions: Dict[str, dict]  # session_id -> session
#   self.conn_pool: Dict[str, dict] # conn_id -> 连接信息（路由 DATA 帧）

# LOGIN 成功后创建 session：
session = {
    "id": session_id,            # uuid4 生成
    "control_writer": writer,    # 控制连接
    "data_writer": None,         # 数据连接（DATA_AUTH 后绑定）
    "addr": addr,
    "proxies": set(),            # 已注册的代理名
}
self.sessions[session_id] = session

# 客户端连上 data_port 后发送 DATA_AUTH(session_id)：
#   服务端校验 session_id 存在 -> 绑定 data_writer -> 回复 DATA_AUTH_RESP(ok)
#   校验失败 -> 回复 DATA_AUTH_RESP(error) 并关闭

# 服务端转发 DATA 帧时，按 conn_id 从 conn_pool 找到对应的 proxy_writer，
# 再将数据写入外部用户的 socket；反向同理，经 session.data_writer 发回客户端。
```

**关键行为**：
- 客户端 LOGIN 成功后，服务端分配 `session_id` 并创建 session（`data_writer` 初始为 `None`）。
- 客户端连接 `data_port` 后发送 `DATA_AUTH(session_id)`，服务端校验后将 `data_writer` 绑定到该 session，回复 `DATA_AUTH_RESP(status=ok)`。
- 服务端收到 DATA 帧时，按 `conn_id` 从 `conn_pool` 找到对应代理连接，将数据写入外部用户 socket；无需为每个连接建立独立数据通道。
- 客户端发送 DATA 时优先使用数据连接的 `data_writer`，若数据连接不可用则回退到控制连接的 `control_writer`（向后兼容）。
- 连接关闭时从 `conn_pool` 中移除；session 在控制连接断开或超时后整体清理。

#### 3.2.4 通道复用策略

实际采用「多连接复用通道」一种策略：单个客户端的所有代理连接共享一条数据连接，按 `conn_id` 区分。无需池化、扩缩容等机制。

| 策略 | 说明 | 实际是否采用 |
|------|------|-------------|
| 一连接一通道 | 每个外部连接对应一个数据通道 | ❌ 未采用 |
| 多连接复用通道 | 多个连接共享一个数据通道（按 conn_id 区分） | ✅ 采用（每客户端一条） |
| 动态扩缩容 | 根据负载动态创建/销毁通道 | ❌ 未采用 |

#### 3.2.5 配置扩展

```json
{
    "data_port": 7001
}
```

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| data_port | int | None | 数据通道服务端口；为 None 时不启用数据通道分离，DATA 走控制连接 |

实际仅有 `data_port` 一个配置项。会话复用是内置行为（每客户端一条数据连接），无需池大小、空闲超时、复用开关等参数。`data_port` 为 `None` 时退化为 P1 模式：DATA 帧直接在控制连接上传输，保证向后兼容。

---

## 4. 零拷贝优化

### 4.1 问题分析

当前数据转发流程：

```
外部用户数据 → 内核缓冲区 → 用户缓冲区 (asyncio read) → 用户缓冲区 (构造消息) → 内核缓冲区 (asyncio write) → 客户端

问题：
1. 每次 read/write 都需要用户态/内核态切换
2. 数据在内核和用户之间多次拷贝
3. Python 的 byte 转换进一步增加开销
```

### 4.2 设计方案

#### 4.2.1 零拷贝技术对比

| 技术 | 原理 | 适用场景 | 收益 |
|------|------|----------|------|
| sendfile | 直接从文件描述符到文件描述符，不经过用户态 | 文件传输 | ~60% CPU 节省 |
| splice | 管道机制，两个 socket 之间零拷贝 | 代理转发 | ~50% CPU 节省 |
| memoryview | Python 层面的零拷贝视图 | 数据处理 | 减少内存分配 |
| 缓冲区复用 | 预分配缓冲区循环使用 | 高并发 | 减少 GC 压力 |

#### 4.2.2 实际零拷贝优化

实际不存在 `zero_copy.py` 文件，也未使用 `splice` / `sendfile` 系统调用（Python 跨平台限制，且 splice 仅 Linux 可用）。零拷贝优化在 `protocol.py` 与服务端/客户端转发逻辑中以以下方式实现：

```python
# protocol.py —— DATA 编码：bytearray 预分配，避免多次拼接产生中间对象
payload_len = Protocol.UUID_SIZE + len(data_bytes)
buf = bytearray(Protocol.HEADER_SIZE + payload_len)  # 一次性分配整块
struct.pack_into(Protocol._HEADER_FMT, buf, 0,
                 Protocol.MAGIC, Protocol.VERSION, msg_code, 0, payload_len)
buf[Protocol.HEADER_SIZE:Protocol.HEADER_SIZE + Protocol.UUID_SIZE] = uuid_bytes
buf[Protocol.HEADER_SIZE + Protocol.UUID_SIZE:] = data_bytes
return buf
```

```python
# frps.py / frpc.py —— socket 优化与转发循环
READ_BUF_SIZE = 65536  # 64KB 读缓冲（减少系统调用次数）

def _optimize_socket(writer):
    """Set TCP_NODELAY and larger buffers on a connection's socket."""
    sock = writer.get_extra_info("socket")
    if sock:
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 256 * 1024)  # 256KB
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 256 * 1024)  # 256KB

# 转发循环（以服务端 forward_proxy_data 为例）：
while True:
    data = await asyncio.wait_for(
        conn_info["proxy_reader"].read(READ_BUF_SIZE),
        timeout=self.idle_timeout,
    )
    if not data:
        break
    data_msg = Message(MessageType.DATA, conn_id=conn_id, data=data)
    conn_info["client_writer"].write(Protocol.encode(data_msg))
    await conn_info["client_writer"].drain()
```

**关键优化点**：
- **bytearray 预分配**：DATA 编码时一次性分配 `header + uuid + data` 整块缓冲，用 `struct.pack_into` 和切片赋值填充，避免 `bytes + bytes + bytes` 产生中间对象，减少 GC 压力。
- **64KB 读缓冲**：转发循环以 64KB（`READ_BUF_SIZE`）为单位读取，相比默认 4KB 减少系统调用次数。
- **TCP_NODELAY**：禁用 Nagle 算法，降低小包延迟。
- **256KB socket 缓冲**：扩大 `SO_RCVBUF` / `SO_SNDBUF` 至 256KB，减少高带宽下的窗口压力。
- **DATA 帧零 hex**：原始字节直接进帧，不做 hex 编码，体积不膨胀。

#### 4.2.3 服务端数据转发

实际不存在 `DataChannelHandler` 类、`zero_copy_pipe` 函数和 `BufferPool` 类。服务端数据转发在 `frps.py` 中以 `handle_data_conn`（数据连接认证 + 接收）和 `forward_proxy_data`（外部用户 → 客户端转发）两个协程实现：

```python
# frps.py —— 数据连接处理（示意）
async def handle_data_conn(self, reader, writer):
    """处理专用数据连接：先 DATA_AUTH 认证，再循环接收 DATA 帧"""
    _optimize_socket(writer)
    # 1. 等待 DATA_AUTH(session_id)，校验后绑定 session.data_writer
    #    回复 DATA_AUTH_RESP(status=ok/error)
    # 2. 循环读取 DATA 帧，按 conn_id 路由到 conn_pool 中对应的代理连接
    while not self._stop_event.is_set():
        message, buffer = Protocol.decode(buffer)
        if message and message.type == MessageType.DATA:
            conn_id = message.payload["conn_id"]
            data = message.payload["data"]
            conn_info = self.conn_pool.get(conn_id)
            if conn_info:
                conn_info["proxy_writer"].write(data)  # 写给外部用户
                await conn_info["proxy_writer"].drain()

async def forward_proxy_data(self, conn_id):
    """外部用户数据 → 客户端：读取 proxy_reader，编码为 DATA 帧发回"""
    while True:
        data = await conn_info["proxy_reader"].read(READ_BUF_SIZE)
        if not data:
            break
        data_msg = Message(MessageType.DATA, conn_id=conn_id, data=data)
        conn_info["client_writer"].write(Protocol.encode(data_msg))
        await conn_info["client_writer"].drain()
```

两个方向各自独立运行：外部用户 → 服务端 → 客户端走 `forward_proxy_data`，客户端 → 服务端 → 外部用户走 `handle_data_conn` 中的 DATA 帧路由。无双向 `asyncio.gather` 转发管道。

#### 4.2.4 预期收益

| 优化项 | 基线（P1） | P2（零拷贝后） | 提升 |
|--------|-----------|---------------|------|
| CPU 占用（100Mbps） | ~40% | ~15% | -62.5% |
| 最大吞吐量 | ~150 Mbps | ~500 Mbps | 3.3x |
| 内存分配次数 | 高频 | 减少 80%+ | -80% |
| GC 频率 | 高 | 低 | -70% |

---

## 5. 多进程架构

### 5.1 问题分析

Python 的 GIL（全局解释器锁）限制了多线程在 CPU 密集型任务下的并行能力。当前单进程模型：

| 问题 | 影响 |
|------|------|
| 无法利用多核 | 4 核机器只能用 1 个核 |
| CPU 密集型瓶颈 | 协议编解码、加密等 CPU 操作受限 |
| 故障隔离差 | 单个连接崩溃可能影响整个进程 |
| 垂直扩展有限 | 只能通过加机器来扩展 |

### 5.2 设计方案

#### 5.2.1 多进程架构

```
┌─────────────────────────────────────────────────────────────────┐
│                        主进程 (Master)                           │
│                                                                 │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │ 监听端口（端口复用 SO_REUSEPORT）                        │   │
│  │ 控制端口 7000 / 数据端口 7001 / 代理端口 *              │   │
│  └──────────────────────┬──────────────────────────────────┘   │
│                         │                                      │
│          ┌──────────────┼──────────────┐                       │
│          │              │              │                       │
│          ▼              ▼              ▼                       │
│  ┌────────────┐ ┌────────────┐ ┌────────────┐                 │
│  │ Worker 1   │ │ Worker 2   │ │ Worker N   │                 │
│  │ (CPU 0)    │ │ (CPU 1)    │ │ (CPU N)    │                 │
│  │            │ │            │ │            │                 │
│  │ 处理连接   │ │ 处理连接   │ │ 处理连接   │                 │
│  │ 协议编解码 │ │ 协议编解码 │ │ 协议编解码 │                 │
│  │ 数据转发   │ │ 数据转发   │ │ 数据转发   │                 │
│  └────────────┘ └────────────┘ └────────────┘                 │
│                                                                 │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │ 进程间通信 (IPC)                                        │   │
│  │ - Unix Domain Socket                                    │   │
│  │ - 共享状态同步（代理注册、配置更新）                      │   │
│  └─────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────┘
```

#### 5.2.2 端口复用 (SO_REUSEPORT)

```python
import socket

def create_listening_socket(host: str, port: int, reuse_port: bool = True) -> socket.socket:
    """创建支持 SO_REUSEPORT 的监听 socket"""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    if reuse_port and hasattr(socket, "SO_REUSEPORT"):
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
    sock.bind((host, port))
    sock.listen(128)
    sock.setblocking(False)
    return sock
```

**SO_REUSEPORT 特性**：
- 多个进程可以绑定同一个端口
- 内核自动在进程之间分配连接
- 每个进程有独立的连接队列
- 连接分配大致均匀

#### 5.2.3 主从架构实现

```python
# multiprocess_server.py
import os
import signal
import multiprocessing
from typing import List, Dict
import asyncio

class MasterProcess:
    """主进程：管理 Worker 进程"""

    def __init__(self, config):
        self.config = config
        self.workers: List[multiprocessing.Process] = []
        self.worker_count = config.get("worker_count", multiprocessing.cpu_count())
        self.running = False

    def start(self):
        """启动主进程和所有 Worker"""
        self.running = True

        # 注册信号处理
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)
        signal.signal(signal.SIGCHLD, self._child_handler)

        # 启动 Worker 进程
        for i in range(self.worker_count):
            self._spawn_worker(i)

        # 主进程等待
        while self.running:
            signal.pause()

    def _spawn_worker(self, worker_id: int):
        """启动一个 Worker 进程"""
        p = multiprocessing.Process(
            target=worker_main,
            args=(worker_id, self.config),
            name=f"frps-worker-{worker_id}"
        )
        p.start()
        self.workers.append(p)

    def _signal_handler(self, signum, frame):
        """处理关闭信号"""
        self.running = False
        # 通知所有 Worker 优雅关闭
        for p in self.workers:
            if p.is_alive():
                p.terminate()

        # 等待所有 Worker 退出
        for p in self.workers:
            p.join(timeout=30)
            if p.is_alive():
                p.kill()

    def _child_handler(self, signum, frame):
        """处理子进程退出"""
        if not self.running:
            return

        # 重启意外退出的 Worker
        for i, p in enumerate(self.workers):
            if not p.is_alive():
                # Worker 意外退出，重新启动
                self.workers[i] = multiprocessing.Process(
                    target=worker_main,
                    args=(i, self.config),
                    name=f"frps-worker-{i}"
                )
                self.workers[i].start()
                self.logger.warning(f"Worker {i} crashed, restarted")


def worker_main(worker_id: int, config):
    """Worker 进程入口"""
    # 设置进程名
    try:
        import setproctitle
        setproctitle.setproctitle(f"frps-worker-{worker_id}")
    except ImportError:
        pass

    # 绑定到特定 CPU 核心（可选，Linux only）
    try:
        if hasattr(os, "sched_setaffinity"):
            cpu_id = worker_id % os.cpu_count()
            os.sched_setaffinity(0, {cpu_id})
    except (OSError, AttributeError):
        pass

    # 启动事件循环
    server = FRPServer(config, worker_id=worker_id)
    asyncio.run(server.start())


class FRPServer:
    """多进程版本的服务端"""

    def __init__(self, config, worker_id: int = 0):
        self.worker_id = worker_id
        # ... 其他初始化 ...

    async def start(self):
        # 使用 SO_REUSEPORT 创建监听 socket
        bind_addr = self.config.get("bind_addr", "0.0.0.0")
        bind_port = self.config.get("bind_port", 7000)

        # 创建支持端口复用的监听 socket
        sock = create_listening_socket(bind_addr, bind_port)

        self.control_server = await asyncio.start_server(
            self.handle_control_conn,
            sock=sock,
        )

        # ... 启动其他服务 ...

        self.logger.info(
            f"Worker {self.worker_id} started on {bind_addr}:{bind_port}"
        )

        async with self.control_server:
            await self.control_server.serve_forever()
```

#### 5.2.4 进程间状态同步

由于多个 Worker 进程各自独立，需要同步的状态：

| 状态 | 同步策略 | 说明 |
|------|----------|------|
| 代理注册信息 | 各自独立注册 | 每个 Worker 独立处理自己的连接 |
| Token 认证 | 各自独立校验 | 配置相同，无状态 |
| 监控统计 | 主进程汇总 | Worker 上报指标，主进程聚合 |
| 配置变更 | 信号通知 + 热加载 | SIGHUP 触发重新加载配置 |

```python
# 监控数据聚合
class MetricsAggregator:
    """主进程中的指标聚合器"""

    def __init__(self, worker_count: int):
        self.worker_count = worker_count
        self.worker_metrics: Dict[int, dict] = {}
        self._aggregated = {}

    def update_worker_metrics(self, worker_id: int, metrics: dict):
        """更新 Worker 指标"""
        self.worker_metrics[worker_id] = metrics
        self._aggregate()

    def _aggregate(self):
        """聚合所有 Worker 的指标"""
        if not self.worker_metrics:
            return

        all_metrics = list(self.worker_metrics.values())
        first = all_metrics[0]

        result = {}
        for key in first:
            if isinstance(first[key], dict):
                result[key] = self._aggregate_dict(
                    [m[key] for m in all_metrics]
                )
            elif isinstance(first[key], (int, float)):
                result[key] = sum(m[key] for m in all_metrics)
            else:
                result[key] = first[key]

        self._aggregated = result

    def get_aggregated(self) -> dict:
        return self._aggregated
```

#### 5.2.5 配置扩展

```json
{
    "worker_count": 4,
    "cpu_affinity": true,
    "auto_restart_worker": true,
    "graceful_restart": true
}
```

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| worker_count | int | CPU 核心数 | Worker 进程数量 |
| cpu_affinity | bool | false | 是否绑定 CPU 核心 |
| auto_restart_worker | bool | true | 是否自动重启崩溃的 Worker |
| graceful_restart | bool | true | 是否支持优雅重启（平滑升级） |

#### 5.2.6 预期收益

| 指标 | 单进程（P1） | 多进程（P2） | 提升（4核） |
|------|-------------|-------------|-------------|
| 最大并发连接 | ~1000 | ~5000 | 5x |
| 最大吞吐量 | ~150 Mbps | ~600 Mbps | 4x |
| 单进程故障影响 | 全局 | 仅该进程连接 | - |
| CPU 利用率 | ~90%（单核） | ~90%（多核） | 4x 计算能力 |

---

## 6. 综合性能优化

### 6.1 TCP 参数调优

```python
def optimize_socket(sock: socket.socket):
    """优化 socket 参数"""
    # 接收缓冲区
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4 * 1024 * 1024)  # 4MB
    # 发送缓冲区
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 4 * 1024 * 1024)  # 4MB
    # TCP_NODELAY (禁用 Nagle 算法)
    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    # TCP_QUICKACK (Linux)
    if hasattr(socket, "TCP_QUICKACK"):
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_QUICKACK, 1)
    # SO_KEEPALIVE
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
```

### 6.2 读/写批处理

```python
async def batched_read(reader: asyncio.StreamReader, target_size: int = 65536):
    """批量读取，减少系统调用"""
    buf = bytearray(target_size)
    total_read = 0

    while total_read < target_size:
        try:
            data = await reader.read(target_size - total_read)
        except Exception:
            break

        if not data:
            break

        buf[total_read:total_read + len(data)] = data
        total_read += len(data)

        # 如果数据已经满了，就返回
        if total_read >= target_size:
            break

    return bytes(buf[:total_read])
```

### 6.3 事件循环优化

```python
def configure_event_loop():
    """配置高性能事件循环"""
    import sys

    if sys.platform == "linux":
        # 使用 uvloop（如果可用）
        try:
            import uvloop
            asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())
            return True
        except ImportError:
            pass

    return False
```

### 6.4 基准测试结果（预期）

| 场景 | P1（基线） | P2（二进制+多进程+零拷贝） |
|------|-----------|---------------------------|
| TCP 转发吞吐量 | ~100 Mbps | ~1 Gbps |
| 并发连接数 | ~1000 | ~10000 |
| 心跳延迟 | ~5ms | ~2ms |
| CPU 使用率（同流量） | 高 | 降低 60-70% |
| 内存占用 | ~100MB | ~300MB（多进程） |

---

## 7. 实施计划

### 7.1 分阶段实施

| 阶段 | 内容 | 依赖 | 周期 |
|------|------|------|------|
| P2.1 | 二进制协议 | P1 | 1-2 周 |
| P2.2 | 数据通道分离 | P2.1 | 2-3 周 |
| P2.3 | 零拷贝优化 | P2.2 | 1-2 周 |
| P2.4 | 多进程架构 | P2.3 | 2-3 周 |
| P2.5 | 性能调优 + 测试 | P2.4 | 1 周 |

### 7.2 回滚策略

二进制协议与零拷贝优化为默认行为，无独立开关；数据通道分离通过 `data_port` 配置控制；多进程为规划中能力。

| 功能 | 控制方式 | 默认行为 | 回滚方式 |
|------|----------|----------|----------|
| 二进制协议 | 无开关（默认行为） | 始终启用 | 不可关闭，属内置协议格式 |
| 数据通道分离 | `data_port` 配置 | `None`（不启用） | 设为 `None` 则 DATA 走控制连接，退化为 P1 模式 |
| 零拷贝优化 | 无开关（默认行为） | 始终启用 | 不可关闭，属内置编码/缓冲优化 |
| 多进程 | `worker_count` | 1（未实现） | 未实现，当前为单进程 |

---

## 8. 测试验证

### 8.1 性能测试工具

```bash
# 1. 吞吐量测试
# 使用 iperf3 或自建工具测试转发带宽

# 2. 并发测试
# 使用 wrk/ab 测试 HTTP 代理的并发能力
wrk -t12 -c1000 -d30s http://服务端:8080/

# 3. 延迟测试
# 测量端到端延迟
ping -c 100 服务端IP
# 或使用 tcpdump 测量转发延迟

# 4. CPU/内存监控
top -p <pid>
```

### 8.2 压测用例

| 用例 | 说明 | 验证指标 |
|------|------|----------|
| 小文件高频请求 | 1KB 数据，1000 QPS | CPU 占用、延迟 |
| 大文件传输 | 100MB 文件，10 并发 | 吞吐量、内存 |
| 高并发短连接 | 10000 连接，每个 1KB | 连接数、处理速度 |
| 长连接保持 | 5000 连接，心跳模式 | 内存占用、稳定性 |
| 混合流量 | 大小混合，多种模式 | 综合性能 |

---

## 9. 完整演进路线图

```
P0 可用 (2-3天)
    断线重连、超时清理、Token认证、错误处理
    │
    ▼
P1 生产级 (1-2周)
    TLS加密、访问控制、监控统计、优雅关闭
    │
    ▼
P2 高性能 (6-11周)
    二进制协议、数据通道分离、零拷贝、多进程
    │
    ▼
P3 企业级（未来）
    集群化、热升级、插件系统、流量控制
```
