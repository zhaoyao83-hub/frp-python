"""子进程管理：启停 frps/frpc。

使用 asyncio.create_subprocess_exec 启动子进程，实时读取 stdout/stderr 写入日志缓冲，
支持自动重启与优雅退出。
"""

from __future__ import annotations

import asyncio
import os
import signal
import subprocess
import sys
import time
from typing import Dict, List, Optional

from .log_buffer import LogBuffer

# 项目根目录（webadmin/api/service_manager.py 的上三级）
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class ServiceManager:
    """单个服务（frps 或 frpc）的进程管理器。"""

    def __init__(
        self,
        name: str,
        cmd_args: List[str],
        config_path: str,
        log_buffer: LogBuffer,
        auto_restart: bool = False,
        cwd: Optional[str] = None,
    ) -> None:
        self.name = name
        self.cmd_args = cmd_args  # 如 ["python", "frps.py", "-c", "frps.json"]
        self.config_path = config_path
        self.log_buffer = log_buffer
        self.auto_restart = auto_restart
        self.cwd = cwd or PROJECT_ROOT

        self.process: Optional[asyncio.subprocess.Process] = None
        self.start_time: Optional[float] = None
        self.restart_count = 0
        self.exit_code: Optional[int] = None
        # 标记是否为主动停止（避免主动停止后触发 auto_restart）
        self._stopping = False
        # 后台任务引用
        self._tasks: List[asyncio.Task] = []

    def _find_external_processes(self) -> List[Dict]:
        """查找使用相同配置文件的外部进程（非当前管理的进程）。

        通过 ps 命令匹配脚本名 + 配置路径来检测。
        返回列表：[{"pid": int, "cmd": str}]
        """
        results = []
        try:
            script_name = os.path.basename(self.cmd_args[1]) if len(self.cmd_args) > 1 else ""
            config_abs = os.path.abspath(os.path.join(self.cwd, self.config_path))
            config_base = os.path.basename(config_abs)

            cmd = ["ps", "-eo", "pid=,command="]
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=3)
            if proc.returncode != 0:
                return []

            current_pid = self.process.pid if self.process and self.process.returncode is None else None

            for line in proc.stdout.strip().split("\n"):
                line = line.strip()
                if not line:
                    continue
                parts = line.split(None, 1)
                if len(parts) < 2:
                    continue
                try:
                    pid = int(parts[0])
                except ValueError:
                    continue
                cmd_str = parts[1]
                if pid == current_pid:
                    continue
                if script_name and script_name not in cmd_str:
                    continue
                if config_base and config_base not in cmd_str:
                    continue
                if "python" in cmd_str.lower() or "python3" in cmd_str.lower():
                    results.append({"pid": pid, "cmd": cmd_str})
            return results
        except Exception:
            return []

    @property
    def running(self) -> bool:
        """进程是否在运行（仅指当前 ServiceManager 管理的进程）。"""
        return self.process is not None and self.process.returncode is None

    def status(self) -> Dict:
        """返回服务状态字典。"""
        uptime = 0
        if self.running and self.start_time:
            uptime = int(time.time() - self.start_time)

        external_procs = self._find_external_processes()
        has_external = len(external_procs) > 0

        return {
            "name": self.name,
            "running": self.running,
            "pid": self.process.pid if self.running else None,
            "uptime": uptime,
            "restart_count": self.restart_count,
            "exit_code": self.exit_code,
            "has_external_process": has_external,
            "external_pids": [p["pid"] for p in external_procs],
        }

    async def kill_external(self) -> Dict:
        """杀掉检测到的外部遗留进程。"""
        external_procs = self._find_external_processes()
        if not external_procs:
            return {"killed": 0, "message": "未检测到外部进程"}
        killed = 0
        for p in external_procs:
            pid = p["pid"]
            try:
                os.kill(pid, signal.SIGTERM)
                killed += 1
                self.log_buffer.append(f"[{self.name}] 已终止外部遗留进程 pid={pid}")
            except ProcessLookupError:
                pass
            except Exception as e:
                self.log_buffer.append(f"[{self.name}] 终止外部进程 pid={pid} 失败: {e}")
        return {"killed": killed, "message": f"已终止 {killed} 个外部进程"}

    async def start(self, kill_existing: bool = False) -> Dict:
        """启动服务。

        Args:
            kill_existing: 若检测到外部遗留进程，是否先杀掉再启动。
        """
        if self.running:
            raise RuntimeError(f"{self.name} 已在运行")

        external_procs = self._find_external_processes()
        if external_procs:
            pids = [str(p["pid"]) for p in external_procs]
            if kill_existing:
                for p in external_procs:
                    try:
                        os.kill(p["pid"], signal.SIGTERM)
                        self.log_buffer.append(f"[{self.name}] 已终止遗留进程 pid={p['pid']}")
                    except ProcessLookupError:
                        pass
                await asyncio.sleep(0.5)
            else:
                raise RuntimeError(
                    f"检测到 {len(external_procs)} 个使用相同配置的遗留进程（pid={', '.join(pids)}），"
                    f"请先停止这些进程或使用 kill_existing=true 强制清理后启动"
                )

        self._stopping = False
        self.log_buffer.append(f"[{self.name}] 正在启动: {' '.join(self.cmd_args)}")
        try:
            self.process = await asyncio.create_subprocess_exec(
                *self.cmd_args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                cwd=self.cwd,
            )
        except Exception as e:
            self.log_buffer.append(f"[{self.name}] 启动失败: {e}")
            raise

        self.start_time = time.time()
        self.exit_code = None
        self.log_buffer.append(f"[{self.name}] 已启动 pid={self.process.pid}")

        # 启动后台读取与等待任务
        self._tasks = [
            asyncio.create_task(self._read_output()),
            asyncio.create_task(self._wait_exit()),
        ]
        return {"name": self.name, "pid": self.process.pid}

    async def _read_output(self) -> None:
        """逐行读取子进程 stdout（含 stderr 合并），写入日志缓冲。"""
        if not self.process or not self.process.stdout:
            return
        try:
            while True:
                line_bytes = await self.process.stdout.readline()
                if not line_bytes:
                    # EOF
                    break
                line = line_bytes.decode("utf-8", errors="replace").rstrip("\n")
                if line:
                    self.log_buffer.append(f"[{self.name}] {line}")
        except Exception as e:
            self.log_buffer.append(f"[{self.name}] 读取输出异常: {e}")

    async def _wait_exit(self) -> None:
        """等待子进程退出，记录退出码；按需自动重启。"""
        if not self.process:
            return
        try:
            await self.process.wait()
        except Exception:
            pass
        self.exit_code = self.process.returncode
        self.log_buffer.append(f"[{self.name}] 进程退出 code={self.exit_code}")

        # 主动停止不重启
        if self._stopping:
            return
        # auto_restart 且异常退出（非 0）则自动拉起
        if self.auto_restart and self.exit_code != 0:
            self.restart_count += 1
            self.log_buffer.append(
                f"[{self.name}] 异常退出，1 秒后自动重启（第 {self.restart_count} 次）"
            )
            await asyncio.sleep(1)
            try:
                await self.start()
            except Exception as e:
                self.log_buffer.append(f"[{self.name}] 自动重启失败: {e}")

    async def stop(self, timeout: float = 5) -> Dict:
        """停止服务：terminate -> wait -> kill。"""
        if not self.process:
            return {"name": self.name, "message": "未运行"}

        self._stopping = True
        proc = self.process
        try:
            proc.terminate()
        except ProcessLookupError:
            # 进程已退出
            pass
        try:
            await asyncio.wait_for(proc.wait(), timeout)
        except asyncio.TimeoutError:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            try:
                await proc.wait()
            except Exception:
                pass
            self.log_buffer.append(f"[{self.name}] 已强制终止")
        else:
            self.log_buffer.append(f"[{self.name}] 已停止")
        self.exit_code = proc.returncode
        # 清理后台任务
        await self._cleanup_tasks()
        return {"name": self.name, "message": "已停止"}

    async def restart(self) -> Dict:
        """重启服务：先停止再启动。"""
        # 保留原 auto_restart 设置
        original_auto_restart = self.auto_restart
        if self.running:
            await self.stop()
        self.auto_restart = original_auto_restart
        result = await self.start()
        return {"name": self.name, "pid": result.get("pid"), "message": "已重启"}

    async def _cleanup_tasks(self) -> None:
        """清理后台任务。"""
        for task in self._tasks:
            if not task.done():
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass
        self._tasks = []

    async def shutdown(self) -> None:
        """dashboard 退出时优雅终止子进程。"""
        if self.running:
            # 关闭时不自动重启
            self.auto_restart = False
            await self.stop(timeout=3)
        await self._cleanup_tasks()


class ServiceRegistry:
    """管理 frps/frpc 两个 ServiceManager 实例的注册表。"""

    def __init__(self) -> None:
        self._managers: Dict[str, ServiceManager] = {}

    def register(self, name: str, manager: ServiceManager) -> None:
        """注册服务管理器。"""
        self._managers[name] = manager

    def get(self, name: str) -> Optional[ServiceManager]:
        """获取服务管理器。"""
        return self._managers.get(name)

    def status_all(self) -> List[Dict]:
        """返回所有服务状态。"""
        return [m.status() for m in self._managers.values()]

    async def shutdown_all(self) -> None:
        """优雅终止所有子进程（dashboard 退出时调用）。"""
        for manager in self._managers.values():
            try:
                await manager.shutdown()
            except Exception:
                pass


def build_default_cmd(name: str, config_path: str) -> List[str]:
    """构建默认启动命令。"""
    # 使用当前 Python 解释器，确保环境一致
    python_bin = sys.executable or "python"
    script = "frps.py" if name == "frps" else "frpc.py"
    return [python_bin, script, "-c", config_path]
