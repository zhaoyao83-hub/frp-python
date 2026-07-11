"""Pydantic 请求/响应模型定义。"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field


# ---------------- 鉴权相关 ----------------


class LoginRequest(BaseModel):
    """登录请求。"""

    username: str = Field(..., description="用户名")
    password: str = Field(..., description="密码")


class LoginResponse(BaseModel):
    """登录响应。"""

    access_token: str
    token_type: str = "bearer"
    expires_in: int = Field(..., description="token 有效期（秒）")
    must_change_password: Optional[bool] = Field(
        None, description="是否需要修改密码（首次登录）"
    )


class RefreshTokenResponse(BaseModel):
    """刷新 token 响应。"""

    access_token: str
    expires_in: int


class UserInfoResponse(BaseModel):
    """当前用户信息。"""

    username: str
    role: str
    must_change_password: bool = False


class UserListItem(BaseModel):
    """用户列表项（不含密码哈希）。"""

    username: str
    role: str
    must_change_password: bool = False


class CreateUserRequest(BaseModel):
    """创建用户请求。"""

    username: str = Field(..., min_length=1, description="用户名")
    password: str = Field(..., min_length=8, description="密码（至少 8 位）")
    role: str = Field("viewer", description="角色：admin/viewer")


class ChangePasswordRequest(BaseModel):
    """修改密码请求。"""

    old_password: str = Field(..., description="旧密码")
    new_password: str = Field(..., min_length=8, description="新密码（至少 8 位）")


class MessageResponse(BaseModel):
    """通用消息响应。"""

    message: str


# ---------------- 仪表盘相关 ----------------


class ServiceStatus(BaseModel):
    """服务运行状态。"""

    name: str
    running: bool
    pid: Optional[int] = None
    uptime: int = 0
    restart_count: int = 0
    exit_code: Optional[int] = None
    has_external_process: bool = False
    external_pids: List[int] = []


class OverviewResponse(BaseModel):
    """仪表盘总览响应。"""

    frps_status: ServiceStatus
    frpc_status: ServiceStatus
    uptime: int = 0
    total_proxies: int = 0
    current_connections: int = 0
    total_bytes_in: int = 0
    total_bytes_out: int = 0
    stats_available: bool = False


class ProxyItem(BaseModel):
    """代理项。"""

    name: str
    type: str = ""
    remote_port: int = 0
    status: str = "online"
    current_conns: int = 0
    total_conns: int = 0
    bytes_in: int = 0
    bytes_out: int = 0
    created_at: int = 0


class ConnectionItem(BaseModel):
    """连接项。"""

    conn_id: str
    proxy_name: str
    bytes_in: int = 0
    bytes_out: int = 0
    created_at: int = 0


# ---------------- 配置管理相关 ----------------


class ConfigResponse(BaseModel):
    """配置读取响应。"""

    config: Dict[str, Any]
    schema_: List[Dict[str, Any]] = Field(alias="schema")


class ConfigSaveResponse(BaseModel):
    """配置保存响应。"""

    message: str
    need_restart: bool = True


class ConfigValidateResponse(BaseModel):
    """配置校验响应。"""

    valid: bool
    errors: List[str] = []


class ConfigRawResponse(BaseModel):
    """原始文本配置响应。"""

    content: str


class ConfigRawSaveRequest(BaseModel):
    """原始文本配置保存请求。"""

    content: str


# ---------------- 端口映射（代理）相关 ----------------


class ProxyConfig(BaseModel):
    """代理配置项。"""

    name: str = Field(..., description="代理名称（唯一）")
    type: str = Field("tcp", description="代理类型：tcp/udp/http/stcp/stcp_visitor")
    local_ip: str = Field("127.0.0.1", description="本地 IP")
    local_port: Optional[int] = Field(None, ge=1, le=65535, description="本地端口（stcp_visitor 不需要）")
    remote_port: Optional[int] = Field(None, ge=1, le=65535, description="远程端口（http/stcp/stcp_visitor 不需要）")
    enabled: bool = Field(True, description="是否启用")
    # HTTP 代理相关
    custom_domains: Optional[List[str]] = Field(None, description="自定义域名（http 用）")
    subdomain: Optional[str] = Field(None, description="子域名（http 用）")
    # STCP 相关
    sk: Optional[str] = Field(None, description="密钥（stcp/stcp_visitor 用）")
    server_name: Optional[str] = Field(None, description="提供方代理名称（stcp_visitor 用）")
    bind_addr: Optional[str] = Field(None, description="本地监听地址（stcp_visitor 用）")
    bind_port: Optional[int] = Field(None, ge=1, le=65535, description="本地监听端口（stcp_visitor 用）")
    # 插件（预留）
    plugin: Optional[str] = Field(None, description="插件名称")
    plugin_params: Optional[Dict[str, Any]] = Field(None, description="插件参数")


class ProxyCreateRequest(ProxyConfig):
    """创建代理请求（同 ProxyConfig）。"""


class ProxyUpdateRequest(BaseModel):
    """更新代理请求（允许部分字段，但 name 不可变）。"""

    type: Optional[str] = None
    local_ip: Optional[str] = None
    local_port: Optional[int] = Field(None, ge=1, le=65535)
    remote_port: Optional[int] = Field(None, ge=1, le=65535)
    enabled: Optional[bool] = None
    custom_domains: Optional[List[str]] = None
    subdomain: Optional[str] = None
    sk: Optional[str] = None
    server_name: Optional[str] = None
    bind_addr: Optional[str] = None
    bind_port: Optional[int] = Field(None, ge=1, le=65535)
    plugin: Optional[str] = None
    plugin_params: Optional[Dict[str, Any]] = None


class ProxyListResponse(BaseModel):
    """代理列表响应。"""

    proxies: List[Dict[str, Any]]
    total: int


class ProxySaveResponse(BaseModel):
    """代理保存响应。"""

    message: str
    need_restart: bool = True
    proxy: Dict[str, Any]


# ---------------- 服务管理相关 ----------------


class ServiceStartResponse(BaseModel):
    """服务启动响应。"""

    name: str
    pid: Optional[int] = None
    message: str


# ---------------- 监控相关 ----------------


class LogHistoryResponse(BaseModel):
    """历史日志响应。"""

    lines: List[str]
    truncated: bool = False


# ---------------- 文件管理相关 ----------------


class FileItem(BaseModel):
    """文件/目录项。"""

    name: str
    path: str
    is_dir: bool = False
    size: int = 0
    modified_at: float = 0


class FileListResponse(BaseModel):
    """文件列表响应。"""

    path: str
    items: List[FileItem]
    parent: Optional[str] = None


class FileContentResponse(BaseModel):
    """文件内容响应。"""

    path: str
    content: str
    size: int


class FileSaveRequest(BaseModel):
    """文件保存请求。"""

    content: str


class FileDeleteRequest(BaseModel):
    """文件/目录删除请求。"""

    path: str


class FileRenameRequest(BaseModel):
    """文件重命名请求。"""

    path: str
    new_name: str


class FileMkdirRequest(BaseModel):
    """创建目录请求。"""

    path: str
    dir_name: str
