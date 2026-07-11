import ipaddress


class AccessControl:
    """访问控制：端口白名单 + IP黑白名单"""

    def __init__(self, logger=None):
        self.logger = logger
        self.allow_ports = None
        self.ip_whitelist = None
        self.ip_blacklist = set()

    def configure(self, allow_ports=None, ip_whitelist=None, ip_blacklist=None):
        if allow_ports:
            self.allow_ports = self._parse_port_ranges(allow_ports)
        if ip_whitelist:
            self.ip_whitelist = self._parse_ip_list(ip_whitelist)
        if ip_blacklist:
            self.ip_blacklist = self._parse_ip_list(ip_blacklist)

        if self.logger:
            if self.allow_ports:
                self.logger.info(
                    f"Port whitelist: {len(self.allow_ports)} ranges configured"
                )
            if self.ip_whitelist is not None:
                self.logger.info(
                    f"IP whitelist: {len(self.ip_whitelist)} entries"
                )
            if self.ip_blacklist:
                self.logger.info(
                    f"IP blacklist: {len(self.ip_blacklist)} entries"
                )

    def _parse_port_ranges(self, port_specs):
        """支持 "8080,9000-9100,3000" 格式"""
        ranges = []
        for spec in port_specs.split(","):
            spec = spec.strip()
            if "-" in spec:
                start, end = spec.split("-", 1)
                ranges.append((int(start), int(end)))
            else:
                port = int(spec)
                ranges.append((port, port))
        return ranges

    def _parse_ip_list(self, ip_specs):
        """支持 "192.168.1.0/24,10.0.0.1" 格式"""
        networks = set()
        for spec in ip_specs.split(","):
            spec = spec.strip()
            if not spec:
                continue
            try:
                networks.add(ipaddress.ip_network(spec, strict=False))
            except ValueError:
                if self.logger:
                    self.logger.warning(f"Invalid IP/CIDR: {spec}")
        return networks

    def check_port(self, port):
        if not self.allow_ports:
            return True
        for start, end in self.allow_ports:
            if start <= port <= end:
                return True
        return False

    def check_ip(self, ip_str):
        try:
            ip = ipaddress.ip_address(ip_str)
        except ValueError:
            return False

        if self.ip_blacklist:
            for network in self.ip_blacklist:
                if ip in network:
                    return False

        if self.ip_whitelist is not None:
            for network in self.ip_whitelist:
                if ip in network:
                    return True
            return False

        return True