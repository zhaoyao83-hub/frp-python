"""端口映射（代理）管理路由 /api/proxies/*。

底层读写 frpc.json 的 proxies 字段，提供 CRUD 接口。
"""

from __future__ import annotations

import json
from typing import Any, Dict, List

from fastapi import APIRouter, Depends, HTTPException, Request, status

from ..auth import require_admin
from ..config_manager import read_config, write_config
from ..schemas import (
    ProxyCreateRequest,
    ProxyListResponse,
    ProxySaveResponse,
    ProxyUpdateRequest,
)

router = APIRouter(prefix="/api/proxies", tags=["proxies"])

_VALID_TYPES = ("tcp", "udp", "http", "https", "ftp", "stcp", "stcp_visitor")


def _get_frpc_path(request: Request) -> str:
    """从 app state 获取 frpc 配置路径。"""
    state = request.app.state
    return getattr(state, "frpc_config_path", "config/frpc.json")


def _load_proxies(request: Request) -> List[Dict[str, Any]]:
    """加载 proxies 列表。"""
    frpc_path = _get_frpc_path(request)
    config = read_config("client", frpc_path=frpc_path)
    proxies = config.get("proxies", []) or []
    if not isinstance(proxies, list):
        return []
    return proxies


def _save_proxies(request: Request, proxies: List[Dict[str, Any]]) -> None:
    """保存 proxies 列表到 frpc.json。"""
    frpc_path = _get_frpc_path(request)
    config = read_config("client", frpc_path=frpc_path)
    config["proxies"] = proxies
    write_config("client", config, frpc_path=frpc_path)


def _validate_proxy(proxy: Dict[str, Any], existing_names: List[str], skip_name: str = None) -> List[str]:
    """校验代理配置，返回错误列表。"""
    errors = []
    name = proxy.get("name")
    if not name:
        errors.append("name 必填")
    elif not isinstance(name, str):
        errors.append("name 必须是字符串")
    elif name in existing_names and name != skip_name:
        errors.append(f"name 重复: {name}")

    ptype = proxy.get("type", "tcp")
    if ptype not in _VALID_TYPES:
        errors.append(f"type 必须是 {', '.join(_VALID_TYPES)} 之一")

    # local_port 校验：tcp/udp/http/stcp 需要
    local_port = proxy.get("local_port")
    if ptype in ("tcp", "udp", "http", "stcp"):
        if local_port is None:
            errors.append(f"local_port 必填（{ptype} 类型）")
        elif not isinstance(local_port, int) or local_port < 1 or local_port > 65535:
            errors.append("local_port 必须是 1-65535 的整数")

    # remote_port 校验：tcp/udp 需要
    remote_port = proxy.get("remote_port")
    if ptype in ("tcp", "udp"):
        if remote_port is None:
            errors.append(f"remote_port 必填（{ptype} 类型）")
        elif not isinstance(remote_port, int) or remote_port < 1 or remote_port > 65535:
            errors.append("remote_port 必须是 1-65535 的整数")

    # HTTP 代理校验
    if ptype == "http":
        custom_domains = proxy.get("custom_domains") or []
        subdomain = proxy.get("subdomain") or ""
        if not custom_domains and not subdomain:
            errors.append("http 类型至少需要 custom_domains 或 subdomain 之一")
        if custom_domains:
            if not isinstance(custom_domains, list):
                errors.append("custom_domains 必须是数组")
            else:
                for d in custom_domains:
                    if not isinstance(d, str) or not d:
                        errors.append("custom_domains 每项必须是非空字符串")

    # STCP 提供方校验
    if ptype == "stcp":
        sk = proxy.get("sk")
        if not sk:
            errors.append("sk 必填（stcp 类型）")
        elif not isinstance(sk, str):
            errors.append("sk 必须是字符串")

    # STCP 访问方校验
    if ptype == "stcp_visitor":
        sk = proxy.get("sk")
        if not sk:
            errors.append("sk 必填（stcp_visitor 类型）")
        elif not isinstance(sk, str):
            errors.append("sk 必须是字符串")

        server_name = proxy.get("server_name")
        if not server_name:
            errors.append("server_name 必填（stcp_visitor 类型）")
        elif not isinstance(server_name, str):
            errors.append("server_name 必须是字符串")

        bind_port = proxy.get("bind_port")
        if bind_port is None:
            errors.append("bind_port 必填（stcp_visitor 类型）")
        elif not isinstance(bind_port, int) or bind_port < 1 or bind_port > 65535:
            errors.append("bind_port 必须是 1-65535 的整数")

    return errors


@router.get("", response_model=ProxyListResponse)
async def list_proxies(request: Request):
    """获取代理列表。"""
    proxies = _load_proxies(request)
    return ProxyListResponse(proxies=proxies, total=len(proxies))


@router.get("/{name}")
async def get_proxy(name: str, request: Request):
    """获取单个代理配置。"""
    proxies = _load_proxies(request)
    for p in proxies:
        if p.get("name") == name:
            return p
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"代理不存在: {name}")


@router.post("", response_model=ProxySaveResponse, status_code=status.HTTP_201_CREATED)
async def create_proxy(proxy: ProxyCreateRequest, request: Request, _=Depends(require_admin)):
    """新增代理。"""
    proxies = _load_proxies(request)
    existing_names = [p.get("name") for p in proxies]

    proxy_dict = proxy.model_dump(exclude_none=False)
    errors = _validate_proxy(proxy_dict, existing_names)
    if errors:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="; ".join(errors))

    proxies.append(proxy_dict)
    _save_proxies(request, proxies)
    return ProxySaveResponse(message="代理已添加", need_restart=True, proxy=proxy_dict)


@router.put("/{name}", response_model=ProxySaveResponse)
async def update_proxy(name: str, proxy: ProxyUpdateRequest, request: Request, _=Depends(require_admin)):
    """更新代理（按 name 查找，合并字段）。"""
    proxies = _load_proxies(request)
    existing_names = [p.get("name") for p in proxies]

    idx = None
    for i, p in enumerate(proxies):
        if p.get("name") == name:
            idx = i
            break

    if idx is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"代理不存在: {name}")

    update_data = proxy.model_dump(exclude_unset=True)
    updated = {**proxies[idx], **update_data}

    errors = _validate_proxy(updated, existing_names, skip_name=name)
    if errors:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="; ".join(errors))

    proxies[idx] = updated
    _save_proxies(request, proxies)
    return ProxySaveResponse(message="代理已更新", need_restart=True, proxy=updated)


@router.delete("/{name}", response_model=ProxySaveResponse)
async def delete_proxy(name: str, request: Request, _=Depends(require_admin)):
    """删除代理。"""
    proxies = _load_proxies(request)
    new_proxies = [p for p in proxies if p.get("name") != name]

    if len(new_proxies) == len(proxies):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"代理不存在: {name}")

    _save_proxies(request, new_proxies)
    return ProxySaveResponse(
        message="代理已删除",
        need_restart=True,
        proxy={"name": name},
    )


@router.post("/{name}/toggle", response_model=ProxySaveResponse)
async def toggle_proxy(name: str, request: Request, _=Depends(require_admin)):
    """切换代理启用/禁用状态。"""
    proxies = _load_proxies(request)

    idx = None
    for i, p in enumerate(proxies):
        if p.get("name") == name:
            idx = i
            break

    if idx is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"代理不存在: {name}")

    current_enabled = proxies[idx].get("enabled", True)
    proxies[idx]["enabled"] = not current_enabled
    _save_proxies(request, proxies)

    new_state = "启用" if proxies[idx]["enabled"] else "禁用"
    return ProxySaveResponse(
        message=f"代理已{new_state}",
        need_restart=True,
        proxy=proxies[idx],
    )
