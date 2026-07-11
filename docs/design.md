# MyFRP 设计文档

## 1. 概述

### 1.1 项目背景

MyFRP 是一个基于 Python asyncio 实现的轻量级反向代理工具，用于实现内网穿透和端口转发功能。项目参考了 FRP (Fast Reverse Proxy) 的设计理念，但采用纯 Python 异步编程实现，具有简单易用、易于扩展的特点。

### 1.2 设计目标

- **轻量级**: 代码简洁，依赖少，易于部署和维护
- **高性能**: 基于 asyncio 异步 IO，支持高并发连接
- **协议支持**: 支持 TCP 和 UDP 两种传输协议
- **可靠性**: 内置心跳机制，保证连接稳定性
- **可配置**: 通过 JSON 配置文件灵活配置代理规则

### 1.3 适用场景

- 内网服务暴露到公网
- 远程访问内网设备
- 开发测试环境的端口转发
- 跨网络的服务访问

---

## 2. 架构设计

### 2.1 整体架构

```
┌─────────────────────────────────────────────────────────────────┐
│                         客户端 (Client)                          │
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────┐      │
│  │ 本地服务 A   │    │ 本地服务 B   │    │ 本地服务 C   │      │
│  │ (TCP/UDP)    │    │ (TCP/UDP)    │    │ (TCP/UDP)    │      │
│  └──────┬───────┘    └──────┬───────┘    └──────┬───────┘      │
│         │                   │                   │               │
│         └──────────┬────────┴───────────────────┘               │
│                    │                                            │
│              ┌─────┴─────┐                                      │
│              │ FRPClient │                                      │
│              │           │                                      │
│              │  - 控制连接  │                                      │
│              │  - 代理管理  │                                      │
│              │  - 数据转发  │                                      │
│              │  - 心跳机制  │                                      │
│              └─────┬─────┘                                      │
│                    │ 控制通道 (TCP)                              │
│                    │ 数据通道 (TCP, 可选)                        │
└────────────────────┼─────────────────────────────────────────────┘
                     │
                     ▼
┌─────────────────────────────────────────────────────────────────┐
│                         服务端 (Server)                          │
│                    ┌─────────────┐                               │
│                    │ FRPServer   │                               │
│                    │             │                               │
│                    │  - 控制服务  │                               │
│                    │  - 代理服务  │                               │
│                    │  - 连接管理  │                               │
│                    └──────┬──────┘                               │
│                           │                                      │
│   ┌───────────┬───────────┼───────────┬───────────┐             │
│   ▼           ▼           ▼           ▼           ▼             │
│ ┌──────┐   ┌──────┐   ┌──────┐   ┌──────┐   ┌──────┐           │
│ │ TCP  │   │ TCP  │   │ UDP  │   │ TCP  │   │ UDP  │           │
│ │ Proxy│   │ Proxy│   │ Proxy│   │ Proxy│   │ Proxy│           │
│ └──┬───┘   └──┬───┘   └──┬───┘   └──┬───┘   └──┬───┘           │
│    │          │          │          │          │                 │
│    └──────────┴──────────┴──────────┴──────────┘                 │
│                    外部访问入口                                   │
└─────────────────────────────────────────────────────────────────┘
```

### 2.2 架构特点

| 特点 | 说明 |
|------|------|
| 主从架构 | 服务端作为主控节点，客户端作为代理节点 |
| 控制通道分离 | 控制连接与数据连接分离，便于管理和扩展 |
| 异步处理 | 基于 asyncio，单线程处理大量并发连接 |
| 事件驱动 | 通过消息驱动的方式处理连接和数据转发 |

---

## 3. 模块设计

### 3.1 模块划分

| 模块 | 文件 | 职责 |
|------|------|------|
| 客户端 | frpc.py | 客户端主程序，负责连接服务端、Token 认证、注册代理、转发数据、断线重连 |
| 服务端 | frps.py | 服务端主程序，负责监听连接、认证、管理代理、转发数据、Dashboard、优雅关闭 |
| 配置管理 | config.py | 配置加载和管理 |
| 协议处理 | protocol.py | 消息协议的编码和解码 |
| 日志模块 | log.py | 日志记录和管理 |
| 流量统计 | stats.py | 连接数、流量等运行指标采集（线程安全） |
| 访问控制 | access.py | 端口白名单、IP 黑/白名单（基于 ipaddress 模块） |

### 3.2 客户端模块设计 (frpc.py)

#### 3.2.1 类结构

| 类 | 职责 | 关键方法 |
|----|------|----------|
| FRPClient | 客户端核心类 | start, _login, register_proxies, _connect_data_channel, handle_server_messages, handle_data_messages, send_heartbeat |
| ClientState | 客户端状态管理 | - |
| UDPLocalProtocol | UDP 本地协议处理 | datagram_received |

#### 3.2.2 核心流程

```
客户端启动
    │
    ├── 1. 读取配置文件
    │
    ├── 2. 建立控制连接 (TCP)
    │
    ├── 3. 登录 (LOGIN 消息，携带 token)
    │      └── 收到 LOGIN_RESP，保存 session_id 与 data_port
    │
    ├── 4. 注册代理 (REGISTER 消息)
    │
    ├── 5. 建立数据通道 (DATA_AUTH，携带 session_id)
    │      └── 收到 DATA_AUTH_RESP 后启动数据通道消息处理任务
    │
    ├── 6. 启动心跳任务
    │
    └── 7. 监听服务端消息
            │
            ├── NEW_CONN → 建立本地连接，启动数据转发
            │
            ├── DATA → 写入本地服务（数据通道活跃时由数据通道处理，控制通道跳过）
            │
            ├── CLOSE → 关闭连接（数据通道活跃时由数据通道处理，控制通道跳过）
            │
            ├── PONG → 心跳响应（忽略）
            │
            └── ERROR → 记录错误日志
```

> `_login` 方法发送 LOGIN 并等待 LOGIN_RESP，成功后保存 `session_id` 与服务端通告的 `data_port`；`_connect_data_channel` 方法建立独立数据连接并发送 DATA_AUTH 完成会话认证；`handle_data_messages` 方法处理数据通道上的 DATA/CLOSE 消息。数据通道活跃时，控制通道的 `process_message` 会跳过 DATA/CLOSE，确保每条连接仅由一个读取者处理。

#### 3.2.3 状态管理

```python
class ClientState:
    def __init__(self):
        self.proxies = {}      # 代理配置映射: {proxy_name: proxy_info}
        self.conn_pool = {}    # 连接池: {conn_id: conn_info}
```

### 3.3 服务端模块设计 (frps.py)

#### 3.3.1 类结构

| 类 | 职责 | 关键方法 |
|----|------|----------|
| FRPServer | 服务端核心类 | start, handle_control_conn, handle_login, handle_register, handle_data_conn, handle_webapi, idle_cleanup_loop, _cleanup_all |
| UDPProxyProtocol | UDP 代理协议处理 | datagram_received |

> 注：服务端不再使用 `ServerState` 类，代理与连接池直接作为 `FRPServer` 的实例属性 `self.proxies`、`self.conn_pool` 管理；客户端统计由 `Stats` 类负责，访问控制由 `AccessControl` 类负责。

#### 3.3.2 核心流程

```
服务端启动
    │
    ├── 1. 读取配置文件
    │
    ├── 2. 初始化 SSL 上下文（若 tls=true）
    │
    ├── 3. 初始化访问控制（端口/IP 白名单）
    │
    ├── 4. 启动控制服务 (监听 bind_port，可选 TLS)
    │
    ├── 5. 启动 WebAPI HTTP 服务（若配置 webapi_port）
    │
    ├── 6. 启动数据通道服务（若配置 data_port，可选 TLS）
    │
    ├── 7. 启动空闲连接清理任务（每 60 秒）
    │
    ├── 8. 注册 SIGINT/SIGTERM 信号处理
    │
    └── 9. 监听控制连接
            │
            ├── LOGIN → 校验 Token，返回 LOGIN_RESP（含 session_id、data_port）
            │
            ├── REGISTER → 端口白名单校验，启动代理服务
            │
            ├── INIT_CONN → 设置 client_writer（优先数据通道），触发 client_ready
            │
            ├── PING → 返回 PONG 响应
            │
            ├── DATA → 转发到代理客户端（数据通道活跃时由数据通道处理）
            │
            └── CLOSE → 关闭连接（数据通道活跃时由数据通道处理）
```

> 数据通道连接由 `handle_data_conn` 方法处理：先等待客户端发送 `DATA_AUTH`（携带 `session_id`），校验通过后将该连接绑定到对应会话的 `data_writer`，随后仅处理 `DATA`/`CLOSE` 消息。

#### 3.3.3 状态管理

服务端不使用独立的状态类，状态直接作为 `FRPServer` 实例属性：

```python
class FRPServer:
    def __init__(self, config):
        self.proxies = {}        # 代理配置映射: {proxy_name: proxy_info}
        self.conn_pool = {}      # 连接池: {conn_id: conn_info}
        self.total_connections = 0
        self.stats = Stats()     # 流量统计
        self.access = AccessControl(self.logger)
        self.sessions = {}       # 会话映射: {session_id: {control_writer, data_writer, addr, proxies}}
        # ...
```

### 3.4 配置管理模块设计 (config.py)

#### 3.4.1 类结构

| 类 | 职责 | 关键方法 |
|----|------|----------|
| Config | 配置管理类 | load_from_file, get |

#### 3.4.2 默认配置

```python
defaults = {
    "server": {
        "bind_port": 7000,
        "bind_addr": "0.0.0.0",
        "log_level": "info",
        "log_file": None,
        "auth_token": None,
        "max_connections": 1000,
        "idle_timeout": 300,
        "tls": False,
        "tls_cert_file": None,
        "tls_key_file": None,
        "webapi_port": None,
        "webapi_addr": "0.0.0.0",
        "allow_ports": None,
        "ip_whitelist": None,
        "ip_blacklist": None,
        "data_port": None,
    },
    "client": {
        "server_addr": "127.0.0.1",
        "server_port": 7000,
        "log_level": "info",
        "log_file": None,
        "auth_token": None,
        "reconnect": True,
        "reconnect_max_retries": 0,
        "reconnect_base_delay": 1,
        "reconnect_max_delay": 60,
        "tls": False,
        "tls_insecure": False,
        "tls_ca_file": None,
        "data_port": None,
        "proxies": [],
    },
}
```

#### 3.4.3 配置加载流程

```
创建 Config 对象
    │
    ├── 初始化默认配置
    │
    └── load_from_file(filepath)
            │
            ├── 检查文件是否存在
            │
            ├── 读取 JSON 文件
            │
            └── 更新配置字典
```

### 3.5 协议模块设计 (protocol.py)

#### 3.5.1 消息类型定义

```python
class MessageType:
    LOGIN = "login"                # Token 认证请求
    LOGIN_RESP = "login_resp"      # 认证响应
    REGISTER = "register"          # 注册代理
    NEW_CONN = "new_conn"          # 新连接通知
    INIT_CONN = "init_conn"        # 客户端本地连接就绪通知
    PING = "ping"                  # 心跳请求
    PONG = "pong"                  # 心跳响应
    ERROR = "error"                # 错误信息
    CLOSE = "close"                # 关闭连接
    DATA = "data"                  # 数据传输
    DATA_AUTH = "data_auth"        # 数据通道会话认证请求
    DATA_AUTH_RESP = "data_auth_resp"  # 数据通道会话认证响应
```

> 共 12 种消息类型，在二进制帧头部中以 1 字节类型码标识（0x01~0x0C）。`DATA_AUTH`/`DATA_AUTH_RESP` 用于数据通道建立后的会话认证：客户端建立数据通道连接后须发送 `DATA_AUTH`（携带 `session_id`），服务端校验通过后才转发数据。

#### 3.5.2 消息格式

```
┌────────┬─────────┬──────┬───────┬──────────────┬───────────────┐
│ Magic  │ Version │ Type │ Flags │ Payload Len  │ Payload       │
│ 1 byte │ 1 byte  │1 byte│1 byte │ 4 bytes (BE) │ variable      │
└────────┴─────────┴──────┴───────┴──────────────┴───────────────┘
|<────────────── 8 字节二进制头部 (struct ">BBBBI") ─────────────>|
```

| 字段 | 长度 | 说明 |
|------|------|------|
| Magic | 1 byte | 魔数 `0xAA`，用于帧同步 |
| Version | 1 byte | 协议版本，当前为 `0x01` |
| Type | 1 byte | 消息类型码（0x01~0x0C，见 3.5.1） |
| Flags | 1 byte | 标志位，当前保留为 0 |
| Payload Len | 4 bytes | Big-endian uint32，payload 字节数 |
| Payload | 变长 | 负载内容，编码方式随消息类型而异 |

Payload 编码规则：
- **DATA** 消息：二进制负载 = 16 字节 UUID（conn_id）+ 原始 data 字节，零拷贝无 hex 转换
- **其余消息**：JSON UTF-8 字符串 `{"type": "...", ...}`（控制消息体积小、频率低）

#### 3.5.3 编码解码流程

```
编码 (encode):
    Message 对象
        │
        ├── 查表 _TYPE_TO_CODE 得到 1 字节类型码
        │
        ├── DATA 消息：二进制 payload
        │   ├── uuid.UUID(conn_id).bytes → 16 字节
        │   └── + raw data bytes（零拷贝预分配单缓冲区）
        │
        ├── 其余消息：JSON payload
        │   ├── to_dict() → {"type": "...", **payload}
        │   └── json.dumps().encode("utf-8") → bytes
        │
        ├── struct.pack(">BBBBI", MAGIC, VERSION, type_code, 0, len) → 8字节头部
        │
        └── 返回 header + payload

解码 (decode):
    原始 bytes
        │
        ├── 检查长度 >= 8（HEADER_SIZE）
        │
        ├── struct.unpack(">BBBBI", header) → magic, version, type_code, flags, length
        │
        ├── 校验 magic == 0xAA，不匹配则丢弃 1 字节重同步
        │
        ├── 检查长度 >= 8 + length
        │
        ├── 提取 payload bytes
        │
        ├── DATA 消息：解析 16 字节 UUID + 剩余 raw bytes
        │
        └── 其余消息：json.loads(payload) → Message.from_dict()
```

#### 3.5.4 各消息类型的 payload 结构

| 消息类型 | payload 字段 |
|----------|-------------|
| LOGIN | token |
| LOGIN_RESP | status ("ok"/"error"), session_id, data_port (成功时), message (错误时) |
| REGISTER | proxy_name, proxy_type, local_port, remote_port, local_ip |
| NEW_CONN | proxy_name, conn_id |
| INIT_CONN | conn_id |
| PING | 无 |
| PONG | 无 |
| DATA | conn_id, data (binary: 16-byte UUID + raw bytes) |
| CLOSE | conn_id |
| ERROR | message |
| DATA_AUTH | session_id |
| DATA_AUTH_RESP | status ("ok"/"error"), message (错误时) |

### 3.6 日志模块设计 (log.py)

#### 3.6.1 功能特点

- 支持控制台和文件双输出
- 可配置日志级别
- 标准日志格式

#### 3.6.2 日志格式

```
%(asctime)s - %(name)s - %(levelname)s - %(message)s
```

---

## 4. 数据流程设计

### 4.1 TCP 代理数据流程

```
外部客户端 ──► 服务端代理端口 ──► 服务端控制通道 ──► 客户端控制通道 ──► 本地服务
     │              │                    │                    │              │
     │   1. 建立连接 │                    │                    │              │
     │              │   2. NEW_CONN      │                    │              │
     │              │   消息             │                    │              │
     │              │───────────────────►│                    │              │
     │              │                    │   3. 建立本地连接   │              │
     │              │                    │───────────────────►│              │
     │              │                    │                    │   4. 连接成功 │
     │   5. 数据传输 │                    │   6. DATA 消息      │              │
     │◄─────────────│◄───────────────────│◄───────────────────│◄─────────────│
     │              │                    │                    │              │
```

### 4.2 UDP 代理数据流程

```
外部客户端 ──► 服务端代理端口 ──► 服务端控制通道 ──► 客户端控制通道 ──► 本地服务
     │              │                    │                    │              │
     │   1. 发送数据 │                    │                    │              │
     │              │   2. NEW_CONN +    │                    │              │
     │              │   DATA 消息        │                    │              │
     │              │───────────────────►│                    │              │
     │              │                    │   3. 发送到本地    │              │
     │              │                    │───────────────────►│              │
     │              │                    │                    │   4. 响应数据 │
     │              │   6. DATA 消息      │   5. DATA 消息     │              │
     │◄─────────────│◄───────────────────│◄───────────────────│◄─────────────│
```

### 4.3 心跳机制流程

```
客户端 ──────────────────────► 服务端
  │                              │
  │   PING 消息 (每30秒)          │
  │─────────────────────────────►│
  │                              │
  │                              │   PONG 消息
  │◄─────────────────────────────│
  │                              │
```

---

## 5. 接口设计

### 5.1 命令行接口

#### 5.1.1 服务端启动

```bash
python frps.py -c <config_file>
```

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| -c, --config | string | config/frps.json | 配置文件路径 |

#### 5.1.2 客户端启动

```bash
python frpc.py -c <config_file>
```

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| -c, --config | string | config/frpc.json | 配置文件路径 |

### 5.2 配置文件接口

#### 5.2.1 服务端配置 (frps.json)

```json
{
    "bind_addr": "0.0.0.0",
    "bind_port": 7000,
    "log_level": "info",
    "log_file": "frps.log"
}
```

#### 5.2.2 客户端配置 (frpc.json)

```json
{
    "server_addr": "127.0.0.1",
    "server_port": 7000,
    "log_level": "info",
    "log_file": "frpc.log",
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

---

## 6. 异常处理设计

### 6.1 异常分类

| 异常类型 | 处理策略 | 影响范围 |
|----------|----------|----------|
| 网络连接异常 | 记录日志，关闭连接，尝试重连 | 单个连接 |
| 配置错误 | 记录错误，使用默认值或退出 | 全局 |
| 协议解析异常 | 跳过错误消息，继续处理后续消息 | 单个消息 |
| 端口占用 | 记录错误，跳过该代理 | 单个代理 |

### 6.2 关键异常处理点

#### 6.2.1 客户端异常处理

```
┌─────────────────────────────────────────────────────┐
│  FRPClient.start()                                  │
│  ├── 连接失败 → 记录错误，按指数退避重连             │
│  ├── 认证失败 → 抛出 PermissionError，触发重连       │
│  ├── 注册失败 → 记录错误，继续                       │
│  ├── 心跳失败 → 记录错误，断开连接并重连             │
│  └── 数据转发失败 → 关闭连接，记录错误               │
└─────────────────────────────────────────────────────┘
```

#### 6.2.2 服务端异常处理

```
┌─────────────────────────────────────────────────────┐
│  FRPServer.handle_control_conn()                    │
│  ├── IP 黑名单 → 拒绝连接                           │
│  ├── 连接数超限 → 返回 ERROR，关闭连接              │
│  ├── 认证失败 → 返回 LOGIN_RESP(error)，关闭连接    │
│  ├── 连接断开 → 清理客户端代理，关闭连接             │
│  ├── 注册失败 → 返回 ERROR 消息                     │
│  ├── 端口占用/端口不允许 → 返回 ERROR 消息          │
│  ├── 数据转发失败 → 关闭连接，记录错误               │
│  └── 空闲超时 → 清理连接                            │
└─────────────────────────────────────────────────────┘
```

---

## 7. 扩展性设计

### 7.1 新增代理类型

1. 在 `MessageType` 中添加新类型
2. 在客户端 `handle_new_conn` 中添加新协议处理逻辑
3. 在服务端 `handle_register` 中添加新协议的代理服务启动逻辑
4. 创建对应的 Protocol 类处理数据收发

### 7.2 新增消息类型

1. 在 `MessageType` 中定义新类型
2. 在客户端 `process_message` 中添加处理逻辑
3. 在服务端 `process_message` 中添加处理逻辑

### 7.3 配置扩展

在 `Config.defaults` 中添加新的配置项，并在对应的模块中使用。

---

## 8. 安全性考虑

### 8.1 已实现的安全机制

- **Token 认证**：客户端连接后须先发送 LOGIN 消息携带 token，服务端校验通过返回 LOGIN_RESP 后才允许注册代理
- **TLS/SSL 加密**：控制通道支持 TLS 加密（服务端配置 `tls=true` + 证书，客户端配置 `tls=true`）
- **IP 黑/白名单**：服务端通过 `ip_whitelist` / `ip_blacklist`（CIDR）对控制连接与代理端口访问进行过滤
- **端口白名单**：服务端通过 `allow_ports` 限制客户端可注册的 `remote_port` 范围
- **连接数限制**：`max_connections` 限制最大并发控制连接
- **空闲超时清理**：`idle_timeout` 自动清理空闲连接，防止资源泄漏
- **数据通道会话认证**：客户端建立数据通道后须发送 DATA_AUTH（携带 `session_id`），服务端校验通过后才转发数据，防止未授权连接窃用数据通道

### 8.2 后续可增强方向

- 连接速率限制（防止单 IP 大量连接）
- 基于用户的细粒度授权
- 证书双向认证（mTLS）

---

## 9. 性能考虑

### 9.1 性能特点

- 基于 asyncio 异步 IO，单线程高并发
- 无阻塞的数据转发
- 内存占用低（仅维护必要的连接状态）
- 二进制协议帧：DATA 消息采用二进制负载（16 字节 UUID + raw bytes），避免 JSON/hex 序列化开销
- 控制通道与数据通道分离：大数据走独立数据通道，避免阻塞控制消息
- 零拷贝转发：DATA 编码预分配单缓冲区，配合 TCP_NODELAY 与 64KB 读缓冲、256KB 收发 socket 缓冲调优

### 9.2 性能优化建议

- 添加限流机制

---

## 10. 部署说明

### 10.1 服务端部署

```bash
# 安装 Python 3.8+
# 上传代码到服务器
# 修改配置文件 config/frps.json
# 启动服务
nohup python frps.py -c config/frps.json &
```

### 10.2 客户端部署

```bash
# 安装 Python 3.8+
# 修改配置文件 config/frpc.json
# 启动客户端
python frpc.py -c config/frpc.json
```

### 10.3 防火墙配置

服务端需要开放以下端口：
- 控制端口（默认 7000）
- 所有代理端口（根据配置）

---

## 11. 版本历史

| 版本 | 日期 | 变更说明 |
|------|------|----------|
| v1.0.0 | 2026-07-10 | 初始版本，支持 TCP/UDP 代理 |
| v1.1.0 | 2026-07-10 | P0：Token 认证、断线重连、空闲超时清理、错误处理 |
| v1.2.0 | 2026-07-10 | P1：TLS 加密、访问控制、流量统计 Dashboard、优雅关闭 |
| v1.3.0 | 2026-07-11 | P2：二进制协议帧、数据通道分离与会话认证、零拷贝转发与 socket 调优 |