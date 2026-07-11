"""环形日志缓冲。

使用 deque 存储历史日志行，配合 asyncio.Queue 实现订阅广播。
所有操作均为协程安全（单线程 asyncio 事件循环内调用）。
"""

from __future__ import annotations

import asyncio
from collections import deque
from typing import AsyncIterator, Deque, Set


class LogBuffer:
    """环形日志缓冲，支持历史查询与订阅推送。"""

    def __init__(self, max_lines: int = 1000) -> None:
        self._buffer: Deque[str] = deque(maxlen=max_lines)
        self._subscribers: Set[asyncio.Queue] = set()

    def append(self, line: str) -> None:
        """追加一行日志，并广播给所有订阅者。"""
        if not line.endswith("\n"):
            display = line
        else:
            display = line.rstrip("\n")
        self._buffer.append(display)
        # 将行推送给所有订阅者队列
        for queue in list(self._subscribers):
            try:
                queue.put_nowait(display)
            except asyncio.QueueFull:
                # 队列满则丢弃，避免阻塞生产者
                pass

    def get_history(self) -> list:
        """返回历史日志列表（按时间顺序）。"""
        return list(self._buffer)

    def clear(self) -> None:
        """清空历史日志缓冲。"""
        self._buffer.clear()

    async def subscribe(self) -> AsyncIterator[str]:
        """订阅新日志行的异步生成器。

        调用方：async for line in buffer.subscribe(): ...
        退出时自动取消订阅。
        """
        queue: asyncio.Queue = asyncio.Queue()
        self._subscribers.add(queue)
        try:
            while True:
                line = await queue.get()
                yield line
        finally:
            self._subscribers.discard(queue)

    @property
    def subscriber_count(self) -> int:
        """当前订阅者数量。"""
        return len(self._subscribers)
