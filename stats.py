import time
import threading
from collections import defaultdict


class Stats:
    """流量统计与监控"""

    def __init__(self):
        self._lock = threading.Lock()
        self.start_time = time.time()

        self.total_connections = 0
        self.current_connections = 0
        self.total_bytes_in = 0
        self.total_bytes_out = 0
        self.total_proxies = 0

        self.proxy_stats = defaultdict(lambda: {
            "type": "",
            "remote_port": 0,
            "connections": 0,
            "current_conns": 0,
            "bytes_in": 0,
            "bytes_out": 0,
            "created_at": 0,
        })

        self.conn_stats = {}

    def on_connect(self, proxy_name, conn_id, proxy_type, remote_port):
        with self._lock:
            self.total_connections += 1
            self.current_connections += 1
            self.proxy_stats[proxy_name]["connections"] += 1
            self.proxy_stats[proxy_name]["current_conns"] += 1
            self.proxy_stats[proxy_name]["type"] = proxy_type
            self.proxy_stats[proxy_name]["remote_port"] = remote_port
            self.conn_stats[conn_id] = {
                "proxy_name": proxy_name,
                "bytes_in": 0,
                "bytes_out": 0,
                "created_at": time.time(),
            }

    def on_disconnect(self, conn_id):
        with self._lock:
            self.current_connections = max(0, self.current_connections - 1)
            conn_info = self.conn_stats.pop(conn_id, None)
            if conn_info:
                proxy_name = conn_info["proxy_name"]
                self.proxy_stats[proxy_name]["current_conns"] = max(
                    0, self.proxy_stats[proxy_name]["current_conns"] - 1
                )

    def on_data_in(self, conn_id, size):
        with self._lock:
            self.total_bytes_in += size
            conn_info = self.conn_stats.get(conn_id)
            if conn_info:
                conn_info["bytes_in"] += size
                self.proxy_stats[conn_info["proxy_name"]]["bytes_in"] += size

    def on_data_out(self, conn_id, size):
        with self._lock:
            self.total_bytes_out += size
            conn_info = self.conn_stats.get(conn_id)
            if conn_info:
                conn_info["bytes_out"] += size
                self.proxy_stats[conn_info["proxy_name"]]["bytes_out"] += size

    def on_proxy_register(self, proxy_name, proxy_type, remote_port):
        with self._lock:
            self.total_proxies += 1
            self.proxy_stats[proxy_name]["type"] = proxy_type
            self.proxy_stats[proxy_name]["remote_port"] = remote_port
            self.proxy_stats[proxy_name]["created_at"] = time.time()

    def on_proxy_unregister(self, proxy_name):
        with self._lock:
            self.total_proxies = max(0, self.total_proxies - 1)

    def get_summary(self):
        with self._lock:
            uptime = int(time.time() - self.start_time)
            return {
                "uptime": uptime,
                "total_connections": self.total_connections,
                "current_connections": self.current_connections,
                "total_proxies": self.total_proxies,
                "total_bytes_in": self.total_bytes_in,
                "total_bytes_out": self.total_bytes_out,
            }

    def get_proxy_stats(self):
        with self._lock:
            return dict(self.proxy_stats)

    def get_all_stats(self):
        with self._lock:
            return {
                "summary": {
                    "uptime": int(time.time() - self.start_time),
                    "total_connections": self.total_connections,
                    "current_connections": self.current_connections,
                    "total_proxies": self.total_proxies,
                    "total_bytes_in": self.total_bytes_in,
                    "total_bytes_out": self.total_bytes_out,
                },
                "proxies": {k: dict(v) for k, v in self.proxy_stats.items()},
            }