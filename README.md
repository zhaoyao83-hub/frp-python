# MyFRP

一个基于 Python asyncio 实现的轻量级反向代理工具，支持 TCP/UDP 端口转发、HTTP 虚拟主机代理、STCP 秘密 TCP 穿透。

## 功能特性

- 支持 TCP 端口转发
- 支持 UDP 端口转发
- **支持 HTTP 代理**（基于 Host 头部的虚拟主机路由，支持 custom_domains 和 subdomain）
- **支持 STCP（Secret TCP）**（密钥认证的点对点 TCP 穿透，无需公网端口）
- 基于 asyncio 的高性能异步网络编程
- 心跳机制保持连接存活
- **二进制协议**（8 字节头 + 二进制 UUID + 原始数据，DATA 消息体积减少 72%）
- **数据通道分离**（控制通道与数据通道独立，支持会话复用，向后兼容）
- **零拷贝优化**（bytearray 预分配、64KB 读缓冲、TCP_NODELAY、256KB socket 缓冲）
- 配置文件驱动
- Token 认证（LOGIN/LOGIN_RESP 握手，返回 session_id）
- TLS/SSL 加密控制通道与数据通道
- 客户端断线自动重连（指数退避）
- 空闲连接超时清理
- 访问控制（端口白名单、IP 黑/白名单）
- 流量统计与 HTTP Dashboard API（`/stats`）
- 优雅关闭（SIGINT/SIGTERM 信号处理）

## 架构概览

```
┌─────────────────────────────────────────────────────────────┐
│                      客户端 (FRPClient)                      │
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────┐  │
│  │ 本地服务 A   │    │ 本地服务 B   │    │ 本地服务 C   │  │
│  │ 127.0.0.1:80 │    │ 127.0.0.1:22 │    │ 127.0.0.1:53 │  │
│  └──────┬───────┘    └──────┬───────┘    └──────┬───────┘  │
│         │                   │                   │          │
│         └──────────┬────────┴───────────────────┘          │
│                    │                                        │
│              ┌─────┴─────┐                                  │
│              │ FRPClient │                                  │
│              │ 控制连接  │                                  │
│              └─────┬─────┘                                  │
│                    │                                        │
└────────────────────┼────────────────────────────────────────┘
                     │ TCP/UDP
                     ▼
┌─────────────────────────────────────────────────────────────┐
│                      服务端 (FRPServer)                      │
│                    ┌─────────────┐                          │
│                    │ FRPServer   │                          │
│                    │ 控制连接    │                          │
│                    └──────┬──────┘                          │
│                           │                                 │
│   ┌───────────┬───────────┼───────────┬───────────┐        │
│   ▼           ▼           ▼           ▼           ▼        │
│ ┌──────┐   ┌──────┐   ┌──────┐   ┌──────┐   ┌──────┐      │
│ │ 8080 │   │ 8081 │   │ 8082 │   │ 8083 │   │ 8084 │      │
│ │ TCP  │   │ TCP  │   │ UDP  │   │ TCP  │   │ UDP  │      │
│ └──┬───┘   └──┬───┘   └──┬───┘   └──┬───┘   └──┬───┘      │
│    │          │          │          │          │           │
│    └──────────┴──────────┴──────────┴──────────┘           │
│                    外部访问                                  │
└─────────────────────────────────────────────────────────────┘
```

## 快速开始

### 环境要求

- Python 3.8+

### 服务端部署

```bash
# 启动服务端
python frps.py -c config/frps.json
```

### 客户端配置

```bash
# 启动客户端
python frpc.py -c config/frpc.json
```

## 配置说明

### 服务端配置 (frps.json)

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| bind_addr | string | 0.0.0.0 | 绑定地址 |
| bind_port | int | 7000 | 控制连接端口 |
| vhost_http_port | int | None | HTTP 虚拟主机监听端口，None 表示不启用 HTTP 代理 |
| subdomain_host | string | None | 子域名根主机（如 `example.com`），配合 HTTP 代理的 subdomain 使用 |
| log_level | string | info | 日志级别 |
| log_file | string | None | 日志文件路径 |
| auth_token | string | None | Token 认证，None 表示不启用 |
| max_connections | int | 1000 | 最大并发控制连接数 |
| idle_timeout | int | 300 | 空闲连接超时（秒） |
| tls | bool | false | 是否启用 TLS 加密 |
| tls_cert_file | string | None | TLS 证书文件路径 |
| tls_key_file | string | None | TLS 私钥文件路径 |
| webapi_port | int | None | WebAPI HTTP 端口，None 表示不启用 |
| webapi_addr | string | 0.0.0.0 | WebAPI 监听地址 |
| data_port | int | None | 数据通道端口，None 表示复用控制通道 |
| allow_ports | string | None | 允许注册的端口范围，如 "8080,9000-9100" |
| ip_whitelist | string | None | IP 白名单（CIDR），如 "192.168.1.0/24,10.0.0.1" |
| ip_blacklist | string | None | IP 黑名单（CIDR） |

示例配置：

```json
{
    "bind_port": 7000,
    "bind_addr": "0.0.0.0",
    "auth_token": "my-secret-token-123",
    "max_connections": 1000,
    "idle_timeout": 300,
    "tls": true,
    "tls_cert_file": "server.crt",
    "tls_key_file": "server.key",
    "webapi_port": 7501,
    "webapi_addr": "0.0.0.0",
    "data_port": 7001,
    "allow_ports": "7000-9000",
    "ip_blacklist": null
}
```

### 客户端配置 (frpc.json)

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| server_addr | string | 127.0.0.1 | 服务端地址 |
| server_port | int | 7000 | 服务端端口 |
| log_level | string | info | 日志级别 |
| log_file | string | None | 日志文件路径 |
| auth_token | string | None | Token 认证，需与服务端一致 |
| reconnect | bool | true | 是否启用断线重连 |
| reconnect_max_retries | int | 0 | 最大重试次数，0 表示无限 |
| reconnect_base_delay | int | 1 | 初始重连延迟（秒） |
| reconnect_max_delay | int | 60 | 最大重连延迟（秒） |
| tls | bool | false | 是否启用 TLS |
| tls_insecure | bool | false | 是否跳过证书验证（仅调试） |
| tls_ca_file | string | None | CA 证书路径 |
| data_port | int | None | 服务端数据通道端口，None 表示复用控制通道 |
| proxies | array | [] | 代理配置列表 |

代理配置项（通用）：

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| name | string | 是 | 代理名称，需唯一 |
| type | string | 否 | 协议类型，tcp/udp/http/stcp/stcp_visitor，默认 tcp |
| local_ip | string | 否 | 本地服务地址，默认 127.0.0.1 |
| local_port | int | 是* | 本地服务端口（stcp_visitor 不需要） |
| remote_port | int | 是* | 服务端监听端口（http/stcp/stcp_visitor 不需要） |

**TCP/UDP 代理**（type = tcp / udp）：
- `remote_port`：服务端监听端口（必填）

**HTTP 代理**（type = http）：

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| custom_domains | array | 否* | 自定义域名列表，如 `["app.example.com"]` |
| subdomain | string | 否* | 子域名前缀，配合服务端 `subdomain_host` 使用 |

*custom_domains 和 subdomain 至少填一个

**STCP 代理（提供方）**（type = stcp）：

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| sk | string | 是 | 密钥，访问方需要提供相同密钥 |

**STCP 代理（访问方）**（type = stcp_visitor）：

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| sk | string | 是 | 密钥，需与提供方一致 |
| server_name | string | 是 | 提供方的代理名称 |
| bind_addr | string | 否 | 本地监听地址，默认 127.0.0.1 |
| bind_port | int | 是 | 本地监听端口 |

示例配置：

```json
{
    "server_addr": "127.0.0.1",
    "server_port": 7000,
    "auth_token": "my-secret-token-123",
    "reconnect": true,
    "reconnect_max_retries": 5,
    "reconnect_base_delay": 1,
    "reconnect_max_delay": 30,
    "tls": true,
    "tls_insecure": true,
    "data_port": 7001,
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

### HTTP 代理示例

**服务端**配置 `vhost_http_port` 和 `subdomain_host`：

```json
{
    "bind_port": 7000,
    "vhost_http_port": 8080,
    "subdomain_host": "example.com"
}
```

**客户端**配置 HTTP 类型代理：

```json
{
    "server_addr": "your-server-ip",
    "server_port": 7000,
    "proxies": [
        {
            "name": "my-web",
            "type": "http",
            "local_port": 8000,
            "custom_domains": ["app.example.com", "www.example.com"],
            "subdomain": "web"
        }
    ]
}
```

访问方式：
- 自定义域名：`http://app.example.com:8080` → 转发到本地 8000 端口
- 子域名：`http://web.example.com:8080` → 转发到本地 8000 端口

### STCP 示例

STCP（Secret TCP）用于点对点 TCP 穿透，服务端不需要开放公网端口，通过密钥认证。

**提供方**（运行本地服务的机器）：

```json
{
    "server_addr": "your-server-ip",
    "server_port": 7000,
    "proxies": [
        {
            "name": "my-ssh",
            "type": "stcp",
            "local_port": 22,
            "sk": "your-secret-key"
        }
    ]
}
```

**访问方**（要访问服务的机器）：

```json
{
    "server_addr": "your-server-ip",
    "server_port": 7000,
    "proxies": [
        {
            "name": "visit-ssh",
            "type": "stcp_visitor",
            "server_name": "my-ssh",
            "sk": "your-secret-key",
            "bind_addr": "127.0.0.1",
            "bind_port": 2222
        }
    ]
}
```

访问方式：在访问方机器上连接 `127.0.0.1:2222` → 通过服务端中继 → 连接到提供方的本地 22 端口。

## 文件结构

```
myfrp/
├── frps.py              # 服务端主程序
├── frpc.py              # 客户端主程序
├── manage.py            # Web 管理面板入口（FastAPI）
├── config.py            # 配置管理模块
├── protocol.py          # 消息协议模块
├── log.py               # 日志模块
├── stats.py             # 流量统计模块
├── access.py            # 访问控制模块（端口/IP 白名单）
├── server.crt           # TLS 证书（示例）
├── server.key           # TLS 私钥（示例）
├── config/              # 配置文件目录
│   ├── frps.json        # 服务端配置文件
│   ├── frpc.json        # 客户端配置文件
│   └── webadmin.json    # 管理面板配置（端口、用户、JWT 密钥）
├── webadmin/            # Web 管理面板
│   ├── api/             # 后端 API
│   │   ├── app.py       # FastAPI app 创建、路由注册、静态托管
│   │   ├── auth.py      # JWT 鉴权、密码哈希
│   │   ├── config_manager.py  # 配置读写、校验
│   │   ├── service_manager.py # 子进程管理（启停 frps/frpc）
│   │   ├── log_buffer.py      # 环形日志缓冲
│   │   ├── schemas.py   # Pydantic 模型
│   │   └── routes/      # API 路由
│   │       ├── auth.py  # /api/auth/*
│   │       ├── config.py # /api/config/*
│   │       ├── dashboard.py # /api/dashboard/*
│   │       ├── monitor.py # /api/monitor/* + /ws/*
│   │       ├── service.py # /api/service/*
│   │       └── proxies.py # /api/proxies/*
│   └── app/             # 前端应用（React + TypeScript + Vite）
│       ├── src/         # React 源码
│       └── dist/        # 构建产物
└── docs/
    ├── design.md                # 设计文档
    ├── connection_flow.md       # 连接流程详解
    ├── new_conn_detail.md       # NEW_CONN 阶段详解
    ├── dashboard_spec.md        # Dashboard 规范
    ├── extend_data_channel.md   # 数据通道扩展规划
    ├── p0_production_ready.md   # P0 可用性增强设计
    ├── p1_enterprise.md         # P1 生产级增强设计
    ├── p2_high_performance.md   # P2 高性能增强设计
    ├── contest_intro.md         # 大赛介绍
    └── tech_article.md          # 技术文章
```

## 协议说明

### 消息格式

采用二进制帧格式（8 字节头 + payload）：

```
┌────────┬─────────┬──────┬───────┬──────────────┬───────────────┐
│ Magic  │ Version │ Type │ Flags │ Payload Len  │ Payload       │
│ 1 byte │ 1 byte  │1 byte│1 byte │ 4 bytes (BE) │ variable      │
│ 0xAA   │  0x01   │      │       │              │               │
└────────┴─────────┴──────┴───────┴──────────────┴───────────────┘
```

- **DATA 消息**：payload = 16 字节二进制 UUID + 原始数据字节（零拷贝，无 hex 编码）
- **控制消息**：payload = JSON UTF-8（小体积、低频）

### 消息类型

| 类型 | 代码 | 说明 |
|------|------|------|
| login | 0x01 | 客户端发送 Token 认证请求 |
| login_resp | 0x02 | 服务端返回认证结果（含 session_id、data_port） |
| register | 0x03 | 客户端注册代理 |
| new_conn | 0x04 | 服务端通知客户端有新连接 |
| init_conn | 0x05 | 客户端通知服务端本地连接已就绪 |
| ping | 0x06 | 心跳请求 |
| pong | 0x07 | 心跳响应 |
| error | 0x08 | 错误信息 |
| close | 0x09 | 关闭连接 |
| data | 0x0A | 数据传输（二进制 UUID + 原始字节） |
| data_auth | 0x0B | 数据通道认证（携带 session_id） |
| data_auth_resp | 0x0C | 数据通道认证响应 |
| http_new_conn | 0x0D | HTTP 代理新连接 |
| http_resp_req | 0x0E | HTTP 响应请求（预留） |
| stcp_register | 0x0F | STCP 注册（预留） |
| stcp_visitor_register | 0x10 | STCP 访问方注册（预留） |
| stcp_visitor_register_resp | 0x11 | STCP 访问方注册响应（预留） |
| stcp_new_visitor | 0x12 | STCP 新访问方连接通知 |
| stcp_visitor_ready | 0x13 | STCP 访问方就绪通知 |

### 数据通道分离

配置 `data_port` 后，客户端在登录后建立独立的数据连接：

1. 客户端通过控制端口登录，获得 `session_id` 和 `data_port`
2. 客户端连接数据端口，发送 `DATA_AUTH`（携带 `session_id`）
3. 服务端校验 session，回复 `DATA_AUTH_RESP`
4. 后续 DATA/CLOSE 消息通过数据通道传输，控制通道仅处理控制消息

未配置 `data_port` 时，自动回退到控制通道复用模式（向后兼容）。

## 使用场景

- 内网穿透，访问内网服务
- 远程管理内网设备
- 开发测试环境的端口转发
- 将本地服务暴露到公网

## Dashboard

服务端配置 `webapi_port` 后，可通过 HTTP 访问运行指标：

```bash
curl http://服务端IP:7501/stats
```

返回 JSON 格式的统计信息，包括 uptime、总连接数、当前连接数、各代理的流量明细等。

### 独立 Web 管理面板

项目还包含一套完整的独立 Web 管理面板（`manage.py`），基于 FastAPI + React 实现：

- **Web UI**：仪表盘、配置管理、服务启停、连接/日志监控、用户管理、**端口映射管理**
- **鉴权**：JWT + bcrypt 密码哈希
- **实时监控**：WebSocket 推送日志和统计数据
- **服务管理**：通过子进程启停 frps/frpc
- **代理类型支持**：TCP / UDP / HTTP 虚拟主机 / STCP 提供方 / STCP 访问方

启动方式：
```bash
python manage.py -c config/webadmin.json
```

默认监听 `0.0.0.0:7500`，首次启动自动生成 `config/webadmin.json` 配置文件和默认 admin 用户。

**Web 面板管理 P3 功能：**

| 功能 | 操作路径 | 说明 |
|------|----------|------|
| HTTP 代理服务端配置 | 配置管理 → 服务端配置 | 设置 `vhost_http_port` 和 `subdomain_host` |
| HTTP 代理客户端配置 | 端口映射 → 新增 → 类型选「HTTP 虚拟主机」 | 填写自定义域名或子域名前缀 |
| STCP 提供方配置 | 端口映射 → 新增 → 类型选「STCP 秘密 TCP（提供方）」 | 填写本地端口和共享密钥 |
| STCP 访问方配置 | 端口映射 → 新增 → 类型选「STCP 秘密 TCP（访问方）」 | 填写密钥、提供方名称和本地监听端口 |

## 许可证

MIT License