"""服务启停路由 /api/service/*。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status

from ..auth import require_admin
from ..schemas import MessageResponse, ServiceStartResponse, ServiceStatus

router = APIRouter(prefix="/api/service", tags=["service"])

_VALID_NAMES = ("frps", "frpc")


def _get_manager(request: Request, name: str):
    """从 app state 获取 ServiceManager，校验名称。"""
    if name not in _VALID_NAMES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"未知的服务名: {name}，应为 frps 或 frpc",
        )
    registry = getattr(request.app.state, "service_registry", None)
    if registry is None:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="服务注册表未初始化")
    mgr = registry.get(name)
    if mgr is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"服务 {name} 不存在")
    return mgr


@router.get("/{name}/status", response_model=ServiceStatus)
async def get_status(name: str, request: Request):
    """获取服务状态。"""
    mgr = _get_manager(request, name)
    return ServiceStatus(**mgr.status())


@router.post(
    "/{name}/start",
    response_model=ServiceStartResponse,
    dependencies=[Depends(require_admin)],
)
async def start_service(name: str, request: Request, kill_existing: bool = False):
    """启动服务（仅 admin）。

    Args:
        kill_existing: 若检测到遗留进程，是否先清理再启动。
    """
    mgr = _get_manager(request, name)
    try:
        result = await mgr.start(kill_existing=kill_existing)
    except RuntimeError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))
    return ServiceStartResponse(name=name, pid=result.get("pid"), message="已启动")


@router.post(
    "/{name}/kill-external",
    response_model=MessageResponse,
    dependencies=[Depends(require_admin)],
)
async def kill_external_process(name: str, request: Request):
    """杀掉检测到的外部遗留进程（仅 admin）。"""
    mgr = _get_manager(request, name)
    result = await mgr.kill_external()
    return MessageResponse(message=result.get("message", "操作完成"))


@router.post(
    "/{name}/stop",
    response_model=MessageResponse,
    dependencies=[Depends(require_admin)],
)
async def stop_service(name: str, request: Request):
    """停止服务（仅 admin）。"""
    mgr = _get_manager(request, name)
    result = await mgr.stop()
    return MessageResponse(message=result.get("message", "已停止"))


@router.post(
    "/{name}/restart",
    response_model=ServiceStartResponse,
    dependencies=[Depends(require_admin)],
)
async def restart_service(name: str, request: Request):
    """重启服务（仅 admin）。"""
    mgr = _get_manager(request, name)
    try:
        result = await mgr.restart()
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))
    return ServiceStartResponse(name=name, pid=result.get("pid"), message="已重启")
