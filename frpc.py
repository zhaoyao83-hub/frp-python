import asyncio
import ssl
import socket
import time
from config import Config
from protocol import Protocol, Message, MessageType
from log import get_logger

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


class ClientState:
    def __init__(self):
        self.proxies = {}
        self.conn_pool = {}
        self.ftp_data_conns = {}  # data_conn_id -> {"local_reader", "local_writer", ...}


class FRPClient:
    def __init__(self, config):
        self.config = config
        self.logger = get_logger(
            "frpc", config.get("log_level"), config.get("log_file")
        )
        self.state = ClientState()
        self.control_reader = None
        self.control_writer = None
        self.heartbeat_task = None
        self.reader_task = None
        self._stop_event = asyncio.Event()
        self.auth_token = config.get("auth_token")
        self.reconnect = config.get("reconnect", True)
        self.max_retries = config.get("reconnect_max_retries", 0)
        self.base_delay = config.get("reconnect_base_delay", 1)
        self.max_delay = config.get("reconnect_max_delay", 60)
        self.tls_enabled = config.get("tls", False)
        self.tls_insecure = config.get("tls_insecure", False)
        self.tls_ca_file = config.get("tls_ca_file")
        self.data_port = config.get("data_port")
        self._ssl_ctx = None

        self.data_reader = None
        self.data_writer = None
        self.data_task = None
        self.session_id = None

    def _init_ssl(self):
        if not self.tls_enabled:
            return None
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        if self.tls_ca_file:
            ctx.load_verify_locations(self.tls_ca_file)
        else:
            ctx.load_default_certs()
        if self.tls_insecure:
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            self.logger.info("TLS enabled (insecure mode, cert not verified)")
        else:
            self.logger.info("TLS enabled (cert verified)")
        return ctx

    async def start(self):
        self._ssl_ctx = self._init_ssl()
        retry_count = 0
        server_addr = self.config.get("server_addr", "127.0.0.1")
        server_port = self.config.get("server_port", 7000)

        while not self._stop_event.is_set():
            try:
                self.logger.info(
                    f"Connecting to server {server_addr}:{server_port}"
                )
                await self._connect_and_run(server_addr, server_port)
            except asyncio.CancelledError:
                break
            except Exception as e:
                self.logger.error(f"Connection error: {e}")

            if self._stop_event.is_set():
                break

            if not self.reconnect:
                break

            retry_count += 1
            if self.max_retries > 0 and retry_count > self.max_retries:
                self.logger.error(
                    f"Max retries ({self.max_retries}) reached, exiting"
                )
                break

            delay = min(self.base_delay * (2 ** (retry_count - 1)), self.max_delay)
            self.logger.info(
                f"Reconnecting in {delay:.1f}s (attempt {retry_count})"
            )

            try:
                await asyncio.sleep(delay)
            except asyncio.CancelledError:
                break

        await self._cleanup()

    async def _connect_and_run(self, server_addr, server_port):
        self.control_reader, self.control_writer = await asyncio.wait_for(
            asyncio.open_connection(
                server_addr, server_port, ssl=self._ssl_ctx
            ),
            timeout=10,
        )
        _optimize_socket(self.control_writer)
        self.logger.info(f"Connected to server {server_addr}:{server_port}")

        if self.auth_token:
            await self._login()

        await self.register_proxies()

        # Establish data channel if server supports it
        if self.data_port and self.session_id:
            try:
                await self._connect_data_channel(server_addr)
            except Exception as e:
                self.logger.warning(
                    f"Data channel setup failed, using control channel: {e}"
                )

        self.heartbeat_task = asyncio.create_task(self.send_heartbeat())
        self.reader_task = asyncio.create_task(self.handle_server_messages())

        try:
            await self.reader_task
        finally:
            if self.heartbeat_task:
                self.heartbeat_task.cancel()
                try:
                    await self.heartbeat_task
                except asyncio.CancelledError:
                    pass
                self.heartbeat_task = None
            if self.data_task:
                self.data_task.cancel()
                try:
                    await self.data_task
                except asyncio.CancelledError:
                    pass
                self.data_task = None
            self._close_data_channel()

    async def _connect_data_channel(self, server_addr):
        """Open a dedicated data connection and authenticate it."""
        self.data_reader, self.data_writer = await asyncio.wait_for(
            asyncio.open_connection(
                server_addr, self.data_port, ssl=self._ssl_ctx
            ),
            timeout=10,
        )
        _optimize_socket(self.data_writer)
        self.logger.info(
            f"Data channel connected to {server_addr}:{self.data_port}"
        )

        auth_msg = Message(
            MessageType.DATA_AUTH, session_id=self.session_id
        )
        self.data_writer.write(Protocol.encode(auth_msg))
        await self.data_writer.drain()

        # Wait for DATA_AUTH_RESP
        buffer = b""
        while True:
            data = await asyncio.wait_for(
                self.data_reader.read(READ_BUF_SIZE), timeout=10
            )
            if not data:
                raise ConnectionError("Data channel closed during auth")
            buffer += data
            message, buffer = Protocol.decode(buffer)
            if message and message.type == MessageType.DATA_AUTH_RESP:
                if message.payload.get("status") == "ok":
                    self.logger.info("Data channel authenticated")
                    break
                else:
                    raise ConnectionError(
                        f"Data auth failed: {message.payload.get('message')}"
                    )

        # Start data message handler
        self.data_task = asyncio.create_task(self.handle_data_messages())

    def _close_data_channel(self):
        if self.data_writer:
            try:
                self.data_writer.close()
            except:
                pass
            self.data_writer = None
        self.data_reader = None

    async def handle_data_messages(self):
        """Process messages arriving on the dedicated data channel.

        Only DATA and CLOSE are expected here. When the data channel is
        active, the control-channel handler skips DATA/CLOSE so each
        connection is processed by exactly one reader.
        """
        buffer = b""
        try:
            while not self._stop_event.is_set():
                data = await self.data_reader.read(READ_BUF_SIZE)
                if not data:
                    self.logger.info("Data channel closed by server")
                    break
                buffer += data
                while True:
                    message, buffer = Protocol.decode(buffer)
                    if not message:
                        break
                    if message.type == MessageType.DATA:
                        await self.handle_data(message)
                    elif message.type == MessageType.CLOSE:
                        await self.handle_close(message)
                    else:
                        self.logger.warning(
                            f"Unexpected message on data channel: {message.type}"
                        )
        except asyncio.CancelledError:
            pass
        except ConnectionResetError:
            self.logger.info("Data channel reset by server")
        except Exception as e:
            self.logger.error(f"Data channel handler error: {e}")
        finally:
            # Mark data channel down so control channel takes over again
            self.data_writer = None
            self.data_reader = None

    def _data_writer_for(self, conn_id):
        """Return the writer to use for sending data to the server.

        Uses the dedicated data channel writer when available, otherwise
        falls back to the control writer.
        """
        if self.data_writer and not self.data_writer.is_closing():
            return self.data_writer
        return self.control_writer

    async def _login(self):
        login_msg = Message(MessageType.LOGIN, token=self.auth_token)
        self.control_writer.write(Protocol.encode(login_msg))
        await self.control_writer.drain()

        buffer = b""
        while True:
            data = await asyncio.wait_for(
                self.control_reader.read(READ_BUF_SIZE), timeout=10
            )
            if not data:
                raise ConnectionError("Server closed connection during login")

            buffer += data
            message, buffer = Protocol.decode(buffer)
            if message and message.type == MessageType.LOGIN_RESP:
                if message.payload.get("status") == "ok":
                    self.session_id = message.payload.get("session_id")
                    server_data_port = message.payload.get("data_port")
                    # Prefer server-advertised data_port if client didn't set one
                    if server_data_port and not self.data_port:
                        self.data_port = server_data_port
                    self.logger.info(
                        f"Authentication successful (session={self.session_id[:8] if self.session_id else 'n/a'})"
                    )
                    return
                else:
                    error_msg = message.payload.get("message", "Unknown error")
                    raise PermissionError(f"Authentication failed: {error_msg}")
            elif message and message.type == MessageType.ERROR:
                raise PermissionError(
                    f"Authentication error: {message.payload.get('message')}"
                )

    async def register_proxies(self):
        proxies = self.config.get("proxies", [])
        enabled_proxies = []
        for proxy in proxies:
            proxy_name = proxy.get("name")
            proxy_type = proxy.get("type", "tcp")
            enabled = proxy.get("enabled", True)

            if not enabled:
                self.logger.info(
                    f"Skipping disabled proxy: {proxy_name} ({proxy_type})"
                )
                continue

            enabled_proxies.append(proxy)

            if proxy_type == "http":
                register_msg = Message(
                    MessageType.REGISTER,
                    proxy_name=proxy_name,
                    proxy_type=proxy_type,
                    local_port=proxy.get("local_port"),
                    local_ip=proxy.get("local_ip", "127.0.0.1"),
                    custom_domains=proxy.get("custom_domains", []),
                    subdomain=proxy.get("subdomain", ""),
                )
            elif proxy_type == "stcp":
                register_msg = Message(
                    MessageType.REGISTER,
                    proxy_name=proxy_name,
                    proxy_type=proxy_type,
                    local_port=proxy.get("local_port"),
                    local_ip=proxy.get("local_ip", "127.0.0.1"),
                    sk=proxy.get("sk", ""),
                )
            elif proxy_type == "stcp_visitor":
                register_msg = Message(
                    MessageType.REGISTER,
                    proxy_name=proxy_name,
                    proxy_type=proxy_type,
                    sk=proxy.get("sk", ""),
                    bind_port=proxy.get("bind_port"),
                    bind_addr=proxy.get("bind_addr", "127.0.0.1"),
                    server_name=proxy.get("server_name", proxy_name),
                )
            else:
                register_msg = Message(
                    MessageType.REGISTER,
                    proxy_name=proxy_name,
                    proxy_type=proxy_type,
                    local_port=proxy.get("local_port"),
                    remote_port=proxy.get("remote_port"),
                    local_ip=proxy.get("local_ip", "127.0.0.1"),
                )

            self.control_writer.write(Protocol.encode(register_msg))
            await self.control_writer.drain()

            proxy_info = {
                "type": proxy_type,
                "local_port": proxy.get("local_port"),
                "local_ip": proxy.get("local_ip", "127.0.0.1"),
            }
            if proxy_type == "http":
                proxy_info["custom_domains"] = proxy.get("custom_domains", [])
                proxy_info["subdomain"] = proxy.get("subdomain", "")
            elif proxy_type == "stcp":
                proxy_info["sk"] = proxy.get("sk", "")
            elif proxy_type == "stcp_visitor":
                proxy_info["sk"] = proxy.get("sk", "")
                proxy_info["bind_port"] = proxy.get("bind_port")
                proxy_info["bind_addr"] = proxy.get("bind_addr", "127.0.0.1")
                proxy_info["server_name"] = proxy.get("server_name", proxy_name)

            self.state.proxies[proxy_name] = proxy_info

            extra_info = ""
            if proxy_type == "http":
                domains = proxy.get("custom_domains", [])
                sub = proxy.get("subdomain", "")
                extra_info = f"domains={domains} subdomain={sub}"
            elif proxy_type in ("stcp", "stcp_visitor"):
                extra_info = f"sk={'***' if proxy.get('sk') else 'none'}"
            else:
                extra_info = f"remote_port={proxy.get('remote_port')}"

            self.logger.info(
                f"Registered proxy: {proxy_name} ({proxy_type}) {extra_info}"
            )

        # Start STCP visitor listeners after all registrations
        for proxy in enabled_proxies:
            if proxy.get("type") == "stcp_visitor":
                asyncio.create_task(self._start_stcp_visitor_listener(proxy))

    async def handle_server_messages(self):
        buffer = b""
        try:
            while not self._stop_event.is_set():
                data = await self.control_reader.read(READ_BUF_SIZE)
                if not data:
                    self.logger.info("Server connection closed")
                    break

                buffer += data

                while True:
                    message, buffer = Protocol.decode(buffer)
                    if not message:
                        break

                    await self.process_message(message)
        except asyncio.CancelledError:
            pass
        except ConnectionResetError:
            self.logger.info("Connection reset by server")
        except Exception as e:
            self.logger.error(f"Message handler error: {e}")

    async def process_message(self, message):
        try:
            if message.type == MessageType.NEW_CONN:
                await self.handle_new_conn(message)
            elif message.type == MessageType.PONG:
                pass
            elif message.type == MessageType.DATA:
                # When data channel is active, DATA arrives there; ignore on control
                if not (self.data_writer and not self.data_writer.is_closing()):
                    await self.handle_data(message)
            elif message.type == MessageType.CLOSE:
                if not (self.data_writer and not self.data_writer.is_closing()):
                    await self.handle_close(message)
            elif message.type == MessageType.ERROR:
                self.logger.error(
                    f"Server error: {message.payload.get('message')}"
                )
            elif message.type == MessageType.STCP_NEW_VISITOR:
                    await self.handle_stcp_new_visitor(message)
            elif message.type == MessageType.FTP_NEW_DATA:
                await self.handle_ftp_new_data(message)
            else:
                self.logger.warning(f"Unknown message type: {message.type}")
        except Exception as e:
            self.logger.error(f"Error processing {message.type}: {e}")

    async def handle_new_conn(self, message):
        proxy_name = message.payload.get("proxy_name")
        conn_id = message.payload.get("conn_id")

        proxy_info = self.state.proxies.get(proxy_name)
        if not proxy_info:
            self.logger.error(f"Unknown proxy: {proxy_name}")
            return

        local_ip = proxy_info.get("local_ip", "127.0.0.1")
        local_port = proxy_info.get("local_port")

        try:
            if proxy_info.get("type") in ("tcp", "http", "ftp"):
                local_reader, local_writer = await asyncio.wait_for(
                    asyncio.open_connection(local_ip, local_port), timeout=5
                )
                _optimize_socket(local_writer)

                self.state.conn_pool[conn_id] = {
                    "proxy_name": proxy_name,
                    "local_reader": local_reader,
                    "local_writer": local_writer,
                    "created_at": time.time(),
                    "last_activity": time.time(),
                }

                init_msg = Message(MessageType.INIT_CONN, conn_id=conn_id)
                self.control_writer.write(Protocol.encode(init_msg))
                await self.control_writer.drain()

                asyncio.create_task(self.forward_local_data(conn_id))

                self.logger.debug(
                    f"Connected to local service {local_ip}:{local_port} "
                    f"for {proxy_name} ({conn_id})"
                )
            elif proxy_info.get("type") == "udp":
                loop = asyncio.get_event_loop()
                transport, protocol = await loop.create_datagram_endpoint(
                    lambda: UDPLocalProtocol(self, conn_id),
                    remote_addr=(local_ip, local_port),
                )

                self.state.conn_pool[conn_id] = {
                    "proxy_name": proxy_name,
                    "udp_transport": transport,
                    "created_at": time.time(),
                    "last_activity": time.time(),
                }

                init_msg = Message(MessageType.INIT_CONN, conn_id=conn_id)
                self.control_writer.write(Protocol.encode(init_msg))
                await self.control_writer.drain()

                self.logger.debug(
                    f"UDP connected to local service {local_ip}:{local_port} "
                    f"for {proxy_name} ({conn_id})"
                )

        except Exception as e:
            self.logger.warning(
                f"Failed to connect to local service {local_ip}:{local_port}: {e}"
            )
            close_msg = Message(MessageType.CLOSE, conn_id=conn_id)
            try:
                writer = self._data_writer_for(conn_id)
                writer.write(Protocol.encode(close_msg))
                await writer.drain()
            except:
                pass

    async def forward_local_data(self, conn_id):
        conn_info = self.state.conn_pool.get(conn_id)
        if not conn_info:
            return

        try:
            while True:
                data = await conn_info["local_reader"].read(READ_BUF_SIZE)
                if not data:
                    break

                conn_info["last_activity"] = time.time()
                data_msg = Message(
                    MessageType.DATA,
                    conn_id=conn_id,
                    data=data,
                )
                writer = self._data_writer_for(conn_id)
                writer.write(Protocol.encode(data_msg))
                await writer.drain()
        except ConnectionResetError:
            self.logger.debug(f"Local connection reset: {conn_id}")
        except Exception as e:
            self.logger.debug(f"Error forwarding local data: {e}")
        finally:
            self.close_conn(conn_id)

    async def handle_data(self, message):
        conn_id = message.payload.get("conn_id")
        data = message.payload.get("data", b"")

        ftp_data_info = self.state.ftp_data_conns.get(conn_id)
        if ftp_data_info:
            ftp_data_info["last_activity"] = time.time()
            try:
                if ftp_data_info.get("local_writer"):
                    ftp_data_info["local_writer"].write(data)
                    await ftp_data_info["local_writer"].drain()
            except Exception as e:
                self.logger.debug(f"Error writing to FTP data connection: {e}")
                self._close_ftp_data_conn(conn_id)
            return

        conn_info = self.state.conn_pool.get(conn_id)
        if not conn_info:
            return

        conn_info["last_activity"] = time.time()

        try:
            if conn_info.get("local_writer"):
                conn_info["local_writer"].write(data)
                await conn_info["local_writer"].drain()
            elif conn_info.get("visitor_writer"):
                conn_info["visitor_writer"].write(data)
                await conn_info["visitor_writer"].drain()
            elif conn_info.get("udp_transport"):
                conn_info["udp_transport"].sendto(data)
        except Exception as e:
            self.logger.debug(f"Error writing to local: {e}")
            self.close_conn(conn_id)

    async def handle_close(self, message):
        conn_id = message.payload.get("conn_id")
        if conn_id in self.state.ftp_data_conns:
            self._close_ftp_data_conn(conn_id)
        else:
            self.close_conn(conn_id)

    def close_conn(self, conn_id):
        conn_info = self.state.conn_pool.pop(conn_id, None)
        if conn_info:
            try:
                if conn_info.get("local_writer"):
                    conn_info["local_writer"].close()
                    asyncio.create_task(
                        self._safe_wait_closed(conn_info["local_writer"])
                    )
                elif conn_info.get("udp_transport"):
                    conn_info["udp_transport"].close()
                elif conn_info.get("visitor_writer"):
                    conn_info["visitor_writer"].close()
                    asyncio.create_task(
                        self._safe_wait_closed(conn_info["visitor_writer"])
                    )
            except:
                pass

    async def handle_ftp_new_data(self, message):
        """Handle FTP_NEW_DATA from server: establish a data connection to local FTP server."""
        ctrl_conn_id = message.payload.get("ctrl_conn_id")
        data_conn_id = message.payload.get("data_conn_id")
        internal_port = message.payload.get("internal_port")

        if not data_conn_id or not internal_port:
            return

        ctrl_info = self.state.conn_pool.get(ctrl_conn_id) if ctrl_conn_id else None
        proxy_name = ctrl_info["proxy_name"] if ctrl_info else "unknown"
        proxy_info = self.state.proxies.get(proxy_name)

        local_ip = "127.0.0.1"
        if proxy_info:
            local_ip = proxy_info.get("local_ip", "127.0.0.1")

        try:
            local_reader, local_writer = await asyncio.wait_for(
                asyncio.open_connection(local_ip, internal_port), timeout=5
            )
            _optimize_socket(local_writer)

            self.state.ftp_data_conns[data_conn_id] = {
                "local_reader": local_reader,
                "local_writer": local_writer,
                "ctrl_conn_id": ctrl_conn_id,
                "proxy_name": proxy_name,
                "created_at": time.time(),
                "last_activity": time.time(),
            }

            ready_msg = Message(
                MessageType.FTP_DATA_READY,
                data_conn_id=data_conn_id,
            )
            self.control_writer.write(Protocol.encode(ready_msg))
            await self.control_writer.drain()

            asyncio.create_task(self._forward_ftp_data_to_server(data_conn_id))

            self.logger.info(
                f"FTP data connection established: {data_conn_id[:8]} "
                f"-> {local_ip}:{internal_port}"
            )
        except Exception as e:
            self.logger.warning(
                f"Failed to connect to local FTP data port {internal_port}: {e}"
            )

    async def _forward_ftp_data_to_server(self, data_conn_id):
        """Forward data from local FTP server to frps through control/data channel."""
        conn_info = self.state.ftp_data_conns.get(data_conn_id)
        if not conn_info:
            return

        try:
            while True:
                data = await conn_info["local_reader"].read(READ_BUF_SIZE)
                if not data:
                    break

                conn_info["last_activity"] = time.time()
                data_msg = Message(
                    MessageType.DATA,
                    conn_id=data_conn_id,
                    data=data,
                )
                writer = self._data_writer_for(data_conn_id)
                writer.write(Protocol.encode(data_msg))
                await writer.drain()
        except ConnectionResetError:
            pass
        except Exception as e:
            self.logger.debug(f"Error forwarding FTP data to server: {e}")
        finally:
            self._close_ftp_data_conn(data_conn_id)

    def _close_ftp_data_conn(self, data_conn_id):
        conn_info = self.state.ftp_data_conns.pop(data_conn_id, None)
        if conn_info:
            try:
                if conn_info.get("local_writer"):
                    conn_info["local_writer"].close()
            except:
                pass

    async def _safe_wait_closed(self, writer):
        try:
            await writer.wait_closed()
        except:
            pass

    async def handle_stcp_new_visitor(self, message):
        """Handle STCP_NEW_VISITOR from server (provider side).
        
        The provider connects to the local service and sends
        STCP_VISITOR_READY, then forwards data using the existing
        DATA/CLOSE protocol with visitor_conn_id as conn_id.
        """
        proxy_name = message.payload.get("proxy_name")
        visitor_conn_id = message.payload.get("visitor_conn_id")

        proxy_info = self.state.proxies.get(proxy_name)
        if not proxy_info or proxy_info.get("type") != "stcp":
            self.logger.error(
                f"STCP new visitor for unknown/non-stcp proxy: {proxy_name}"
            )
            return

        local_ip = proxy_info.get("local_ip", "127.0.0.1")
        local_port = proxy_info.get("local_port")

        try:
            local_reader, local_writer = await asyncio.wait_for(
                asyncio.open_connection(local_ip, local_port), timeout=5
            )
            _optimize_socket(local_writer)

            self.state.conn_pool[visitor_conn_id] = {
                "proxy_name": proxy_name,
                "local_reader": local_reader,
                "local_writer": local_writer,
                "stcp": True,
                "created_at": time.time(),
                "last_activity": time.time(),
            }

            ready_msg = Message(
                MessageType.STCP_VISITOR_READY,
                proxy_name=proxy_name,
                visitor_conn_id=visitor_conn_id,
            )
            self.control_writer.write(Protocol.encode(ready_msg))
            await self.control_writer.drain()

            asyncio.create_task(self.forward_local_data(visitor_conn_id))

            self.logger.debug(
                f"STCP new visitor connected: {proxy_name} "
                f"({visitor_conn_id}) -> {local_ip}:{local_port}"
            )
        except Exception as e:
            self.logger.warning(
                f"Failed to connect STCP local service {local_ip}:{local_port}: {e}"
            )
            close_msg = Message(MessageType.CLOSE, conn_id=visitor_conn_id)
            try:
                writer = self._data_writer_for(visitor_conn_id)
                writer.write(Protocol.encode(close_msg))
                await writer.drain()
            except:
                pass

    async def _start_stcp_visitor_listener(self, proxy):
        """Start a local listener for an STCP visitor proxy."""
        proxy_name = proxy.get("name")
        bind_addr = proxy.get("bind_addr", "127.0.0.1")
        bind_port = proxy.get("bind_port")

        if not bind_port:
            self.logger.error(
                f"STCP visitor {proxy_name}: bind_port required"
            )
            return

        try:
            server = await asyncio.start_server(
                lambda r, w: self._handle_stcp_visitor_conn(r, w, proxy_name),
                bind_addr,
                bind_port,
            )
            self.logger.info(
                f"STCP visitor listening on {bind_addr}:{bind_port} "
                f"for {proxy_name}"
            )
            async with server:
                await server.serve_forever()
        except Exception as e:
            self.logger.error(
                f"Failed to start STCP visitor listener on {bind_addr}:{bind_port}: {e}"
            )

    async def _handle_stcp_visitor_conn(self, reader, writer, proxy_name):
        """Handle a user connection to the STCP visitor's local port."""
        addr = writer.get_extra_info("peername")
        _optimize_socket(writer)

        proxy_info = self.state.proxies.get(proxy_name)
        if not proxy_info or proxy_info.get("type") != "stcp_visitor":
            writer.close()
            await writer.wait_closed()
            return

        visitor_conn_id = Protocol.generate_conn_id()
        server_name = proxy_info.get("server_name", proxy_name)

        self.logger.debug(
            f"STCP visitor connection from {addr} for {proxy_name} "
            f"({visitor_conn_id})"
        )

        # Register in conn_pool with the visitor_writer
        self.state.conn_pool[visitor_conn_id] = {
            "proxy_name": proxy_name,
            "visitor_writer": writer,
            "visitor_reader": reader,
            "stcp_visitor": True,
            "created_at": time.time(),
            "last_activity": time.time(),
        }

        try:
            # Notify server of new visitor
            new_visitor_msg = Message(
                MessageType.STCP_NEW_VISITOR,
                proxy_name=server_name,
                visitor_conn_id=visitor_conn_id,
            )
            self.control_writer.write(Protocol.encode(new_visitor_msg))
            await self.control_writer.drain()

            # Start forwarding visitor data to server
            asyncio.create_task(self._forward_stcp_visitor_data(visitor_conn_id))
        except Exception as e:
            self.logger.warning(f"Failed to handle STCP visitor conn: {e}")
            self.close_conn(visitor_conn_id)

    async def _forward_stcp_visitor_data(self, conn_id):
        """Forward data from STCP visitor user connection to server."""
        conn_info = self.state.conn_pool.get(conn_id)
        if not conn_info:
            return

        try:
            while True:
                data = await conn_info["visitor_reader"].read(READ_BUF_SIZE)
                if not data:
                    break

                conn_info["last_activity"] = time.time()
                data_msg = Message(
                    MessageType.DATA,
                    conn_id=conn_id,
                    data=data,
                )
                writer = self._data_writer_for(conn_id)
                writer.write(Protocol.encode(data_msg))
                await writer.drain()
        except ConnectionResetError:
            self.logger.debug(f"STCP visitor connection reset: {conn_id}")
        except Exception as e:
            self.logger.debug(f"Error forwarding STCP visitor data: {e}")
        finally:
            self.close_conn(conn_id)

    async def send_heartbeat(self):
        while not self._stop_event.is_set():
            try:
                ping_msg = Message(MessageType.PING)
                self.control_writer.write(Protocol.encode(ping_msg))
                await self.control_writer.drain()
                await asyncio.sleep(30)
            except asyncio.CancelledError:
                break
            except Exception as e:
                self.logger.debug(f"Heartbeat failed: {e}")
                break

    async def stop(self):
        self._stop_event.set()
        await self._cleanup()

    async def _cleanup(self):
        for conn_id in list(self.state.conn_pool.keys()):
            self.close_conn(conn_id)

        self._close_data_channel()

        if self.control_writer:
            try:
                self.control_writer.close()
                await self.control_writer.wait_closed()
            except:
                pass
            self.control_writer = None
            self.control_reader = None

        self.logger.info("FRPClient stopped")


class UDPLocalProtocol(asyncio.DatagramProtocol):
    def __init__(self, client, conn_id):
        self.client = client
        self.conn_id = conn_id
        self.transport = None

    def connection_made(self, transport):
        self.transport = transport

    def datagram_received(self, data, addr):
        data_msg = Message(
            MessageType.DATA,
            conn_id=self.conn_id,
            data=data,
        )
        try:
            writer = self.client._data_writer_for(self.conn_id)
            writer.write(Protocol.encode(data_msg))
            asyncio.create_task(writer.drain())
        except Exception as e:
            self.client.logger.debug(f"UDP forward error: {e}")


def main():
    import argparse

    parser = argparse.ArgumentParser(description="FRP Client")
    parser.add_argument("-c", "--config", default="config/frpc.json", help="Config file")
    args = parser.parse_args()

    config = Config("client").load_from_file(args.config)
    client = FRPClient(config)

    try:
        asyncio.run(client.start())
    except KeyboardInterrupt:
        print("\nFRPClient stopped")


if __name__ == "__main__":
    main()