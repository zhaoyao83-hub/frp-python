"""配置管理路由 /api/config/*。"""

from __future__ import annotations

from typing import Any, Dict, List

from fastapi import APIRouter, HTTPException, Request, status

from ..config_manager import (
    get_schema,
    read_config,
    read_raw,
    save_config_validated,
    validate_config,
    write_raw,
)
from ..schemas import (
    ConfigRawResponse,
    ConfigRawSaveRequest,
    ConfigResponse,
    ConfigSaveResponse,
    ConfigValidateResponse,
)

router = APIRouter(prefix="/api/config", tags=["config"])

_VALID_TYPES = ("server", "client")


def _resolve_paths(request: Request):
    """从 app state 读取 frps/frpc 配置路径。"""
    state = request.app.state
    return (
        getattr(state, "frps_config_path", "config/frps.json"),
        getattr(state, "frpc_config_path", "config/frpc.json"),
    )


def _check_type(config_type: str) -> None:
    """校验配置类型。"""
    if config_type not in _VALID_TYPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"未知的配置类型: {config_type}，应为 server 或 client",
        )


@router.get("/{config_type}", response_model=ConfigResponse)
async def get_config(config_type: str, request: Request):
    """读取配置 JSON + schema。"""
    _check_type(config_type)
    frps_path, frpc_path = _resolve_paths(request)
    config = read_config(config_type, frps_path, frpc_path)
    schema = get_schema(config_type)
    return ConfigResponse(config=config, schema=schema)


@router.put("/{config_type}", response_model=ConfigSaveResponse)
async def save_config(config_type: str, config: Dict[str, Any], request: Request):
    """校验并保存配置（返回 need_restart=true）。请求体直接为配置 JSON。"""
    _check_type(config_type)
    frps_path, frpc_path = _resolve_paths(request)
    ok, errors = save_config_validated(config_type, config, frps_path, frpc_path)
    if not ok:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="; ".join(errors),
        )
    return ConfigSaveResponse(message="配置已保存", need_restart=True)


@router.post("/{config_type}/validate", response_model=ConfigValidateResponse)
async def validate_config_endpoint(config_type: str, config: Dict[str, Any]):
    """仅校验配置（不保存）。请求体直接为配置 JSON。"""
    _check_type(config_type)
    errors = validate_config(config_type, config)
    return ConfigValidateResponse(valid=not errors, errors=errors)


@router.get("/{config_type}/schema")
async def get_config_schema(config_type: str) -> List[Dict[str, Any]]:
    """返回字段 schema 数组（用于前端表单生成）。"""
    _check_type(config_type)
    return get_schema(config_type)


@router.get("/{config_type}/raw", response_model=ConfigRawResponse)
async def get_raw_config(config_type: str, request: Request):
    """原始文本读取配置。"""
    _check_type(config_type)
    frps_path, frpc_path = _resolve_paths(request)
    content = read_raw(config_type, frps_path, frpc_path)
    return ConfigRawResponse(content=content)


@router.put("/{config_type}/raw", response_model=ConfigSaveResponse)
async def save_raw_config(config_type: str, req: ConfigRawSaveRequest, request: Request):
    """解析 JSON 校验 + 保存原文。"""
    _check_type(config_type)
    frps_path, frpc_path = _resolve_paths(request)
    _, errors = write_raw(config_type, req.content, frps_path, frpc_path)
    if errors:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="; ".join(errors),
        )
    return ConfigSaveResponse(message="配置已保存", need_restart=True)
