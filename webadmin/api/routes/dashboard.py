"""仪表盘路由 /api/dashboard/*。

通过 httpx 异步请求 frps 内置的 /stats 端点获取统计数据；
frps/frpc 状态从 ServiceManager 获取。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import httpx
from fastapi import APIRouter, Request

from ..config_manager import read_config
from ..schemas import (
    ConnectionItem,
    OverviewResponse,
    ProxyItem,
    ServiceStatus,
)

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


def _get_frps_stats_url(request: Request) -> Optional[str]:
    """从 frps.json 读取 webapi_addr/webapi_port，构造 /stats URL。

    若 webapi_port 为空或配置缺失，返回 None。
    """
    state = request.app.state
    frps_path = getattr(state, "frps_config_path", "config/frps.json")
    try:
        frps_cfg = read_config("server", frps_path)
    except Exception:
        return None
    webapi_port = frps_cfg.get("webapi_port")
    if not webapi_port:
        return None
    webapi_addr = frps_cfg.get("webapi_addr", "127.0.0.1")
    # 浏览器侧访问 frps 时通常用 127.0.0.1 或实际地址；此处用配置地址
    if webapi_addr in ("0.0.0.0", "::"):
        webapi_addr = "127.0.0.1"
    return f"http://{webapi_addr}:{webapi_port}/stats"


async def _fetch_frps_stats(request: Request) -> Optional[Dict[str, Any]]:
    """请求 frps /stats 端点；失败返回 None。"""
    url = _get_frps_stats_url(request)
    if not url:
        return None
    state = request.app.state
    # 仅当 frps 服务在运行时才请求
    registry = getattr(state, "service_registry", None)
    if registry is None:
        return None
    frps_mgr = registry.get("frps")
    if frps_mgr is None or not frps_mgr.running:
        return None
    try:
        async with httpx.AsyncClient(timeout=3, trust_env=False) as client:
            resp = await client.get(url)
            if resp.status_code == 200:
                return resp.json()
    except Exception:
        return None
    return None


def _service_status(registry, name: str) -> ServiceStatus:
    """从 ServiceRegistry 获取单个服务状态。"""
    if registry is None:
        return ServiceStatus(name=name, running=False)
    mgr = registry.get(name)
    if mgr is None:
        return ServiceStatus(name=name, running=False)
    st = mgr.status()
    return ServiceStatus(**st)


@router.get("/overview", response_model=OverviewResponse)
async def overview(request: Request):
    """总览：frps/frpc 状态 + frps 统计数据。"""
    registry = getattr(request.app.state, "service_registry", None)
    frps_status = _service_status(registry, "frps")
    frpc_status = _service_status(registry, "frpc")

    stats = await _fetch_frps_stats(request)
    summary: Dict[str, Any] = {}
    if stats and isinstance(stats, dict):
        summary = stats.get("summary", {}) or {}

    return OverviewResponse(
        frps_status=frps_status,
        frpc_status=frpc_status,
        uptime=summary.get("uptime", 0),
        total_proxies=summary.get("total_proxies", 0),
        current_connections=summary.get("current_connections", 0),
        total_bytes_in=summary.get("total_bytes_in", 0),
        total_bytes_out=summary.get("total_bytes_out", 0),
        stats_available=stats is not None,
    )


@router.get("/proxies", response_model=list[ProxyItem])
async def proxies(request: Request):
    """代理列表。"""
    stats = await _fetch_frps_stats(request)
    if not stats:
        return []
    proxy_map: Dict[str, Any] = stats.get("proxies", {}) or {}
    result: List[ProxyItem] = []
    for name, info in proxy_map.items():
        if not isinstance(info, dict):
            continue
        result.append(
            ProxyItem(
                name=name,
                type=info.get("type", ""),
                remote_port=info.get("remote_port", 0),
                status="online",
                current_conns=info.get("current_conns", 0),
                total_conns=info.get("connections", 0),
                bytes_in=info.get("bytes_in", 0),
                bytes_out=info.get("bytes_out", 0),
                created_at=info.get("created_at", 0),
            )
        )
    return result


@router.get("/connections", response_model=list[ConnectionItem])
async def connections(request: Request):
    """活跃连接列表。"""
    stats = await _fetch_frps_stats(request)
    if not stats:
        return []
    # frps /stats 当前返回 summary + proxies；连接明细若存在则用 conn_stats
    conn_map: Dict[str, Any] = stats.get("connections", {}) or {}
    result: List[ConnectionItem] = []
    for conn_id, info in conn_map.items():
        if not isinstance(info, dict):
            continue
        result.append(
            ConnectionItem(
                conn_id=str(conn_id),
                proxy_name=info.get("proxy_name", ""),
                bytes_in=info.get("bytes_in", 0),
                bytes_out=info.get("bytes_out", 0),
                created_at=info.get("created_at", 0),
            )
        )
    return result
