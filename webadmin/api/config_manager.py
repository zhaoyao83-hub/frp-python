"""配置读写、校验、schema 定义。

复用现有 config.py 的 Config 类默认值定义；本模块直接基于 json 文件读写，
便于支持原始文本编辑模式。
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Tuple

# 项目根目录（webadmin/api/config_manager.py 的上三级）
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ---------- 配置文件路径 ----------


def _resolve_path(path: str) -> str:
    """将相对路径解析为相对于 PROJECT_ROOT 的绝对路径。"""
    if os.path.isabs(path):
        return path
    return os.path.join(PROJECT_ROOT, path)


def get_config_path(config_type: str, frps_path: str = "config/frps.json", frpc_path: str = "config/frpc.json") -> str:
    """根据配置类型返回对应文件路径。"""
    if config_type == "server":
        return _resolve_path(frps_path)
    elif config_type == "client":
        return _resolve_path(frpc_path)
    raise ValueError(f"未知的配置类型: {config_type}，应为 server 或 client")


# ---------- 配置 schema 定义 ----------
# 每个字段: {name, type, default, required, description, options?}


SERVER_SCHEMA: List[Dict[str, Any]] = [
    {
        "key": "bind_port",
        "type": "number",
        "default": 7000,
        "required": True,
        "description": "服务端监听端口（1-65535）",
        "min": 1,
        "max": 65535,
    },
    {
        "key": "bind_addr",
        "type": "string",
        "default": "0.0.0.0",
        "required": False,
        "description": "服务端监听地址",
    },
    {
        "key": "log_level",
        "type": "select",
        "default": "info",
        "required": False,
        "description": "日志级别",
        "options": ["debug", "info", "warning", "error"],
    },
    {
        "key": "log_file",
        "type": "string",
        "default": None,
        "required": False,
        "description": "日志文件路径（为空则输出到 stdout）",
        "nullable": True,
    },
    {
        "key": "auth_token",
        "type": "string",
        "default": None,
        "required": False,
        "description": "鉴权 token，客户端需一致",
        "nullable": True,
    },
    {
        "key": "max_connections",
        "type": "number",
        "default": 1000,
        "required": False,
        "description": "最大连接数（>=1）",
        "min": 1,
    },
    {
        "key": "idle_timeout",
        "type": "number",
        "default": 300,
        "required": False,
        "description": "空闲连接超时秒数（>=10）",
        "min": 10,
    },
    {
        "key": "tls",
        "type": "boolean",
        "default": False,
        "required": False,
        "description": "是否启用 TLS",
    },
    {
        "key": "tls_cert_file",
        "type": "string",
        "default": None,
        "required": False,
        "description": "TLS 证书文件路径",
        "nullable": True,
    },
    {
        "key": "tls_key_file",
        "type": "string",
        "default": None,
        "required": False,
        "description": "TLS 私钥文件路径",
        "nullable": True,
    },
    {
        "key": "webapi_port",
        "type": "number",
        "default": None,
        "required": False,
        "description": "内置 WebAPI 端口（1-65535 或空）",
        "nullable": True,
        "min": 1,
        "max": 65535,
    },
    {
        "key": "webapi_addr",
        "type": "string",
        "default": "0.0.0.0",
        "required": False,
        "description": "内置 WebAPI 监听地址",
    },
    {
        "key": "allow_ports",
        "type": "string",
        "default": None,
        "required": False,
        "description": "允许的远程端口，如 8080,9000-9100",
        "nullable": True,
    },
    {
        "key": "ip_whitelist",
        "type": "string",
        "default": None,
        "required": False,
        "description": "IP 白名单（CIDR 逗号分隔）",
        "nullable": True,
    },
    {
        "key": "ip_blacklist",
        "type": "string",
        "default": None,
        "required": False,
        "description": "IP 黑名单（CIDR 逗号分隔）",
        "nullable": True,
    },
    {
        "key": "vhost_http_port",
        "type": "number",
        "default": None,
        "required": False,
        "description": "HTTP 虚拟主机监听端口（1-65535 或空）",
        "nullable": True,
        "min": 1,
        "max": 65535,
    },
    {
        "key": "subdomain_host",
        "type": "string",
        "default": None,
        "required": False,
        "description": "子域名根域名，如 example.com",
        "nullable": True,
    },
    {
        "key": "forward_proxy_port",
        "type": "number",
        "default": None,
        "required": False,
        "description": "正向代理监听端口（1-65535 或空），手机/浏览器可设置为此端口作为 HTTP 代理",
        "nullable": True,
        "min": 1,
        "max": 65535,
    },
    {
        "key": "forward_proxy_user",
        "type": "string",
        "default": None,
        "required": False,
        "description": "正向代理认证用户名（为空则不认证）",
        "nullable": True,
    },
    {
        "key": "forward_proxy_pass",
        "type": "string",
        "default": None,
        "required": False,
        "description": "正向代理认证密码",
        "nullable": True,
    },
]


CLIENT_SCHEMA: List[Dict[str, Any]] = [
    {
        "key": "server_addr",
        "type": "string",
        "default": "127.0.0.1",
        "required": True,
        "description": "服务端地址（IP/域名）",
    },
    {
        "key": "server_port",
        "type": "number",
        "default": 7000,
        "required": True,
        "description": "服务端端口（1-65535）",
        "min": 1,
        "max": 65535,
    },
    {
        "key": "log_level",
        "type": "select",
        "default": "info",
        "required": False,
        "description": "日志级别",
        "options": ["debug", "info", "warning", "error"],
    },
    {
        "key": "log_file",
        "type": "string",
        "default": None,
        "required": False,
        "description": "日志文件路径",
        "nullable": True,
    },
    {
        "key": "auth_token",
        "type": "string",
        "default": None,
        "required": False,
        "description": "鉴权 token，需与服务端一致",
        "nullable": True,
    },
    {
        "key": "reconnect",
        "type": "boolean",
        "default": True,
        "required": False,
        "description": "是否自动重连",
    },
    {
        "key": "reconnect_max_retries",
        "type": "number",
        "default": 0,
        "required": False,
        "description": "最大重连次数（0=无限）",
        "min": 0,
    },
    {
        "key": "reconnect_base_delay",
        "type": "number",
        "default": 1,
        "required": False,
        "description": "重连基础延迟秒数",
        "min": 0,
    },
    {
        "key": "reconnect_max_delay",
        "type": "number",
        "default": 60,
        "required": False,
        "description": "重连最大延迟秒数",
        "min": 1,
    },
    {
        "key": "tls",
        "type": "boolean",
        "default": False,
        "required": False,
        "description": "是否启用 TLS",
    },
    {
        "key": "tls_insecure",
        "type": "boolean",
        "default": False,
        "required": False,
        "description": "是否允许不安全的 TLS（自签证书）",
    },
    {
        "key": "tls_ca_file",
        "type": "string",
        "default": None,
        "required": False,
        "description": "TLS CA 证书文件路径",
        "nullable": True,
    },
    {
        "key": "proxies",
        "type": "array",
        "default": [],
        "required": False,
        "description": "代理列表（建议使用「端口映射」页面管理）",
        "item_schema": {
            "name": "string",
            "type": "string",
            "local_ip": "string",
            "local_port": "number",
            "remote_port": "number",
            "custom_domains": "array",
            "subdomain": "string",
            "sk": "string",
            "server_name": "string",
            "bind_addr": "string",
            "bind_port": "number",
            "enabled": "boolean",
        },
    },
]


SCHEMA_MAP = {
    "server": SERVER_SCHEMA,
    "client": CLIENT_SCHEMA,
}


def get_schema(config_type: str) -> List[Dict[str, Any]]:
    """返回指定配置类型的字段 schema（数组，直接返回给前端）。"""
    if config_type not in SCHEMA_MAP:
        raise ValueError(f"未知的配置类型: {config_type}")
    return SCHEMA_MAP[config_type]


# ---------- 默认配置 ----------


def get_default_config(config_type: str) -> Dict[str, Any]:
    """根据 schema 生成默认配置。"""
    schema = SCHEMA_MAP.get(config_type, [])
    defaults: Dict[str, Any] = {}
    for field in schema:
        if "default" in field:
            defaults[field["key"]] = field["default"]
    return defaults


# ---------- 校验逻辑 ----------


def _validate_port_range(value: Any, field_name: str, errors: List[str], mn: int = 1, mx: int = 65535) -> None:
    """校验端口范围。"""
    if value is None:
        return
    if not isinstance(value, int):
        errors.append(f"{field_name} 必须为整数")
        return
    if value < mn or value > mx:
        errors.append(f"{field_name} 必须在 {mn}-{mx} 之间")


def _validate_allow_ports(value: Any, errors: List[str]) -> None:
    """校验 allow_ports 格式：8080,9000-9100。"""
    if value is None or value == "":
        return
    if not isinstance(value, str):
        errors.append("allow_ports 必须为字符串")
        return
    parts = [p.strip() for p in value.split(",") if p.strip()]
    for part in parts:
        if "-" in part:
            segs = part.split("-")
            if len(segs) != 2:
                errors.append(f"allow_ports 端口段格式错误: {part}")
                continue
            try:
                a, b = int(segs[0]), int(segs[1])
                if a < 1 or a > 65535 or b < 1 or b > 65535 or a > b:
                    errors.append(f"allow_ports 端口段非法: {part}")
            except ValueError:
                errors.append(f"allow_ports 端口段格式错误: {part}")
        else:
            try:
                p = int(part)
                if p < 1 or p > 65535:
                    errors.append(f"allow_ports 端口非法: {part}")
            except ValueError:
                errors.append(f"allow_ports 端口格式错误: {part}")


def _validate_ip_list(value: Any, field_name: str, errors: List[str]) -> None:
    """简单校验 IP/CIDR 列表格式（逗号分隔）。"""
    if value is None or value == "":
        return
    if not isinstance(value, str):
        errors.append(f"{field_name} 必须为字符串")
        return
    parts = [p.strip() for p in value.split(",") if p.strip()]
    for part in parts:
        # 允许 IP 或 CIDR，简单校验不含非法字符
        if not all(c.isalnum() or c in ".:/" for c in part):
            errors.append(f"{field_name} 条目格式错误: {part}")
            break


def validate_server_config(config: Dict[str, Any]) -> List[str]:
    """校验服务端配置，返回错误列表（空表示通过）。"""
    errors: List[str] = []

    # bind_port 必填
    bind_port = config.get("bind_port")
    if bind_port is None:
        errors.append("bind_port 为必填项")
    else:
        _validate_port_range(bind_port, "bind_port", errors)

    # max_connections
    max_conn = config.get("max_connections")
    if max_conn is not None:
        if not isinstance(max_conn, int) or max_conn < 1:
            errors.append("max_connections 必须 >=1")

    # idle_timeout
    idle = config.get("idle_timeout")
    if idle is not None:
        if not isinstance(idle, int) or idle < 10:
            errors.append("idle_timeout 必须 >=10")

    # tls
    tls = config.get("tls")
    if tls is not None and not isinstance(tls, bool):
        errors.append("tls 必须为布尔值")

    # webapi_port
    webapi_port = config.get("webapi_port")
    if webapi_port is not None:
        _validate_port_range(webapi_port, "webapi_port", errors)

    # allow_ports
    _validate_allow_ports(config.get("allow_ports"), errors)

    # ip 白名单/黑名单
    _validate_ip_list(config.get("ip_whitelist"), "ip_whitelist", errors)
    _validate_ip_list(config.get("ip_blacklist"), "ip_blacklist", errors)

    # vhost_http_port
    vhost_http_port = config.get("vhost_http_port")
    if vhost_http_port is not None:
        _validate_port_range(vhost_http_port, "vhost_http_port", errors)

    # subdomain_host
    subdomain_host = config.get("subdomain_host")
    if subdomain_host is not None and not isinstance(subdomain_host, str):
        errors.append("subdomain_host 必须为字符串")

    # forward_proxy_port
    forward_proxy_port = config.get("forward_proxy_port")
    if forward_proxy_port is not None:
        _validate_port_range(forward_proxy_port, "forward_proxy_port", errors)

    return errors


def validate_client_config(config: Dict[str, Any]) -> List[str]:
    """校验客户端配置，返回错误列表。"""
    errors: List[str] = []

    # server_addr 必填
    server_addr = config.get("server_addr")
    if not server_addr:
        errors.append("server_addr 为必填项")
    elif not isinstance(server_addr, str):
        errors.append("server_addr 必须为字符串")

    # server_port 必填
    server_port = config.get("server_port")
    if server_port is None:
        errors.append("server_port 为必填项")
    else:
        _validate_port_range(server_port, "server_port", errors)

    # proxies 校验
    proxies = config.get("proxies", [])
    if proxies is not None:
        if not isinstance(proxies, list):
            errors.append("proxies 必须为数组")
        else:
            names = set()
            valid_types = ("tcp", "udp", "http", "https", "ftp", "stcp", "stcp_visitor")
            for idx, proxy in enumerate(proxies):
                if not isinstance(proxy, dict):
                    errors.append(f"proxies[{idx}] 必须为对象")
                    continue
                name = proxy.get("name")
                if not name:
                    errors.append(f"proxies[{idx}] 缺少 name")
                elif name in names:
                    errors.append(f"proxies[{idx}] name 重复: {name}")
                else:
                    names.add(name)
                ptype = proxy.get("type", "tcp")
                if ptype not in valid_types:
                    errors.append(f"proxies[{idx}] type 无效: {ptype}")
                # local_port: tcp/udp/http/stcp 需要
                if ptype in ("tcp", "udp", "http", "stcp"):
                    lp = proxy.get("local_port")
                    if lp is None:
                        errors.append(f"proxies[{idx}] 缺少 local_port（{ptype} 类型）")
                    else:
                        _validate_port_range(lp, f"proxies[{idx}].local_port", errors)
                # remote_port: tcp/udp 需要
                if ptype in ("tcp", "udp"):
                    rp = proxy.get("remote_port")
                    if rp is None:
                        errors.append(f"proxies[{idx}] 缺少 remote_port（{ptype} 类型）")
                    else:
                        _validate_port_range(rp, f"proxies[{idx}].remote_port", errors)
                # bind_port: stcp_visitor 需要
                if ptype == "stcp_visitor":
                    bp = proxy.get("bind_port")
                    if bp is not None:
                        _validate_port_range(bp, f"proxies[{idx}].bind_port", errors)

    return errors


def validate_config(config_type: str, config: Dict[str, Any]) -> List[str]:
    """校验配置，返回错误列表。"""
    if config_type == "server":
        return validate_server_config(config)
    elif config_type == "client":
        return validate_client_config(config)
    raise ValueError(f"未知的配置类型: {config_type}")


# ---------- 文件读写 ----------


def read_config(config_type: str, frps_path: str = "config/frps.json", frpc_path: str = "config/frpc.json") -> Dict[str, Any]:
    """读取配置文件并返回 dict；不存在则返回默认配置。"""
    path = get_config_path(config_type, frps_path, frpc_path)
    if not os.path.exists(path):
        return get_default_config(config_type)
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def write_config(
    config_type: str,
    config: Dict[str, Any],
    frps_path: str = "config/frps.json",
    frpc_path: str = "config/frpc.json",
) -> None:
    """写入配置文件（先校验，由调用方处理校验结果）。"""
    path = get_config_path(config_type, frps_path, frpc_path)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=4, ensure_ascii=False)


def read_raw(config_type: str, frps_path: str = "config/frps.json", frpc_path: str = "config/frpc.json") -> str:
    """读取配置文件原始文本；不存在则返回默认配置的 JSON 字符串。"""
    path = get_config_path(config_type, frps_path, frpc_path)
    if not os.path.exists(path):
        default = get_default_config(config_type)
        return json.dumps(default, indent=4, ensure_ascii=False)
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def write_raw(
    config_type: str,
    content: str,
    frps_path: str = "config/frps.json",
    frpc_path: str = "config/frpc.json",
) -> Tuple[Dict[str, Any], List[str]]:
    """写入原始文本配置，返回 (解析后的 dict, 校验错误列表)。"""
    path = get_config_path(config_type, frps_path, frpc_path)
    # 先尝试解析 JSON
    try:
        config = json.loads(content)
    except json.JSONDecodeError as e:
        return {}, [f"JSON 解析错误: {e}"]
    # 校验
    errors = validate_config(config_type, config)
    if errors:
        return config, errors
    # 写入
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    return config, []


def save_config_validated(
    config_type: str,
    config: Dict[str, Any],
    frps_path: str = "config/frps.json",
    frpc_path: str = "config/frpc.json",
) -> Tuple[bool, List[str]]:
    """校验并保存配置，返回 (是否成功, 错误列表)。"""
    errors = validate_config(config_type, config)
    if errors:
        return False, errors
    write_config(config_type, config, frps_path, frpc_path)
    return True, []
