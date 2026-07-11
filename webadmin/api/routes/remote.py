"""远程管理路由 /api/remote/*。

通过 frps 内置 webapi 获取客户端列表、下发远程命令。
仅 admin 角色可访问。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request

from ..auth import require_admin
from ..config_manager import read_config

router = APIRouter(prefix="/api/remote", tags=["remote"])


def _get_frps_api_base(request: Request) -> Optional[str]:
    """从 frps.json 读取 webapi_addr/webapi_port，构造基础 URL。"""
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
    return f"http://{webapi_addr}:{webapi_port}"


def _check_frps_running(request: Request) -> bool:
    """检查 frps 服务是否运行中。"""
    state = request.app.state
    registry = getattr(state, "service_registry", None)
    if registry is None:
        return False
    frps_mgr = registry.get("frps")
    return frps_mgr is not None and frps_mgr.running


@router.get("/clients", dependencies=[Depends(require_admin)])
async def list_clients(request: Request):
    """列出所有已连接的客户端。"""
    if not _check_frps_running(request):
        return {"clients": [], "frps_running": False}

    base = _get_frps_api_base(request)
    if not base:
        raise HTTPException(503, "frps webapi not configured")

    try:
        async with httpx.AsyncClient(timeout=5, trust_env=False) as client:
            resp = await client.get(f"{base}/clients")
            if resp.status_code == 200:
                data = resp.json()
                data["frps_running"] = True
                return data
            else:
                raise HTTPException(502, f"frps api error: {resp.status_code}")
    except Exception as e:
        raise HTTPException(502, f"Failed to fetch clients: {e}")


@router.post("/clients/{session_id}/cmd", dependencies=[Depends(require_admin)])
async def send_command(
    session_id: str,
    request: Request,
    body: Dict[str, Any],
):
    """向指定客户端发送远程命令。

    请求体:
      - cmd: 命令名称（list_proxies, list_files, read_file, write_file,
        delete_file, screenshot, sys_info, exec_shell 等）
      - args: 命令参数（dict）
      - timeout: 超时时间（秒），默认 30
    """
    if not _check_frps_running(request):
        raise HTTPException(503, "frps not running")

    base = _get_frps_api_base(request)
    if not base:
        raise HTTPException(503, "frps webapi not configured")

    cmd = body.get("cmd", "")
    if not cmd:
        raise HTTPException(400, "cmd is required")

    args = body.get("args", {})
    timeout = body.get("timeout", 30)

    try:
        async with httpx.AsyncClient(timeout=timeout + 5, trust_env=False) as client:
            resp = await client.post(
                f"{base}/clients/{session_id}/cmd",
                json={"cmd": cmd, "args": args, "timeout": timeout},
            )
            if resp.status_code == 200:
                return resp.json()
            else:
                raise HTTPException(502, f"frps api error: {resp.status_code}")
    except httpx.TimeoutException:
        raise HTTPException(504, "Command timeout")
    except Exception as e:
        raise HTTPException(502, f"Failed to send command: {e}")
