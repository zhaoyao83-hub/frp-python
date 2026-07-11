import json
import os


class Config:
    def __init__(self, config_type="server"):
        self.config_type = config_type
        self.defaults = {
            "server": {
                "bind_port": 7000,
                "bind_addr": "0.0.0.0",
                "log_level": "info",
                "log_file": None,
                "auth_token": None,
                "max_connections": 1000,
                "idle_timeout": 300,
                "tls": False,
                "tls_cert_file": None,
                "tls_key_file": None,
                "webapi_port": None,
                "webapi_addr": "0.0.0.0",
                "allow_ports": None,
                "ip_whitelist": None,
                "ip_blacklist": None,
                "data_port": None,
                "vhost_http_port": None,
                "subdomain_host": None,
                "forward_proxy_port": None,
                "forward_proxy_user": None,
                "forward_proxy_pass": None,
            },
            "client": {
                "server_addr": "127.0.0.1",
                "server_port": 7000,
                "log_level": "info",
                "log_file": None,
                "auth_token": None,
                "reconnect": True,
                "reconnect_max_retries": 0,
                "reconnect_base_delay": 1,
                "reconnect_max_delay": 60,
                "tls": False,
                "tls_insecure": False,
                "tls_ca_file": None,
                "data_port": None,
                "proxies": [],
            },
        }
        self.config = self.defaults[config_type].copy()

    def load_from_file(self, filepath):
        if os.path.exists(filepath):
            with open(filepath, "r") as f:
                data = json.load(f)
                self.config.update(data)
        return self

    def get(self, key, default=None):
        return self.config.get(key, default)

    def __getitem__(self, key):
        return self.config[key]

    def __setitem__(self, key, value):
        self.config[key] = value

    def __contains__(self, key):
        return key in self.config