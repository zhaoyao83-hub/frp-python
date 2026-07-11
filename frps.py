import asyncio
import ssl
import signal
import socket
import time
import json as json_module
from config import Config
from protocol import Protocol, Message, MessageType
from log import get_logger
from stats import Stats
from access import AccessControl
from forward_proxy import ForwardProxy

# Zero-copy / performance tuning constants
READ_BUF_SIZE = 65536  # 64KB read buffer (reduces syscalls vs 4KB)


def _optimize_socket(writer):
    """Set TCP_NODELAY and larger buffers on a connection's socket."""
    sock = writer.get_extra_info("socket")
    if sock is None:
        return
    try:
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 256 * 1024)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 256 * 1024)
    except (OSError, AttributeError):
        pass


class FRPServer:
    def __init__(self, config):
        self.config = config
        self.logger = get_logger(
            "frps", config.get("log_level"), config.get("log_file")
        )
        self.stats = Stats()
        self.access = AccessControl(self.logger)

        self.proxies = {}
        self.conn_pool = {}
        self.total_connections = 0

        self.control_server = None
        self.webapi_server = None
        self.data_server = None
        self.proxy_servers = {}

        self.auth_token = config.get("auth_token")
        self.max_connections = config.get("max_connections", 1000)
        self.idle_timeout = config.get("idle_timeout", 300)
        self.tls_enabled = config.get("tls", False)
        self.tls_cert_file = config.get("tls_cert_file")
        self.tls_key_file = config.get("tls_key_file")
        self.webapi_port = config.get("webapi_port")
        self.webapi_addr = config.get("webapi_addr", "0.0.0.0")
        self.data_port = config.get("data_port")
        self.vhost_http_port = config.get("vhost_http_port")
        self.subdomain_host = config.get("subdomain_host")
        self.forward_proxy_port = config.get("forward_proxy_port")
        self.forward_proxy_user = config.get("forward_proxy_user")
        self.forward_proxy_pass = config.get("forward_proxy_pass")

        self.sessions = {}  # session_id -> {"control_writer", "data_writer", "proxies"}
        self.stcp_providers = {}  # proxy_name -> {"sk", "session_id", "control_writer"}
        self.stcp_visitors = {}  # proxy_name -> {"sk", "bind_port", "session_id", "visitor_writers"}
        self.stcp_conns = {}  # visitor_conn_id -> {"proxy_name", "provider_writer", "visitor_writer", ...}
        self.http_proxies = {}  # proxy_name -> {"custom_domains": [...], "subdomain": "...", "session_id", "control_writer"}
        self.vhost_http_server = None
        self.forward_proxy_server = None
        self.forward_proxy = None
        self.ftp_data_ports = {}  # data_conn_id -> {"proxy_name", "server", "remote_port", "local_port", "ready_event", "proxy_reader", "proxy_writer"}

        self._ssl_ctx = None
        self._stop_event = asyncio.Event()

    def _init_ssl(self):
        if not self.tls_enabled:
            return None
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(self.tls_cert_file, self.tls_key_file)
        self.logger.info("TLS enabled for control connections")
        return ctx

    def _init_access_control(self):
        self.access.configure(
            allow_ports=self.config.get("allow_ports"),
            ip_whitelist=self.config.get("ip_whitelist"),
            ip_blacklist=self.config.get("ip_blacklist"),
        )

    async def start(self):
        self._ssl_ctx = self._init_ssl()
        self._init_access_control()

        bind_addr = self.config.get("bind_addr", "0.0.0.0")
        bind_port = self.config.get("bind_port", 7000)

        self.control_server = await asyncio.start_server(
            self.handle_control_conn, bind_addr, bind_port,
            ssl=self._ssl_ctx,
        )

        self.logger.info(f"FRPServer started on {bind_addr}:{bind_port}")
        if self.auth_token:
            self.logger.info("Token authentication enabled")
        if self.webapi_port:
            self.webapi_server = await asyncio.start_server(
                self.handle_webapi, self.webapi_addr, self.webapi_port
            )
            self.logger.info(
                f"WebAPI on http://{self.webapi_addr}:{self.webapi_port}"
            )
        if self.data_port:
            self.data_server = await asyncio.start_server(
                self.handle_data_conn, bind_addr, self.data_port,
                ssl=self._ssl_ctx,
            )
            self.logger.info(f"Data channel on {bind_addr}:{self.data_port}")
        if self.vhost_http_port:
            self.vhost_http_server = await asyncio.start_server(
                self.handle_http_vhost_conn, bind_addr, self.vhost_http_port,
            )
            self.logger.info(
                f"HTTP vhost on {bind_addr}:{self.vhost_http_port}"
            )
        if self.forward_proxy_port:
            self.forward_proxy = ForwardProxy(
                access_control=self.access,
                auth_user=self.forward_proxy_user,
                auth_pass=self.forward_proxy_pass,
            )
            self.forward_proxy_server = await asyncio.start_server(
                self.forward_proxy.handle_conn, bind_addr, self.forward_proxy_port,
            )
            auth_info = ""
            if self.forward_proxy_user:
                auth_info = " (auth: on)"
            self.logger.info(
                f"Forward proxy on {bind_addr}:{self.forward_proxy_port}{auth_info}"
            )

        cleanup_task = asyncio.create_task(self.idle_cleanup_loop())

        loop = asyncio.get_event_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, self._handle_signal)
            except NotImplementedError:
                pass

        try:
            if self.webapi_server:
                asyncio.create_task(self.webapi_server.serve_forever())
            async with self.control_server:
                await self.control_server.serve_forever()
        except asyncio.CancelledError:
            pass
        finally:
            cleanup_task.cancel()
            await self._cleanup_all()

    def _handle_signal(self):
        self.logger.info("Received shutdown signal, stopping...")
        self._stop_event.set()
        if self.control_server:
            self.control_server.close()
        if self.webapi_server:
            self.webapi_server.close()
        if self.data_server:
            self.data_server.close()
        if self.vhost_http_server:
            self.vhost_http_server.close()
        if self.forward_proxy_server:
            self.forward_proxy_server.close()

    async def stop(self):
        """Stop the server programmatically."""
        self.logger.info("Stopping FRPServer...")
        self._stop_event.set()
        if self.control_server:
            self.control_server.close()
            await self.control_server.wait_closed()
        if self.webapi_server:
            self.webapi_server.close()
            await self.webapi_server.wait_closed()
        if self.data_server:
            self.data_server.close()
            await self.data_server.wait_closed()
        if self.vhost_http_server:
            self.vhost_http_server.close()
            await self.vhost_http_server.wait_closed()
        if self.forward_proxy_server:
            self.forward_proxy_server.close()
            await self.forward_proxy_server.wait_closed()

    async def _cleanup_all(self):
        for proxy_name in list(self.proxies.keys()):
            self._remove_proxy(proxy_name)
        for conn_id in list(self.conn_pool.keys()):
            self.close_conn(conn_id)
        self.logger.info("All resources cleaned up")

    async def handle_control_conn(self, reader, writer):
        addr = writer.get_extra_info("peername")
        ip = addr[0] if addr else "unknown"
        _optimize_socket(writer)
        self.logger.info(f"New control connection from {addr}")

        if not self.access.check_ip(ip):
            self.logger.warning(f"IP {ip} blocked by access control")
            writer.close()
            await writer.wait_closed()
            return

        if self.total_connections >= self.max_connections:
            self.logger.warning(f"Connection limit reached, rejecting {addr}")
            error_msg = Message(MessageType.ERROR, message="Connection limit reached")
            try:
                writer.write(Protocol.encode(error_msg))
                await writer.drain()
            except:
                pass
            writer.close()
            await writer.wait_closed()
            return

        self.total_connections += 1
        buffer = b""
        authenticated = not self.auth_token
        session = None

        try:
            while not self._stop_event.is_set():
                try:
                    data = await asyncio.wait_for(
                        reader.read(READ_BUF_SIZE), timeout=self.idle_timeout
                    )
                except asyncio.TimeoutError:
                    self.logger.info(f"Control connection idle timeout: {addr}")
                    break

                if not data:
                    break

                buffer += data

                while True:
                    message, buffer = Protocol.decode(buffer)
                    if not message:
                        break

                    if not authenticated:
                        if message.type == MessageType.LOGIN:
                            authenticated, session = await self.handle_login(
                                message, writer, addr
                            )
                            if not authenticated:
                                return
                        else:
                            self.logger.warning(
                                f"Unauthorized message from {addr}: {message.type}"
                            )
                            error_msg = Message(
                                MessageType.ERROR, message="Authentication required"
                            )
                            writer.write(Protocol.encode(error_msg))
                            await writer.drain()
                            return
                    else:
                        await self.process_message(message, writer, addr, session)

        except asyncio.CancelledError:
            pass
        except ConnectionResetError:
            self.logger.info(f"Connection reset by {addr}")
        except Exception as e:
            self.logger.error(f"Control connection error: {e}")
        finally:
            self.total_connections -= 1
            if session:
                self._remove_session(session)
            self.cleanup_client_by_writer(writer)
            try:
                writer.close()
                await writer.wait_closed()
            except:
                pass
            self.logger.info(f"Control connection closed: {addr}")

    async def handle_login(self, message, writer, addr):
        token = message.payload.get("token", "")
        if token == self.auth_token:
            import uuid as _uuid
            session_id = str(_uuid.uuid4())
            session = {
                "id": session_id,
                "control_writer": writer,
                "data_writer": None,
                "addr": addr,
                "proxies": set(),
            }
            self.sessions[session_id] = session
            resp = Message(
                MessageType.LOGIN_RESP, status="ok", session_id=session_id,
                data_port=self.data_port,
            )
            writer.write(Protocol.encode(resp))
            await writer.drain()
            self.logger.info(f"Client authenticated: {addr} (session={session_id[:8]})")
            return True, session
        else:
            resp = Message(
                MessageType.LOGIN_RESP, status="error", message="Invalid token"
            )
            writer.write(Protocol.encode(resp))
            await writer.drain()
            self.logger.warning(f"Authentication failed from {addr}")
            return False, None

    async def process_message(self, message, writer, addr, session=None):
        try:
            if message.type == MessageType.REGISTER:
                await self.handle_register(message, writer, addr, session)
            elif message.type == MessageType.INIT_CONN:
                await self.handle_init_conn(message, writer, session)
            elif message.type == MessageType.PING:
                await self.handle_ping(writer)
            elif message.type == MessageType.DATA:
                await self.handle_data(message, writer)
            elif message.type == MessageType.CLOSE:
                await self.handle_close(message)
            elif message.type == MessageType.STCP_NEW_VISITOR:
                await self.handle_stcp_new_visitor(message, writer, session)
            elif message.type == MessageType.STCP_VISITOR_READY:
                await self.handle_stcp_visitor_ready(message, writer, session)
            elif message.type == MessageType.FTP_DATA_READY:
                await self.handle_ftp_data_ready(message, writer, session)
            else:
                self.logger.warning(f"Unknown message type: {message.type}")
        except Exception as e:
            self.logger.error(f"Error processing {message.type}: {e}")

    async def handle_register(self, message, writer, addr, session=None):
        proxy_name = message.payload.get("proxy_name")
        proxy_type = message.payload.get("proxy_type", "tcp")
        remote_port = message.payload.get("remote_port")

        if not proxy_name:
            error_msg = Message(MessageType.ERROR, message="Missing proxy_name")
            writer.write(Protocol.encode(error_msg))
            await writer.drain()
            return

        # HTTP and STCP types don't need remote_port
        if proxy_type in ("tcp", "udp", "ftp"):
            if not remote_port:
                error_msg = Message(MessageType.ERROR, message="Missing remote_port")
                writer.write(Protocol.encode(error_msg))
                await writer.drain()
                return

            if not self.access.check_port(remote_port):
                error_msg = Message(
                    MessageType.ERROR,
                    message=f"Port {remote_port} not allowed",
                )
                writer.write(Protocol.encode(error_msg))
                await writer.drain()
                self.logger.warning(
                    f"Port {remote_port} rejected by access control for {addr}"
                )
                return

        if proxy_name in self.proxies or proxy_name in self.http_proxies or proxy_name in self.stcp_providers:
            error_msg = Message(
                MessageType.ERROR, message=f"Proxy {proxy_name} already registered"
            )
            writer.write(Protocol.encode(error_msg))
            await writer.drain()
            return

        if proxy_type == "tcp":
            server_socket = await self.start_tcp_proxy(proxy_name, remote_port)
            if server_socket:
                self.proxies[proxy_name] = {
                    "type": proxy_type,
                    "remote_port": remote_port,
                    "control_writer": writer,
                    "server_socket": server_socket,
                    "client_addr": addr,
                    "session_id": session["id"] if session else None,
                    "created_at": time.time(),
                    "last_activity": time.time(),
                }
                if session:
                    session["proxies"].add(proxy_name)
                self.stats.on_proxy_register(proxy_name, proxy_type, remote_port)
                self.logger.info(
                    f"Proxy registered: {proxy_name} ({proxy_type}) port={remote_port}"
                )
        elif proxy_type == "udp":
            server_socket = await self.start_udp_proxy(proxy_name, remote_port)
            if server_socket:
                self.proxies[proxy_name] = {
                    "type": proxy_type,
                    "remote_port": remote_port,
                    "control_writer": writer,
                    "server_socket": server_socket,
                    "client_addr": addr,
                    "session_id": session["id"] if session else None,
                    "created_at": time.time(),
                    "last_activity": time.time(),
                }
                if session:
                    session["proxies"].add(proxy_name)
                self.stats.on_proxy_register(proxy_name, proxy_type, remote_port)
                self.logger.info(
                    f"Proxy registered: {proxy_name} ({proxy_type}) port={remote_port}"
                )
        elif proxy_type == "ftp":
            server_socket = await self.start_ftp_proxy(proxy_name, remote_port)
            if server_socket:
                self.proxies[proxy_name] = {
                    "type": proxy_type,
                    "remote_port": remote_port,
                    "control_writer": writer,
                    "server_socket": server_socket,
                    "client_addr": addr,
                    "session_id": session["id"] if session else None,
                    "created_at": time.time(),
                    "last_activity": time.time(),
                }
                if session:
                    session["proxies"].add(proxy_name)
                self.stats.on_proxy_register(proxy_name, proxy_type, remote_port)
                self.logger.info(
                    f"Proxy registered: {proxy_name} ({proxy_type}) port={remote_port}"
                )
        elif proxy_type == "http":
            if not self.vhost_http_port:
                error_msg = Message(
                    MessageType.ERROR,
                    message="HTTP proxy not enabled on server (vhost_http_port not configured)",
                )
                writer.write(Protocol.encode(error_msg))
                await writer.drain()
                return
            ok, msg = self._register_http_proxy(message, writer, addr, session)
            if not ok:
                error_msg = Message(MessageType.ERROR, message=msg)
                writer.write(Protocol.encode(error_msg))
                await writer.drain()
        elif proxy_type == "stcp":
            ok, msg = self._register_stcp_proxy(message, writer, addr, session)
            if not ok:
                error_msg = Message(MessageType.ERROR, message=msg)
                writer.write(Protocol.encode(error_msg))
                await writer.drain()
        elif proxy_type == "stcp_visitor":
            ok, msg = self._register_stcp_visitor(message, writer, addr, session)
            if not ok:
                error_msg = Message(MessageType.ERROR, message=msg)
                writer.write(Protocol.encode(error_msg))
                await writer.drain()
        else:
            error_msg = Message(
                MessageType.ERROR, message=f"Unsupported type: {proxy_type}"
            )
            writer.write(Protocol.encode(error_msg))
            await writer.drain()

    async def handle_init_conn(self, message, writer, session=None):
        conn_id = message.payload.get("conn_id")
        conn_info = self.conn_pool.get(conn_id)
        if conn_info:
            # Use data channel writer if available, else fall back to control writer
            if session and session.get("data_writer"):
                conn_info["client_writer"] = session["data_writer"]
            else:
                conn_info["client_writer"] = writer
            conn_info["client_ready"].set()
            conn_info["last_activity"] = time.time()
        else:
            self.logger.warning(f"Connection not found for INIT_CONN: {conn_id}")

    async def handle_data_conn(self, reader, writer):
        """Handle a dedicated data channel connection from a client."""
        addr = writer.get_extra_info("peername")
        ip = addr[0] if addr else "unknown"
        _optimize_socket(writer)
        self.logger.info(f"New data connection from {addr}")

        if not self.access.check_ip(ip):
            writer.close()
            await writer.wait_closed()
            return

        buffer = b""
        session = None

        try:
            # Step 1: wait for DATA_AUTH to link this connection to a session
            auth_done = False
            while not auth_done and not self._stop_event.is_set():
                try:
                    data = await asyncio.wait_for(
                        reader.read(READ_BUF_SIZE), timeout=self.idle_timeout
                    )
                except asyncio.TimeoutError:
                    self.logger.info(f"Data connection auth timeout: {addr}")
                    break
                if not data:
                    break

                buffer += data
                while True:
                    message, buffer = Protocol.decode(buffer)
                    if not message:
                        break
                    if message.type == MessageType.DATA_AUTH:
                        session_id = message.payload.get("session_id", "")
                        session = self.sessions.get(session_id)
                        if session:
                            session["data_writer"] = writer
                            auth_done = True
                            resp = Message(
                                MessageType.DATA_AUTH_RESP, status="ok"
                            )
                            writer.write(Protocol.encode(resp))
                            await writer.drain()
                            self.logger.info(
                                f"Data channel linked to session "
                                f"{session_id[:8]} from {addr}"
                            )
                        else:
                            resp = Message(
                                MessageType.DATA_AUTH_RESP,
                                status="error",
                                message="Invalid session",
                            )
                            writer.write(Protocol.encode(resp))
                            await writer.drain()
                            self.logger.warning(
                                f"Data auth failed: unknown session from {addr}"
                            )
                            writer.close()
                            await writer.wait_closed()
                            return
                    else:
                        self.logger.warning(
                            f"Unexpected message on data channel: {message.type}"
                        )

            if not auth_done:
                writer.close()
                await writer.wait_closed()
                return

            # Step 2: process DATA messages
            while not self._stop_event.is_set():
                try:
                    data = await asyncio.wait_for(
                        reader.read(READ_BUF_SIZE), timeout=self.idle_timeout
                    )
                except asyncio.TimeoutError:
                    break
                if not data:
                    break

                buffer += data
                while True:
                    message, buffer = Protocol.decode(buffer)
                    if not message:
                        break
                    if message.type == MessageType.DATA:
                        await self.handle_data(message, writer)
                    elif message.type == MessageType.CLOSE:
                        await self.handle_close(message)
                    else:
                        self.logger.warning(
                            f"Non-data message on data channel: {message.type}"
                        )

        except asyncio.CancelledError:
            pass
        except ConnectionResetError:
            self.logger.info(f"Data connection reset by {addr}")
        except Exception as e:
            self.logger.error(f"Data connection error: {e}")
        finally:
            if session:
                session["data_writer"] = None
                self.logger.info(f"Data channel unlinked for session {session['id'][:8]}")
            try:
                writer.close()
                await writer.wait_closed()
            except:
                pass
            self.logger.info(f"Data connection closed: {addr}")

    def _remove_session(self, session):
        """Clean up a session when its control connection closes."""
        session_id = session.get("id")
        if session_id and session_id in self.sessions:
            # Migrate data_writer to None so proxies fall back to control
            self.sessions.pop(session_id, None)
            # Clean up STCP providers and visitors owned by this session
            for name, info in list(self.stcp_providers.items()):
                if info.get("session_id") == session_id:
                    self.stcp_providers.pop(name, None)
            for name, info in list(self.stcp_visitors.items()):
                if info.get("session_id") == session_id:
                    self.stcp_visitors.pop(name, None)
            # Clean up HTTP proxies owned by this session
            for name, info in list(self.http_proxies.items()):
                if info.get("session_id") == session_id:
                    self.http_proxies.pop(name, None)
            self.logger.info(f"Session removed: {session_id[:8]}")

    async def handle_http_vhost_conn(self, reader, writer):
        """Handle HTTP vhost connections - parse Host header and route to backend."""
        addr = writer.get_extra_info("peername")
        ip = addr[0] if addr else "unknown"
        _optimize_socket(writer)

        if not self.access.check_ip(ip):
            writer.close()
            await writer.wait_closed()
            return

        # Read first line + headers to get Host
        try:
            first_line = b""
            headers_raw = b""
            while True:
                line = await asyncio.wait_for(reader.readline(), timeout=5)
                if not line:
                    break
                if not first_line:
                    first_line = line
                else:
                    headers_raw += line
                if line == b"\r\n":
                    break
                if len(headers_raw) > 8192:
                    writer.write(
                        b"HTTP/1.1 431 Request Header Fields Too Large\r\n"
                        b"Content-Length: 0\r\n\r\n"
                    )
                    await writer.drain()
                    writer.close()
                    await writer.wait_closed()
                    return
        except Exception:
            writer.close()
            await writer.wait_closed()
            return

        if not first_line:
            writer.close()
            await writer.wait_closed()
            return

        # Parse Host header
        host = ""
        for line in headers_raw.split(b"\r\n"):
            if not line:
                continue
            if line.lower().startswith(b"host:"):
                host = line[5:].strip().decode("ascii", errors="replace")
                break

        if ":" in host:
            hostname = host.split(":")[0]
        else:
            hostname = host

        hostname_lower = hostname.lower()

        # Match custom_domains and subdomain routing
        proxy_name = None
        for name, info in self.http_proxies.items():
            # Match custom_domains
            for domain in info.get("custom_domains", []):
                if hostname_lower == domain.lower():
                    proxy_name = name
                    break
            if proxy_name:
                break
            # Match subdomain
            subdomain = info.get("subdomain", "")
            if subdomain and self.subdomain_host:
                expected = f"{subdomain.lower()}.{self.subdomain_host.lower()}"
                if hostname_lower == expected:
                    proxy_name = name
                    break

        if not proxy_name:
            writer.write(
                b"HTTP/1.1 404 Not Found\r\n"
                b"Content-Type: text/plain\r\n"
                b"Content-Length: 14\r\n\r\n"
                b"No such host\r\n"
            )
            await writer.drain()
            writer.close()
            await writer.wait_closed()
            return

        proxy_info = self.http_proxies.get(proxy_name)
        if not proxy_info:
            writer.close()
            await writer.wait_closed()
            return

        conn_id = Protocol.generate_conn_id()
        self.logger.info(
            f"HTTP proxy connection from {addr} host={hostname} "
            f"-> {proxy_name} ({conn_id})"
        )

        self.stats.on_connect(
            proxy_name, conn_id, "http", self.vhost_http_port,
        )

        new_conn_msg = Message(
            MessageType.NEW_CONN,
            proxy_name=proxy_name,
            conn_id=conn_id,
        )

        # Prepend the already-read HTTP request data so it gets forwarded too
        prepend_data = first_line + headers_raw

        try:
            proxy_info["control_writer"].write(Protocol.encode(new_conn_msg))
            await proxy_info["control_writer"].drain()

            self.conn_pool[conn_id] = {
                "proxy_name": proxy_name,
                "proxy_reader": reader,
                "proxy_writer": writer,
                "client_writer": None,
                "client_ready": asyncio.Event(),
                "created_at": time.time(),
                "last_activity": time.time(),
                "prepend_data": prepend_data,
            }

            asyncio.create_task(self.forward_proxy_data(conn_id))
        except Exception as e:
            self.logger.error(f"Failed to handle HTTP vhost conn: {e}")
            writer.close()
            await writer.wait_closed()

    def _register_http_proxy(self, message, writer, addr, session=None):
        """Register an HTTP proxy with custom domains and subdomain."""
        proxy_name = message.payload.get("proxy_name")
        custom_domains = message.payload.get("custom_domains", []) or []
        subdomain = message.payload.get("subdomain", "")

        if not proxy_name:
            return False, "Missing proxy_name"

        if not custom_domains and not subdomain:
            return False, "At least one of custom_domains or subdomain is required"

        if proxy_name in self.http_proxies:
            return False, f"Proxy {proxy_name} already registered"

        subdomain_host = self.subdomain_host or ""
        if subdomain and not subdomain_host:
            return False, "subdomain_host not configured on server"

        # Check for domain conflicts
        all_domains = [d.lower() for d in custom_domains]
        if subdomain and subdomain_host:
            all_domains.append(f"{subdomain.lower()}.{subdomain_host.lower()}")

        for name, info in self.http_proxies.items():
            existing_domains = [d.lower() for d in info.get("custom_domains", [])]
            if info.get("subdomain") and subdomain_host:
                existing_domains.append(
                    f"{info['subdomain'].lower()}.{subdomain_host.lower()}"
                )
            for d in all_domains:
                if d in existing_domains:
                    return False, f"Domain {d} already in use by {name}"

        session_id = session["id"] if session else None
        self.http_proxies[proxy_name] = {
            "custom_domains": custom_domains,
            "subdomain": subdomain,
            "control_writer": writer,
            "session_id": session_id,
            "created_at": time.time(),
        }
        if session:
            session["proxies"].add(proxy_name)
        self.stats.on_proxy_register(proxy_name, "http", self.vhost_http_port)
        self.logger.info(
            f"HTTP proxy registered: {proxy_name} "
            f"domains={custom_domains} subdomain={subdomain}"
        )
        return True, "ok"

    def _register_stcp_proxy(self, message, writer, addr, session=None):
        """Register an STCP provider (server side)."""
        proxy_name = message.payload.get("proxy_name")
        sk = message.payload.get("sk", "")

        if not proxy_name:
            return False, "Missing proxy_name"

        if not sk:
            return False, "Missing sk (secret key)"

        if proxy_name in self.stcp_providers:
            return False, f"STCP proxy {proxy_name} already registered"

        session_id = session["id"] if session else None
        self.stcp_providers[proxy_name] = {
            "sk": sk,
            "control_writer": writer,
            "session_id": session_id,
            "created_at": time.time(),
        }
        if session:
            session["proxies"].add(proxy_name)
        self.stats.on_proxy_register(proxy_name, "stcp", 0)
        self.logger.info(f"STCP provider registered: {proxy_name}")
        return True, "ok"

    def _register_stcp_visitor(self, message, writer, addr, session=None):
        """Register an STCP visitor (client side, local bind port)."""
        proxy_name = message.payload.get("proxy_name")
        sk = message.payload.get("sk", "")
        bind_port = message.payload.get("bind_port")
        server_name = message.payload.get("server_name", proxy_name)

        if not proxy_name:
            return False, "Missing proxy_name"

        if not sk:
            return False, "Missing sk (secret key)"

        if not bind_port:
            return False, "Missing bind_port"

        provider = self.stcp_providers.get(server_name)
        if not provider:
            return False, f"STCP proxy {server_name} not found"

        if provider["sk"] != sk:
            return False, "Invalid secret key"

        if proxy_name in self.stcp_visitors:
            return False, f"STCP visitor {proxy_name} already registered"

        session_id = session["id"] if session else None
        self.stcp_visitors[proxy_name] = {
            "sk": sk,
            "bind_port": bind_port,
            "server_name": server_name,
            "session_id": session_id,
            "control_writer": writer,
            "created_at": time.time(),
        }
        if session:
            session["proxies"].add(proxy_name)
        self.stats.on_proxy_register(proxy_name, "stcp_visitor", bind_port)
        self.logger.info(
            f"STCP visitor registered: {proxy_name} "
            f"-> {server_name} bind_port={bind_port}"
        )
        return True, "ok"

    async def handle_stcp_new_visitor(self, message, writer, session=None):
        """Handle STCP_NEW_VISITOR from visitor client; forward to provider.
        
        Creates an STCP relay entry with the visitor side writer.
        """
        proxy_name = message.payload.get("proxy_name")
        visitor_conn_id = message.payload.get("visitor_conn_id")

        if not proxy_name or not visitor_conn_id:
            return

        provider = self.stcp_providers.get(proxy_name)
        if not provider:
            self.logger.warning(
                f"STCP new visitor for unknown proxy: {proxy_name}"
            )
            return

        # Create STCP connection entry with visitor side
        if visitor_conn_id not in self.stcp_conns:
            self.stcp_conns[visitor_conn_id] = {
                "proxy_name": proxy_name,
                "visitor_writer": writer,
                "provider_writer": None,
                "created_at": time.time(),
                "last_activity": time.time(),
            }
            self.stats.on_connect(proxy_name, visitor_conn_id, "stcp", 0)

        # Forward to provider
        forward_msg = Message(
            MessageType.STCP_NEW_VISITOR,
            proxy_name=proxy_name,
            visitor_conn_id=visitor_conn_id,
        )
        try:
            provider["control_writer"].write(Protocol.encode(forward_msg))
            await provider["control_writer"].drain()
            self.logger.debug(
                f"STCP new visitor forwarded to provider: {proxy_name} "
                f"({visitor_conn_id})"
            )
        except Exception as e:
            self.logger.error(f"Failed to forward STCP new visitor: {e}")
            self.close_stcp_conn(visitor_conn_id)

    async def handle_stcp_visitor_ready(self, message, writer, session=None):
        """Handle STCP_VISITOR_READY from provider; link provider writer.
        
        The provider sends STCP_VISITOR_READY after connecting to the local
        service. We link the provider writer to the existing STCP connection
        entry so data can be relayed between visitor and provider.
        Flushes any buffered visitor data to the provider.
        """
        proxy_name = message.payload.get("proxy_name")
        visitor_conn_id = message.payload.get("visitor_conn_id")

        if not proxy_name or not visitor_conn_id:
            return

        stcp_info = self.stcp_conns.get(visitor_conn_id)
        if not stcp_info:
            self.logger.warning(
                f"STCP visitor ready for unknown conn: {visitor_conn_id}"
            )
            return

        stcp_info["provider_writer"] = writer
        stcp_info["last_activity"] = time.time()

        # Flush any buffered visitor data to the provider
        buffered = stcp_info.get("visitor_buffer", b"")
        if buffered:
            try:
                writer.write(Protocol.encode(
                    Message(MessageType.DATA, conn_id=visitor_conn_id, data=buffered)
                ))
                await writer.drain()
                self.stats.on_data_out(visitor_conn_id, len(buffered))
            except Exception as e:
                self.logger.debug(f"Error flushing STCP buffer: {e}")
                self.close_stcp_conn(visitor_conn_id)
                return
            stcp_info["visitor_buffer"] = b""

        self.logger.debug(
            f"STCP visitor ready: {proxy_name} ({visitor_conn_id})"
        )

    async def start_tcp_proxy(self, proxy_name, remote_port):
        try:
            server = await asyncio.start_server(
                lambda r, w: self.handle_tcp_proxy_conn(r, w, proxy_name),
                "0.0.0.0",
                remote_port,
            )
            self.proxy_servers[proxy_name] = server
            self.logger.info(f"TCP proxy listening on port {remote_port}")
            asyncio.create_task(server.serve_forever())
            return server
        except Exception as e:
            self.logger.error(f"Failed to start TCP proxy on port {remote_port}: {e}")
            return None

    async def start_udp_proxy(self, proxy_name, remote_port):
        try:
            loop = asyncio.get_event_loop()
            transport, protocol = await loop.create_datagram_endpoint(
                lambda: UDPProxyProtocol(self, proxy_name),
                local_addr=("0.0.0.0", remote_port),
            )
            self.proxy_servers[proxy_name] = transport
            self.logger.info(f"UDP proxy listening on port {remote_port}")
            return transport
        except Exception as e:
            self.logger.error(f"Failed to start UDP proxy on port {remote_port}: {e}")
            return None

    async def start_ftp_proxy(self, proxy_name, remote_port):
        try:
            server = await asyncio.start_server(
                lambda r, w: self.handle_ftp_proxy_conn(r, w, proxy_name),
                "0.0.0.0",
                remote_port,
            )
            self.proxy_servers[proxy_name] = server
            self.logger.info(f"FTP proxy listening on port {remote_port}")
            asyncio.create_task(server.serve_forever())
            return server
        except Exception as e:
            self.logger.error(f"Failed to start FTP proxy on port {remote_port}: {e}")
            return None

    async def _find_available_port(self, start_port=30000, end_port=40000):
        """Find an available port for FTP data connection."""
        import random
        for _ in range(100):
            port = random.randint(start_port, end_port)
            if not self.access.check_port(port):
                continue
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.bind(("0.0.0.0", port))
                sock.close()
                return port
            except OSError:
                continue
        return None

    async def handle_ftp_proxy_conn(self, reader, writer, proxy_name):
        addr = writer.get_extra_info("peername")
        ip = addr[0] if addr else "unknown"
        _optimize_socket(writer)

        if not self.access.check_ip(ip):
            self.logger.warning(f"FTP proxy connection from {ip} blocked")
            writer.close()
            await writer.wait_closed()
            return

        proxy_info = self.proxies.get(proxy_name)
        if not proxy_info:
            writer.close()
            await writer.wait_closed()
            return

        conn_id = Protocol.generate_conn_id()
        self.logger.info(
            f"New FTP proxy connection from {addr} for {proxy_name} ({conn_id})"
        )

        self.stats.on_connect(
            proxy_name, conn_id,
            proxy_info["type"], proxy_info["remote_port"],
        )

        new_conn_msg = Message(
            MessageType.NEW_CONN,
            proxy_name=proxy_name,
            conn_id=conn_id,
        )

        try:
            proxy_info["control_writer"].write(Protocol.encode(new_conn_msg))
            await proxy_info["control_writer"].drain()
            proxy_info["last_activity"] = time.time()

            self.conn_pool[conn_id] = {
                "proxy_name": proxy_name,
                "proxy_reader": reader,
                "proxy_writer": writer,
                "client_writer": None,
                "client_ready": asyncio.Event(),
                "created_at": time.time(),
                "last_activity": time.time(),
                "ftp_resp_buffer": b"",
                "ftp_data_conns": {},
                "is_ftp": True,
                "public_addr": self.config.get("bind_addr", "0.0.0.0"),
            }

            asyncio.create_task(self.forward_proxy_data(conn_id))
        except Exception as e:
            self.logger.error(f"Failed to handle FTP proxy conn: {e}")
            writer.close()
            await writer.wait_closed()

    async def _find_available_port(self, start_port=30000, end_port=40000):
        """Find an available port for FTP data connection."""
        import random
        for _ in range(100):
            port = random.randint(start_port, end_port)
            if not self.access.check_port(port):
                continue
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.bind(("0.0.0.0", port))
                sock.close()
                return port
            except OSError:
                continue
        return None

    async def _handle_ftp_response(self, conn_id, data):
        """Process FTP response data, intercept PASV/EPSV and rewrite them.
        
        Returns the data that should be forwarded to the FTP client.
        """
        conn_info = self.conn_pool.get(conn_id)
        if not conn_info:
            return data

        conn_info["ftp_resp_buffer"] += data

        result = b""
        buf = conn_info["ftp_resp_buffer"]

        while True:
            line_end = buf.find(b"\r\n")
            if line_end == -1:
                break

            line = buf[:line_end + 2]
            buf = buf[line_end + 2:]

            line_upper = line.upper()

            pasv_idx = line_upper.find(b"227 ENTERING PASSIVE MODE")
            if pasv_idx != -1:
                new_line = await self._rewrite_pasv_response(conn_id, line)
                if new_line:
                    result += new_line
                    continue

            epsv_idx = line_upper.find(b"229 ENTERING EXTENDED PASSIVE MODE")
            if epsv_idx != -1:
                new_line = await self._rewrite_epsv_response(conn_id, line)
                if new_line:
                    result += new_line
                    continue

            result += line

        conn_info["ftp_resp_buffer"] = buf
        return result

    async def _rewrite_pasv_response(self, conn_id, line):
        """Rewrite PASV response: replace internal IP/port with public one."""
        import re
        match = re.search(rb'\((\d+),(\d+),(\d+),(\d+),(\d+),(\d+)\)', line)
        if not match:
            return line

        internal_port = int(match.group(5)) * 256 + int(match.group(6))

        public_port = await self._create_ftp_data_listener(conn_id, internal_port)
        if not public_port:
            self.logger.warning(f"FTP: Failed to allocate data port for conn {conn_id}")
            return line

        conn_info = self.conn_pool.get(conn_id)
        public_addr = conn_info.get("public_addr", "127.0.0.1")
        if public_addr in ("0.0.0.0", "::"):
            public_addr = "127.0.0.1"

        octets = public_addr.split(".")
        if len(octets) != 4:
            octets = ["127", "0", "0", "1"]

        p1 = public_port // 256
        p2 = public_port % 256

        new_ip_port = f"({octets[0]},{octets[1]},{octets[2]},{octets[3]},{p1},{p2})".encode()
        new_line = re.sub(rb'\(\d+,\d+,\d+,\d+,\d+,\d+\)', new_ip_port, line)

        self.logger.info(
            f"FTP PASV rewrite: internal_port={internal_port} "
            f"-> public_port={public_port} (conn={conn_id[:8]})"
        )
        return new_line

    async def _rewrite_epsv_response(self, conn_id, line):
        """Rewrite EPSV response: replace internal port with public one."""
        import re
        match = re.search(rb'\(\|\|\|(\d+)\|\)', line)
        if not match:
            return line

        internal_port = int(match.group(1))

        public_port = await self._create_ftp_data_listener(conn_id, internal_port)
        if not public_port:
            self.logger.warning(f"FTP: Failed to allocate data port for conn {conn_id}")
            return line

        new_port_str = f"(|||{public_port}|)".encode()
        new_line = re.sub(rb'\(\|\|\d+\|\)', new_port_str, line)
        if new_line == line:
            new_line = re.sub(rb'\(\|\|\|(\d+)\|\)', new_port_str, line)

        self.logger.info(
            f"FTP EPSV rewrite: internal_port={internal_port} "
            f"-> public_port={public_port} (conn={conn_id[:8]})"
        )
        return new_line

    async def _create_ftp_data_listener(self, ctrl_conn_id, internal_port):
        """Create a listener on a public port for FTP data connection.
        
        Returns the public port number, or None if failed.
        """
        conn_info = self.conn_pool.get(ctrl_conn_id)
        if not conn_info:
            return None

        proxy_info = self.proxies.get(conn_info["proxy_name"])
        if not proxy_info:
            return None

        public_port = await self._find_available_port()
        if not public_port:
            return None

        data_conn_id = Protocol.generate_conn_id()

        ready_event = asyncio.Event()

        self.ftp_data_ports[data_conn_id] = {
            "ctrl_conn_id": ctrl_conn_id,
            "proxy_name": conn_info["proxy_name"],
            "internal_port": internal_port,
            "public_port": public_port,
            "ready_event": ready_event,
            "proxy_reader": None,
            "proxy_writer": None,
            "client_writer": None,
        }
        conn_info["ftp_data_conns"][data_conn_id] = public_port

        try:
            server = await asyncio.start_server(
                lambda r, w: self._handle_ftp_data_conn(r, w, data_conn_id),
                "0.0.0.0",
                public_port,
            )
            self.ftp_data_ports[data_conn_id]["server"] = server
            asyncio.create_task(server.serve_forever())
        except Exception as e:
            self.logger.error(f"FTP: Failed to start data listener on port {public_port}: {e}")
            self.ftp_data_ports.pop(data_conn_id, None)
            conn_info["ftp_data_conns"].pop(data_conn_id, None)
            return None

        new_data_msg = Message(
            MessageType.FTP_NEW_DATA,
            ctrl_conn_id=ctrl_conn_id,
            data_conn_id=data_conn_id,
            internal_port=internal_port,
        )
        try:
            proxy_info["control_writer"].write(Protocol.encode(new_data_msg))
            await proxy_info["control_writer"].drain()
        except Exception as e:
            self.logger.error(f"FTP: Failed to send FTP_NEW_DATA: {e}")
            self.ftp_data_ports.pop(data_conn_id, None)
            conn_info["ftp_data_conns"].pop(data_conn_id, None)
            server.close()
            return None

        return public_port

    async def _handle_ftp_data_conn(self, reader, writer, data_conn_id):
        """Handle an incoming FTP data connection from a client."""
        addr = writer.get_extra_info("peername")
        _optimize_socket(writer)

        data_info = self.ftp_data_ports.get(data_conn_id)
        if not data_info:
            writer.close()
            await writer.wait_closed()
            return

        data_info["proxy_reader"] = reader
        data_info["proxy_writer"] = writer

        buffered = data_info.pop("buffer", b"")
        if buffered:
            try:
                writer.write(buffered)
                await writer.drain()
            except Exception as e:
                self.logger.debug(f"Error flushing FTP data buffer: {e}")

        try:
            await asyncio.wait_for(data_info["ready_event"].wait(), timeout=30)
        except asyncio.TimeoutError:
            self.logger.warning(f"FTP data connection timed out waiting for client side: {data_conn_id[:8]}")
            writer.close()
            await writer.wait_closed()
            return

        self.logger.info(f"FTP data connection established: {data_conn_id[:8]}")

        async def forward_client_to_server():
            try:
                while True:
                    data = await reader.read(READ_BUF_SIZE)
                    if not data:
                        break
                    if data_info.get("client_writer"):
                        data_msg = Message(
                            MessageType.DATA,
                            conn_id=data_conn_id,
                            data=data,
                        )
                        data_info["client_writer"].write(Protocol.encode(data_msg))
                        await data_info["client_writer"].drain()
            except Exception:
                pass

        try:
            await forward_client_to_server()
        finally:
            try:
                if data_info.get("client_writer"):
                    close_msg = Message(MessageType.CLOSE, conn_id=data_conn_id)
                    try:
                        data_info["client_writer"].write(Protocol.encode(close_msg))
                        asyncio.create_task(self._safe_drain(data_info["client_writer"]))
                    except:
                        pass
            except:
                pass
            try:
                writer.close()
                await writer.wait_closed()
            except:
                pass
            self.ftp_data_ports.pop(data_conn_id, None)
            ctrl_info = self.conn_pool.get(data_info.get("ctrl_conn_id", ""))
            if ctrl_info:
                ctrl_info["ftp_data_conns"].pop(data_conn_id, None)
            if data_info.get("server"):
                try:
                    data_info["server"].close()
                except:
                    pass

    async def handle_ftp_data_ready(self, message, writer, session=None):
        """Handle FTP_DATA_READY from client (data connection established on client side)."""
        data_conn_id = message.payload.get("data_conn_id")
        if not data_conn_id:
            return

        data_info = self.ftp_data_ports.get(data_conn_id)
        if not data_info:
            self.logger.warning(f"FTP_DATA_READY for unknown data conn: {data_conn_id}")
            return

        data_info["client_writer"] = writer
        data_info["ready_event"].set()
        self.logger.debug(f"FTP data ready signaled for conn: {data_conn_id[:8]}")

    async def handle_tcp_proxy_conn(self, reader, writer, proxy_name):
        addr = writer.get_extra_info("peername")
        ip = addr[0] if addr else "unknown"
        _optimize_socket(writer)

        if not self.access.check_ip(ip):
            self.logger.warning(f"Proxy connection from {ip} blocked")
            writer.close()
            await writer.wait_closed()
            return

        proxy_info = self.proxies.get(proxy_name)
        if not proxy_info:
            writer.close()
            await writer.wait_closed()
            return

        conn_id = Protocol.generate_conn_id()
        self.logger.info(
            f"New proxy connection from {addr} for {proxy_name} ({conn_id})"
        )

        self.stats.on_connect(
            proxy_name, conn_id,
            proxy_info["type"], proxy_info["remote_port"],
        )

        new_conn_msg = Message(
            MessageType.NEW_CONN,
            proxy_name=proxy_name,
            conn_id=conn_id,
        )

        try:
            proxy_info["control_writer"].write(Protocol.encode(new_conn_msg))
            await proxy_info["control_writer"].drain()
            proxy_info["last_activity"] = time.time()

            self.conn_pool[conn_id] = {
                "proxy_name": proxy_name,
                "proxy_reader": reader,
                "proxy_writer": writer,
                "client_writer": None,
                "client_ready": asyncio.Event(),
                "created_at": time.time(),
                "last_activity": time.time(),
            }

            asyncio.create_task(self.forward_proxy_data(conn_id))
        except Exception as e:
            self.logger.error(f"Failed to handle proxy conn: {e}")
            writer.close()
            await writer.wait_closed()

    async def forward_proxy_data(self, conn_id):
        conn_info = self.conn_pool.get(conn_id)
        if not conn_info:
            return

        try:
            await asyncio.wait_for(
                conn_info["client_ready"].wait(), timeout=30
            )
        except asyncio.TimeoutError:
            self.logger.warning(f"Client ready timeout for conn_id: {conn_id}")
            self.close_conn(conn_id)
            return

        try:
            prepend_data = conn_info.get("prepend_data")
            if prepend_data:
                conn_info["last_activity"] = time.time()
                self.stats.on_data_in(conn_id, len(prepend_data))
                data_msg = Message(
                    MessageType.DATA,
                    conn_id=conn_id,
                    data=prepend_data,
                )
                conn_info["client_writer"].write(Protocol.encode(data_msg))
                await conn_info["client_writer"].drain()

            while True:
                try:
                    data = await asyncio.wait_for(
                        conn_info["proxy_reader"].read(READ_BUF_SIZE),
                        timeout=self.idle_timeout,
                    )
                except asyncio.TimeoutError:
                    break

                if not data:
                    break

                conn_info["last_activity"] = time.time()
                self.stats.on_data_in(conn_id, len(data))

                data_msg = Message(
                    MessageType.DATA,
                    conn_id=conn_id,
                    data=data,
                )
                conn_info["client_writer"].write(Protocol.encode(data_msg))
                await conn_info["client_writer"].drain()
        except ConnectionResetError:
            pass
        except Exception as e:
            self.logger.error(f"Error forwarding proxy data: {e}")
        finally:
            self.close_conn(conn_id)

    async def handle_data(self, message, writer):
        conn_id = message.payload.get("conn_id")
        data = message.payload.get("data", b"")

        # Check if this is an STCP connection (relay between two clients)
        if conn_id in self.stcp_conns:
            stcp_info = self.stcp_conns[conn_id]
            stcp_info["last_activity"] = time.time()
            # Determine which side this is
            is_provider = False
            is_visitor = False
            if stcp_info.get("provider_writer") is writer:
                is_provider = True
            elif stcp_info.get("visitor_writer") is writer:
                is_visitor = True
            else:
                # First time we see data from this side, assign the writer
                if not stcp_info.get("provider_writer"):
                    stcp_info["provider_writer"] = writer
                    is_provider = True
                elif not stcp_info.get("visitor_writer"):
                    stcp_info["visitor_writer"] = writer
                    is_visitor = True

            # Get the other side
            other_writer = stcp_info.get("visitor_writer") if is_provider else stcp_info.get("provider_writer")

            if other_writer:
                # Flush any buffered data first
                buffer_key = "provider_buffer" if is_provider else "visitor_buffer"
                buffered = stcp_info.get(buffer_key, b"")
                if buffered:
                    try:
                        other_writer.write(Protocol.encode(
                            Message(MessageType.DATA, conn_id=conn_id, data=buffered)
                        ))
                        await other_writer.drain()
                        self.stats.on_data_out(conn_id, len(buffered))
                    except Exception as e:
                        self.logger.debug(f"Error relaying buffered STCP data: {e}")
                        self.close_stcp_conn(conn_id)
                        return
                    stcp_info[buffer_key] = b""

                # Send current data
                try:
                    other_writer.write(Protocol.encode(
                        Message(MessageType.DATA, conn_id=conn_id, data=data)
                    ))
                    await other_writer.drain()
                    self.stats.on_data_out(conn_id, len(data))
                except Exception as e:
                    self.logger.debug(f"Error relaying STCP data: {e}")
                    self.close_stcp_conn(conn_id)
            else:
                # Other side not ready yet, buffer the data
                buffer_key = "visitor_buffer" if is_visitor else "provider_buffer"
                stcp_info[buffer_key] = stcp_info.get(buffer_key, b"") + data
                self.stats.on_data_in(conn_id, len(data))
            return

        # Check if this is an FTP data connection
        if conn_id in self.ftp_data_ports:
            await self._handle_ftp_data_message(conn_id, data, writer)
            return

        conn_info = self.conn_pool.get(conn_id)
        if not conn_info:
            return

        if not conn_info["client_writer"]:
            conn_info["client_writer"] = writer

        conn_info["last_activity"] = time.time()

        proxy_info = self.proxies.get(conn_info["proxy_name"])
        if proxy_info:
            proxy_info["last_activity"] = time.time()

        self.stats.on_data_out(conn_id, len(data))

        try:
            # For FTP control connections, intercept and rewrite PASV/EPSV responses
            if conn_info.get("is_ftp"):
                data = await self._handle_ftp_response(conn_id, data)
            conn_info["proxy_writer"].write(data)
            await conn_info["proxy_writer"].drain()
        except Exception as e:
            self.logger.debug(f"Error writing to proxy: {e}")
            self.close_conn(conn_id)

    async def _handle_ftp_data_message(self, data_conn_id, data, writer):
        """Handle DATA message for an FTP data connection."""
        data_info = self.ftp_data_ports.get(data_conn_id)
        if not data_info:
            return

        if not data_info.get("client_writer"):
            data_info["client_writer"] = writer

        if data_info.get("proxy_writer"):
            try:
                data_info["proxy_writer"].write(data)
                await data_info["proxy_writer"].drain()
            except Exception as e:
                self.logger.debug(f"Error writing FTP data to client: {e}")
                if data_info.get("proxy_writer"):
                    try:
                        data_info["proxy_writer"].close()
                    except:
                        pass
        else:
            data_info["buffer"] = data_info.get("buffer", b"") + data

    async def handle_ping(self, writer):
        pong_msg = Message(MessageType.PONG)
        try:
            writer.write(Protocol.encode(pong_msg))
            await writer.drain()
        except:
            pass

    async def handle_close(self, message):
        conn_id = message.payload.get("conn_id")
        if conn_id in self.stcp_conns:
            self.close_stcp_conn(conn_id)
        else:
            self.close_conn(conn_id)

    def close_conn(self, conn_id):
        conn_info = self.conn_pool.pop(conn_id, None)
        if conn_info:
            self.stats.on_disconnect(conn_id)
            try:
                if conn_info.get("proxy_writer"):
                    conn_info["proxy_writer"].close()
                    asyncio.create_task(
                        self._safe_wait_closed(conn_info["proxy_writer"])
                    )
            except:
                pass

    def close_stcp_conn(self, conn_id):
        """Close an STCP connection and notify both sides."""
        stcp_info = self.stcp_conns.pop(conn_id, None)
        if stcp_info:
            self.stats.on_disconnect(conn_id)
            # Notify both sides
            for side_writer in [stcp_info.get("provider_writer"), stcp_info.get("visitor_writer")]:
                if side_writer:
                    try:
                        side_writer.write(Protocol.encode(
                            Message(MessageType.CLOSE, conn_id=conn_id)
                        ))
                        asyncio.create_task(self._safe_drain(side_writer))
                    except:
                        pass
            self.logger.debug(f"STCP connection closed: {conn_id}")

    async def _safe_drain(self, writer):
        try:
            await writer.drain()
        except:
            pass

    async def _safe_wait_closed(self, writer):
        try:
            await writer.wait_closed()
        except:
            pass

    def cleanup_client_by_writer(self, writer):
        for proxy_name, proxy_info in list(self.proxies.items()):
            if proxy_info.get("control_writer") is writer:
                self._remove_proxy(proxy_name)

        # Clean up HTTP proxies owned by this writer
        for name in list(self.http_proxies.keys()):
            if self.http_proxies[name].get("control_writer") is writer:
                self.http_proxies.pop(name, None)
                self.stats.on_proxy_unregister(name)
                self.logger.info(f"HTTP proxy removed: {name}")

        # Clean up STCP providers owned by this writer
        for name in list(self.stcp_providers.keys()):
            if self.stcp_providers[name].get("control_writer") is writer:
                self.stcp_providers.pop(name, None)
                self.stats.on_proxy_unregister(name)
                # Close all STCP connections for this provider
                for cid in list(self.stcp_conns.keys()):
                    if self.stcp_conns[cid]["proxy_name"] == name:
                        self.close_stcp_conn(cid)
                self.logger.info(f"STCP provider removed: {name}")

        # Clean up STCP visitors owned by this writer
        for name in list(self.stcp_visitors.keys()):
            if self.stcp_visitors[name].get("control_writer") is writer:
                self.stcp_visitors.pop(name, None)
                self.stats.on_proxy_unregister(name)
                self.logger.info(f"STCP visitor removed: {name}")

    def _remove_proxy(self, proxy_name):
        server = self.proxy_servers.pop(proxy_name, None)
        if server:
            try:
                server.close()
            except:
                pass

        for conn_id in list(self.conn_pool.keys()):
            if self.conn_pool[conn_id]["proxy_name"] == proxy_name:
                self.close_conn(conn_id)

        self.proxies.pop(proxy_name, None)
        self.stats.on_proxy_unregister(proxy_name)
        self.logger.info(f"Proxy removed: {proxy_name}")

    async def idle_cleanup_loop(self):
        while not self._stop_event.is_set():
            try:
                await asyncio.sleep(60)
                await self._cleanup_idle_connections()
            except asyncio.CancelledError:
                break
            except Exception as e:
                self.logger.error(f"Idle cleanup error: {e}")

    async def _cleanup_idle_connections(self):
        now = time.time()
        for conn_id in list(self.conn_pool.keys()):
            conn_info = self.conn_pool.get(conn_id)
            if conn_info and now - conn_info.get("last_activity", now) > self.idle_timeout:
                self.logger.debug(f"Closing idle connection: {conn_id}")
                self.close_conn(conn_id)

        for conn_id in list(self.stcp_conns.keys()):
            conn_info = self.stcp_conns.get(conn_id)
            if conn_info and now - conn_info.get("last_activity", now) > self.idle_timeout:
                self.logger.debug(f"Closing idle STCP connection: {conn_id}")
                self.close_stcp_conn(conn_id)

    async def handle_webapi(self, reader, writer):
        """简易 HTTP WebAPI：返回统计 JSON"""
        try:
            data = await asyncio.wait_for(reader.read(4096), timeout=5)
            request = data.decode("utf-8", errors="ignore")
            path = request.split(" ")[1] if " " in request else "/"

            if path == "/" or path == "/stats":
                body = json_module.dumps(
                    self.stats.get_all_stats(), indent=2, ensure_ascii=False
                ).encode("utf-8")
                response = (
                    f"HTTP/1.1 200 OK\r\n"
                    f"Content-Type: application/json\r\n"
                    f"Content-Length: {len(body)}\r\n"
                    f"Connection: close\r\n"
                    f"\r\n"
                ).encode("utf-8") + body
            else:
                body = b'{"error": "not found"}'
                response = (
                    f"HTTP/1.1 404 Not Found\r\n"
                    f"Content-Type: application/json\r\n"
                    f"Content-Length: {len(body)}\r\n"
                    f"Connection: close\r\n"
                    f"\r\n"
                ).encode("utf-8") + body

            writer.write(response)
            await writer.drain()
        except Exception as e:
            self.logger.debug(f"Dashboard error: {e}")
        finally:
            writer.close()
            await writer.wait_closed()


class UDPProxyProtocol(asyncio.DatagramProtocol):
    def __init__(self, server, proxy_name):
        self.server = server
        self.proxy_name = proxy_name
        self.transport = None

    def connection_made(self, transport):
        self.transport = transport

    def datagram_received(self, data, addr):
        ip = addr[0] if addr else "unknown"
        if not self.server.access.check_ip(ip):
            return

        proxy_info = self.server.proxies.get(self.proxy_name)
        if not proxy_info:
            return

        conn_id = Protocol.generate_conn_id()
        self.server.conn_pool[conn_id] = {
            "proxy_name": self.proxy_name,
            "udp_transport": self.transport,
            "udp_addr": addr,
            "created_at": time.time(),
            "last_activity": time.time(),
        }
        self.server.stats.on_connect(
            self.proxy_name, conn_id,
            proxy_info["type"], proxy_info["remote_port"],
        )
        self.server.stats.on_data_in(conn_id, len(data))

        try:
            new_conn_msg = Message(
                MessageType.NEW_CONN,
                proxy_name=self.proxy_name,
                conn_id=conn_id,
            )
            proxy_info["control_writer"].write(Protocol.encode(new_conn_msg))
            asyncio.create_task(proxy_info["control_writer"].drain())

            data_msg = Message(
                MessageType.DATA, conn_id=conn_id, data=data
            )
            proxy_info["control_writer"].write(Protocol.encode(data_msg))
            asyncio.create_task(proxy_info["control_writer"].drain())
        except Exception as e:
            self.server.logger.error(f"UDP forward error: {e}")


def main():
    import argparse

    parser = argparse.ArgumentParser(description="FRP Server")
    parser.add_argument("-c", "--config", default="config/frps.json", help="Config file")
    args = parser.parse_args()

    config = Config("server").load_from_file(args.config)
    server = FRPServer(config)

    try:
        asyncio.run(server.start())
    except KeyboardInterrupt:
        print("\nFRPServer stopped")


if __name__ == "__main__":
    main()