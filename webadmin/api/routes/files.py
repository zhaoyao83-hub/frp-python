"""文件管理路由 /api/files/*。

仅允许访问配置中指定的根目录，防止路径遍历。
所有操作仅 admin 角色可执行。
"""

from __future__ import annotations

import os
import shutil
from typing import List

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile, status
from fastapi.responses import FileResponse

from ..auth import require_admin
from ..schemas import (
    FileContentResponse,
    FileDeleteRequest,
    FileItem,
    FileListResponse,
    FileMkdirRequest,
    FileRenameRequest,
    FileSaveRequest,
    MessageResponse,
)

router = APIRouter(prefix="/api/files", tags=["files"])

_MAX_TEXT_FILE_SIZE = 2 * 1024 * 1024
_ALLOWED_TEXT_EXT = {
    ".txt", ".json", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".conf",
    ".py", ".js", ".ts", ".tsx", ".jsx", ".html", ".css", ".md",
    ".log", ".env", ".sh",
}


def _get_roots(request: Request) -> List[str]:
    roots = getattr(request.app.state, "file_manager_roots", None)
    if roots is None:
        return []
    return [os.path.abspath(r) for r in roots]


def _root_name_to_path(request: Request, name: str) -> str | None:
    roots_cfg = getattr(request.app.state, "file_manager_roots", []) or []
    for i, root in enumerate(roots_cfg):
        if os.path.basename(os.path.abspath(root)) == name:
            return os.path.abspath(root)
    return None


def _resolve_path(request: Request, rel_path: str) -> str:
    if not rel_path or rel_path == "/":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="根路径为虚拟目录，无法直接操作",
        )
    if ".." in rel_path.replace("\\", "/").split("/"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="非法路径：不允许 ..",
        )
    if rel_path.startswith("/") or rel_path.startswith("\\"):
        rel_path = rel_path[1:]

    parts = rel_path.split("/", 1)
    root_name = parts[0]
    sub_path = parts[1] if len(parts) > 1 else ""

    root_abs = _root_name_to_path(request, root_name)
    if root_abs is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="路径不在允许的范围内",
        )

    candidate = os.path.abspath(os.path.join(root_abs, sub_path))
    if candidate != root_abs and not candidate.startswith(root_abs + os.sep):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="路径不在允许的范围内",
        )
    return candidate


def _to_rel(request: Request, abs_path: str) -> str:
    roots_cfg = getattr(request.app.state, "file_manager_roots", []) or []
    abs_path = os.path.abspath(abs_path)
    for root in roots_cfg:
        root_abs = os.path.abspath(root)
        root_name = os.path.basename(root_abs)
        if abs_path == root_abs:
            return root_name
        if abs_path.startswith(root_abs + os.sep):
            sub = abs_path[len(root_abs) + 1:]
            return f"{root_name}/{sub.replace(os.sep, '/')}"
    return abs_path


@router.get("/list", response_model=FileListResponse, dependencies=[Depends(require_admin)])
async def list_files(request: Request, path: str = ""):
    if not path or path == "/":
        roots_cfg = getattr(request.app.state, "file_manager_roots", []) or []
        items = []
        for root in roots_cfg:
            root_abs = os.path.abspath(root)
            root_name = os.path.basename(root_abs)
            try:
                stat = os.stat(root_abs)
                items.append(FileItem(
                    name=root_name,
                    path=root_name,
                    is_dir=True,
                    size=0,
                    modified_at=stat.st_mtime,
                ))
            except OSError:
                continue
        return FileListResponse(path="", items=items, parent=None)

    abs_path = _resolve_path(request, path)
    if not os.path.isdir(abs_path):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="路径不是目录",
        )

    items = []
    try:
        entries = sorted(os.listdir(abs_path))
    except OSError as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"读取目录失败: {e}",
        )

    for name in entries:
        full = os.path.join(abs_path, name)
        try:
            stat = os.stat(full)
            items.append(FileItem(
                name=name,
                path=_to_rel(request, full),
                is_dir=os.path.isdir(full),
                size=0 if os.path.isdir(full) else stat.st_size,
                modified_at=stat.st_mtime,
            ))
        except OSError:
            continue

    parent = None
    if "/" in path:
        parent = path.rsplit("/", 1)[0]
    elif path:
        parent = ""

    return FileListResponse(path=path, items=items, parent=parent)


@router.get("/content", response_model=FileContentResponse, dependencies=[Depends(require_admin)])
async def get_file_content(request: Request, path: str):
    abs_path = _resolve_path(request, path)
    if not os.path.isfile(abs_path):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="路径不是文件",
        )

    size = os.path.getsize(abs_path)
    if size > _MAX_TEXT_FILE_SIZE:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"文件过大（> {_MAX_TEXT_FILE_SIZE // 1024}KB），请下载查看",
        )

    ext = os.path.splitext(abs_path)[1].lower()
    if ext and ext not in _ALLOWED_TEXT_EXT:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="不支持在线编辑的文件类型",
        )

    try:
        with open(abs_path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
    except OSError as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"读取文件失败: {e}",
        )

    return FileContentResponse(path=path, content=content, size=size)


@router.put("/content", response_model=MessageResponse, dependencies=[Depends(require_admin)])
async def save_file_content(request: Request, req: FileSaveRequest, path: str = ""):
    abs_path = _resolve_path(request, path)
    if not os.path.isfile(abs_path):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="路径不是文件",
        )

    ext = os.path.splitext(abs_path)[1].lower()
    if ext and ext not in _ALLOWED_TEXT_EXT:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="不支持在线编辑的文件类型",
        )

    try:
        with open(abs_path, "w", encoding="utf-8") as f:
            f.write(req.content)
    except OSError as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"写入文件失败: {e}",
        )

    return MessageResponse(message="文件已保存")


@router.get("/download", dependencies=[Depends(require_admin)])
async def download_file(request: Request, path: str):
    abs_path = _resolve_path(request, path)
    if not os.path.isfile(abs_path):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="路径不是文件",
        )
    return FileResponse(abs_path, filename=os.path.basename(abs_path))


@router.delete("/delete", response_model=MessageResponse, dependencies=[Depends(require_admin)])
async def delete_file(request: Request, req: FileDeleteRequest):
    abs_path = _resolve_path(request, req.path)
    if not os.path.exists(abs_path):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="路径不存在",
        )

    try:
        if os.path.isdir(abs_path):
            shutil.rmtree(abs_path)
        else:
            os.remove(abs_path)
    except OSError as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"删除失败: {e}",
        )

    return MessageResponse(message="已删除")


@router.post("/rename", response_model=MessageResponse, dependencies=[Depends(require_admin)])
async def rename_file(request: Request, req: FileRenameRequest):
    abs_path = _resolve_path(request, req.path)
    if not os.path.exists(abs_path):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="路径不存在",
        )
    if not req.new_name or "/" in req.new_name or "\\" in req.new_name:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="非法的新名称",
        )

    parent = os.path.dirname(abs_path)
    new_abs = os.path.join(parent, req.new_name)
    new_rel = _to_rel(request, new_abs)
    _resolve_path(request, new_rel)

    if os.path.exists(new_abs):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="目标已存在",
        )

    try:
        os.rename(abs_path, new_abs)
    except OSError as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"重命名失败: {e}",
        )

    return MessageResponse(message="已重命名")


@router.post("/mkdir", response_model=MessageResponse, dependencies=[Depends(require_admin)])
async def make_dir(request: Request, req: FileMkdirRequest):
    abs_path = _resolve_path(request, req.path)
    if not os.path.isdir(abs_path):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="路径不是目录",
        )
    if not req.dir_name or "/" in req.dir_name or "\\" in req.dir_name:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="非法的目录名",
        )

    new_abs = os.path.join(abs_path, req.dir_name)
    if os.path.exists(new_abs):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="目录已存在",
        )

    try:
        os.mkdir(new_abs)
    except OSError as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"创建目录失败: {e}",
        )

    return MessageResponse(message="目录已创建")


@router.post("/upload", response_model=MessageResponse, dependencies=[Depends(require_admin)])
async def upload_file(request: Request, path: str = Form(""), file: UploadFile = File(...)):
    abs_path = _resolve_path(request, path)
    if not os.path.isdir(abs_path):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="路径不是目录",
        )

    filename = file.filename or "upload"
    if "/" in filename or "\\" in filename:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="非法的文件名",
        )

    dest = os.path.join(abs_path, filename)

    try:
        with open(dest, "wb") as f:
            while True:
                chunk = await file.read(8192)
                if not chunk:
                    break
                f.write(chunk)
    except OSError as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"上传失败: {e}",
        )

    return MessageResponse(message=f"已上传: {filename}")
