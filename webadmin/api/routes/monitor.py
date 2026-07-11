"""监控路由 /api/monitor/* + WebSocket /ws/*。"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Dict, Optional

import httpx
from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect, status
from fastapi.responses import PlainTextResponse

from ..auth import get_auth_manager
from ..config_manager import read_config
from ..schemas import LogHistoryResponse, MessageResponse

router = APIRouter(tags=["monitor"])

_VALID_LOG_NAMES = ("frps", "frpc", "dashboard")


# ---------- 工具函数 ----------


def _get_log_buffer(request: Request, name: str):
    """从 app state 获取指定名称的日志缓冲。"""
    if name not in _VALID_LOG_NAMES:
        return None
    buffers = getattr(request.app.state, "log_buffers", {})
    return buffers.get(name)


async def _fetch_stats(request: Request) -> Optional[Dict[str, Any]]:
    """请求 frps /stats；失败返回 None。"""
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
    if webapi_addr in ("0.0.0.0", "::"):
        webapi_addr = "127.0.0.1"
    url = f"http://{webapi_addr}:{webapi_port}/stats"

    registry = getattr(state, "service_registry", None)
    if registry is not None:
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


def _ws_validate_token(websocket: WebSocket) -> Optional[Dict[str, Any]]:
    """校验 WebSocket query 参数中的 token。

    返回用户字典；无效返回 None（调用方负责关闭连接）。
    """
    token = websocket.query_params.get("token")
    if not token:
        return None
    manager = get_auth_manager()
    try:
        payload = manager.decode_token(token)
    except Exception:
        return None
    username = payload.get("sub")
    if not username:
        return None
    user = manager.find_user(username)
    return user


# ---------- HTTP 路由 ----------


@router.get("/api/monitor/stats")
async def monitor_stats(request: Request):
    """实时统计快照（frps /stats 返回内容）。

    统一响应格式，始终包含 available 字段：
    - available=true: summary/proxies 为 frps 实时数据
    - available=false: frps 未运行或请求失败，summary 为零值
    """
    stats = await _fetch_stats(request)
    if stats is None:
        return {
            "available": False,
            "summary": {
                "uptime": 0,
                "total_connections": 0,
                "current_connections": 0,
                "total_proxies": 0,
                "total_bytes_in": 0,
                "total_bytes_out": 0,
            },
            "proxies": {},
        }
    return {
        "available": True,
        "summary": stats.get("summary", {}) or {},
        "proxies": stats.get("proxies", {}) or {},
    }


@router.get("/api/monitor/logs/{name}", response_model=LogHistoryResponse)
async def get_logs(name: str, request: Request):
    """历史日志（name=frps/frpc/dashboard）。"""
    if name not in _VALID_LOG_NAMES:
        return LogHistoryResponse(lines=[], truncated=False)
    buffer = _get_log_buffer(request, name)
    if buffer is None:
        return LogHistoryResponse(lines=[], truncated=False)
    lines = buffer.get_history()
    return LogHistoryResponse(lines=lines, truncated=False)


@router.get("/api/monitor/logs/{name}/download", response_class=PlainTextResponse)
async def download_logs(name: str, request: Request):
    """下载完整日志（text/plain）。"""
    if name not in _VALID_LOG_NAMES:
        return PlainTextResponse("", status_code=404)
    buffer = _get_log_buffer(request, name)
    if buffer is None:
        return PlainTextResponse("", status_code=404)
    content = "\n".join(buffer.get_history())
    return PlainTextResponse(content, media_type="text/plain; charset=utf-8")


@router.delete("/api/monitor/logs/{name}", response_model=MessageResponse)
async def clear_logs(name: str, request: Request):
    """清空日志缓冲。"""
    if name not in _VALID_LOG_NAMES:
        return MessageResponse(message=f"未知日志名: {name}")
    buffer = _get_log_buffer(request, name)
    if buffer is not None:
        buffer.clear()
    return MessageResponse(message=f"日志 {name} 已清空")


# ---------- WebSocket 路由 ----------


@router.websocket("/ws/logs")
async def ws_logs(websocket: WebSocket):
    """实时日志流。

    鉴权：query 参数 token；name 指定 frps/frpc/dashboard。
    无效 token 关闭连接（code=4401）。
    """
    # 鉴权
    user = _ws_validate_token(websocket)
    if user is None:
        await websocket.close(code=4401)
        return

    name = websocket.query_params.get("name", "frps")
    if name not in _VALID_LOG_NAMES:
        await websocket.close(code=4400)
        return

    buffers = getattr(websocket.app.state, "log_buffers", {})
    buffer = buffers.get(name)
    if buffer is None:
        await websocket.close(code=4404)
        return

    await websocket.accept()

    # 先推送历史日志
    try:
        for line in buffer.get_history():
            await websocket.send_text(line)
    except WebSocketDisconnect:
        return
    except Exception:
        return

    # 订阅新日志
    try:
        async for line in buffer.subscribe():
            await websocket.send_text(line)
    except WebSocketDisconnect:
        return
    except Exception:
        return


@router.websocket("/ws/stats")
async def ws_stats(websocket: WebSocket):
    """实时统计推送，每 2s 推送一次 stats。

    鉴权：query 参数 token；无效则关闭（code=4401）。
    """
    user = _ws_validate_token(websocket)
    if user is None:
        await websocket.close(code=4401)
        return

    await websocket.accept()

    # 构造一个伪 Request 以复用 _fetch_stats
    # 这里直接内联逻辑，避免依赖 Request 对象
    state = websocket.app.state
    try:
        while True:
            stats = await _fetch_stats_from_state(state)
            payload = json.dumps(stats or {"available": False}, ensure_ascii=False)
            await websocket.send_text(payload)
            await asyncio.sleep(2)
    except WebSocketDisconnect:
        return
    except Exception:
        return


async def _fetch_stats_from_state(state) -> Optional[Dict[str, Any]]:
    """从 app state 读取 frps 配置并请求 /stats。"""
    frps_path = getattr(state, "frps_config_path", "config/frps.json")
    try:
        frps_cfg = read_config("server", frps_path)
    except Exception:
        return None
    webapi_port = frps_cfg.get("webapi_port")
    if not webapi_port:
        return None
    webapi_addr = frps_cfg.get("webapi_addr", "127.0.0.1")
    if webapi_addr in ("0.0.0.0", "::"):
        webapi_addr = "127.0.0.1"
    url = f"http://{webapi_addr}:{webapi_port}/stats"

    registry = getattr(state, "service_registry", None)
    if registry is not None:
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
