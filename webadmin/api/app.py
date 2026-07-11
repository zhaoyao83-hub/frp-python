"""FastAPI app 创建、路由注册、静态文件托管、run_dashboard 函数。

dashboard 与 frps/frpc 是独立进程，通过 asyncio.subprocess 管理。
所有读写用 asyncio 协程，单线程。
"""

from __future__ import annotations

import asyncio
import json
import os
import signal
import sys
import uuid
from typing import Any, Dict, Optional

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .auth import get_auth_manager, init_auth
from .config_manager import PROJECT_ROOT
from .log_buffer import LogBuffer
from .service_manager import ServiceManager, ServiceRegistry, build_default_cmd
from .routes.auth import router as auth_router
from .routes.config import router as config_router
from .routes.dashboard import router as dashboard_router
from .routes.files import router as files_router
from .routes.monitor import router as monitor_router
from .routes.proxies import router as proxies_router
from .routes.service import router as service_router

# 项目根目录（webadmin/api/app.py 的上三级）
APP_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 默认 dashboard 配置
_DEFAULT_DASHBOARD_CONFIG: Dict[str, Any] = {
    "host": "0.0.0.0",
    "port": 7500,
    "token_expire_minutes": 720,
    "auto_restart": False,
    "frps_config_path": "config/frps.json",
    "frpc_config_path": "config/frpc.json",
    "log_buffer_lines": 1000,
    "file_manager_roots": ["config", "logs"],
}


def _generate_default_dashboard_config() -> Dict[str, Any]:
    """生成默认 dashboard 配置（含 admin/admin123 的 bcrypt 哈希）。"""
    from passlib.context import CryptContext

    ctx = CryptContext(schemes=["bcrypt"], deprecated="auto")
    config = dict(_DEFAULT_DASHBOARD_CONFIG)
    config["secret_key"] = uuid.uuid4().hex
    config["users"] = [
        {
            "username": "admin",
            "password_hash": ctx.hash("admin123"),
            "role": "admin",
            "must_change_password": True,
        }
    ]
    return config


def load_dashboard_config(config_path: str) -> Dict[str, Any]:
    """加载 webadmin.json；不存在则生成默认配置并写入文件。"""
    abs_path = config_path if os.path.isabs(config_path) else os.path.join(APP_PROJECT_ROOT, config_path)
    if not os.path.exists(abs_path):
        config = _generate_default_dashboard_config()
        os.makedirs(os.path.dirname(abs_path) or ".", exist_ok=True)
        with open(abs_path, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2, ensure_ascii=False)
        return config
    with open(abs_path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_dashboard_config(config_path: str, config: Dict[str, Any]) -> None:
    """写回 webadmin.json。"""
    abs_path = config_path if os.path.isabs(config_path) else os.path.join(APP_PROJECT_ROOT, config_path)
    with open(abs_path, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2, ensure_ascii=False)


class DashboardState:
    """dashboard 全局状态容器（单例）。"""

    def __init__(self) -> None:
        self.config_path: str = "config/webadmin.json"
        self.config: Dict[str, Any] = {}
        self.service_registry: ServiceRegistry = ServiceRegistry()
        self.log_buffers: Dict[str, LogBuffer] = {}
        self.frps_config_path: str = "config/frps.json"
        self.frpc_config_path: str = "config/frpc.json"
        self.auto_restart: bool = False
        self.log_buffer_lines: int = 1000
        self.file_manager_roots: list = []


# 模块级单例
_state: Optional[DashboardState] = None


def get_state() -> DashboardState:
    """获取全局 DashboardState。"""
    global _state
    if _state is None:
        _state = DashboardState()
    return _state


def create_app(config_path: str = "config/webadmin.json") -> FastAPI:
    """创建 FastAPI 应用。

    1. 加载 webadmin.json（不存在则生成）
    2. 初始化鉴权、ServiceRegistry、LogBuffers
    3. 注册所有路由
    4. 挂载 web/dist 静态文件（若存在）
    """
    state = get_state()
    state.config_path = config_path
    state.config = load_dashboard_config(config_path)

    cfg = state.config
    state.frps_config_path = cfg.get("frps_config_path", "config/frps.json")
    state.frpc_config_path = cfg.get("frpc_config_path", "config/frpc.json")
    state.auto_restart = bool(cfg.get("auto_restart", False))
    state.log_buffer_lines = int(cfg.get("log_buffer_lines", 1000))
    state.file_manager_roots = cfg.get("file_manager_roots", [])

    # 初始化日志缓冲
    state.log_buffers = {
        "frps": LogBuffer(state.log_buffer_lines),
        "frpc": LogBuffer(state.log_buffer_lines),
        "dashboard": LogBuffer(state.log_buffer_lines),
    }
    state.log_buffers["dashboard"].append("[dashboard] 面板启动")

    # 初始化鉴权（持久化回调写回 webadmin.json）
    def _persist():
        save_dashboard_config(state.config_path, state.config)

    init_auth(
        secret_key=cfg.get("secret_key", uuid.uuid4().hex),
        token_expire_minutes=int(cfg.get("token_expire_minutes", 720)),
        users=cfg.get("users", []),
        persist_callback=_persist,
    )

    # 初始化 ServiceRegistry（不自动启动）
    frps_cmd = build_default_cmd("frps", state.frps_config_path)
    frpc_cmd = build_default_cmd("frpc", state.frpc_config_path)

    frps_mgr = ServiceManager(
        name="frps",
        cmd_args=frps_cmd,
        config_path=state.frps_config_path,
        log_buffer=state.log_buffers["frps"],
        auto_restart=state.auto_restart,
    )
    frpc_mgr = ServiceManager(
        name="frpc",
        cmd_args=frpc_cmd,
        config_path=state.frpc_config_path,
        log_buffer=state.log_buffers["frpc"],
        auto_restart=state.auto_restart,
    )
    state.service_registry.register("frps", frps_mgr)
    state.service_registry.register("frpc", frpc_mgr)

    # 创建 FastAPI 应用
    app = FastAPI(
        title="MyFRP Dashboard",
        description="MyFRP Web 管理面板后端",
        version="1.0.0",
    )

    # 将状态挂载到 app.state，供路由访问
    app.state.config_path = config_path
    app.state.dashboard_config = cfg
    app.state.service_registry = state.service_registry
    app.state.log_buffers = state.log_buffers
    app.state.frps_config_path = state.frps_config_path
    app.state.frpc_config_path = state.frpc_config_path
    app.state.auto_restart = state.auto_restart
    app.state.log_buffer_lines = state.log_buffer_lines
    app.state.file_manager_roots = state.file_manager_roots

    # 注册路由
    app.include_router(auth_router)
    app.include_router(dashboard_router)
    app.include_router(config_router)
    app.include_router(service_router)
    app.include_router(monitor_router)
    app.include_router(proxies_router)
    app.include_router(files_router)

    # 挂载静态文件（若 webadmin/app/dist 存在）
    web_dist = os.path.join(APP_PROJECT_ROOT, "webadmin", "app", "dist")
    if os.path.isdir(web_dist):
        assets_dir = os.path.join(web_dist, "assets")
        if os.path.isdir(assets_dir):
            app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")

        index_path = os.path.join(web_dist, "index.html")

        # 根路径显式返回 index.html（管理面板默认页）
        @app.get("/", include_in_schema=False)
        async def root_index():
            if os.path.isfile(index_path):
                return FileResponse(index_path)
            return JSONResponse({"detail": "web/dist/index.html not found"}, status_code=404)

        # SPA 兜底：非 /api、/ws 路径返回 index.html（支持前端路由）
        @app.get("/{full_path:path}", include_in_schema=False)
        async def spa_fallback(full_path: str):
            """SPA 前端路由兜底，返回 index.html。"""
            # 排除 API 与 WebSocket 路径
            if full_path.startswith(("api/", "ws", "ws/")):
                return JSONResponse({"detail": "Not Found"}, status_code=404)
            # 优先返回对应静态文件
            candidate = os.path.join(web_dist, full_path)
            if full_path and os.path.isfile(candidate):
                return FileResponse(candidate)
            if os.path.isfile(index_path):
                return FileResponse(index_path)
            return JSONResponse({"detail": "Not Found"}, status_code=404)

    # 注册优雅退出信号处理
    _register_signal_handlers(app, state)

    return app


def _register_signal_handlers(app: FastAPI, state: DashboardState) -> None:
    """注册信号处理，dashboard 退出时优雅终止所有子进程。"""

    @app.on_event("shutdown")
    async def _shutdown():
        try:
            await state.service_registry.shutdown_all()
            state.log_buffers.get("dashboard", None) and state.log_buffers["dashboard"].append(
                "[dashboard] 已关闭所有子进程"
            )
        except Exception:
            pass


def run_dashboard(
    config_path: str = "config/webadmin.json",
    host: Optional[str] = None,
    port: Optional[int] = None,
) -> None:
    """启动 dashboard 服务：create_app + uvicorn.run。"""
    import uvicorn

    # 先加载配置以确定 host/port
    cfg = load_dashboard_config(config_path)
    final_host = host if host is not None else cfg.get("host", "0.0.0.0")
    final_port = port if port is not None else int(cfg.get("port", 7500))

    app = create_app(config_path)
    uvicorn.run(app, host=final_host, port=final_port)
