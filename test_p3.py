#!/usr/bin/env python3
"""Integration test for HTTP proxy and STCP features."""

import asyncio
import sys
import os
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from frps import FRPServer
from frpc import FRPClient
from config import Config
from log import get_logger

logger = get_logger("test_p3", "INFO")


async def test_http_server(reader, writer):
    """Simple test HTTP server."""
    data = await reader.read(4096)
    request = data.decode("utf-8", errors="ignore")
    first_line = request.split("\r\n")[0] if "\r\n" in request else request
    response_body = f"Hello from test server! Request: {first_line}\n"
    response = (
        f"HTTP/1.1 200 OK\r\n"
        f"Content-Type: text/plain\r\n"
        f"Content-Length: {len(response_body)}\r\n"
        f"\r\n"
        f"{response_body}"
    )
    writer.write(response.encode())
    await writer.drain()
    writer.close()
    await writer.wait_closed()


async def test_echo_server(reader, writer):
    """Simple echo server for STCP testing."""
    try:
        while True:
            data = await reader.read(1024)
            if not data:
                break
            writer.write(data)
            await writer.drain()
    except:
        pass
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except:
            pass


async def start_local_http_server(port):
    server = await asyncio.start_server(test_http_server, "127.0.0.1", port)
    logger.info(f"Test HTTP server on 127.0.0.1:{port}")
    return server


async def start_local_echo_server(port):
    server = await asyncio.start_server(test_echo_server, "127.0.0.1", port)
    logger.info(f"Test echo server on 127.0.0.1:{port}")
    return server


async def test_http_proxy():
    """Test HTTP vhost proxy."""
    logger.info("\n=== Testing HTTP Proxy ===")
    
    # Find free ports
    bind_port = 17000
    vhost_port = 18080
    local_port = 19080

    # Start local test HTTP server
    local_server = await start_local_http_server(local_port)
    await asyncio.sleep(0.2)

    # Start frps
    frps_config_data = {
        "bind_port": bind_port,
        "vhost_http_port": vhost_port,
        "subdomain_host": "test.local",
        "log_level": "WARNING",
    }
    frps = FRPServer(frps_config_data)
    frps_task = asyncio.create_task(frps.start())
    await asyncio.sleep(0.3)

    # Start frpc
    frpc_config_data = {
        "server_addr": "127.0.0.1",
        "server_port": bind_port,
        "log_level": "WARNING",
        "reconnect": False,
        "proxies": [
            {
                "name": "test_http",
                "type": "http",
                "local_port": local_port,
                "local_ip": "127.0.0.1",
                "custom_domains": ["test.example.com"],
                "subdomain": "web",
            }
        ],
    }
    frpc = FRPClient(frpc_config_data)
    frpc_task = asyncio.create_task(frpc.start())
    await asyncio.sleep(0.5)

    try:
        # Test custom domain routing
        logger.info("Testing custom domain routing...")
        reader, writer = await asyncio.open_connection("127.0.0.1", vhost_port)
        request = "GET /test HTTP/1.1\r\nHost: test.example.com\r\n\r\n"
        writer.write(request.encode())
        await writer.drain()
        
        response = await reader.read(4096)
        response_text = response.decode("utf-8", errors="ignore")
        writer.close()
        await writer.wait_closed()

        assert "200 OK" in response_text, f"Expected 200 OK, got: {response_text[:200]}"
        assert "Hello from test server" in response_text, f"Expected server response"
        assert "GET /test HTTP/1.1" in response_text, f"Request not forwarded correctly"
        logger.info("  Custom domain routing: PASS")

        # Test subdomain routing
        logger.info("Testing subdomain routing...")
        reader, writer = await asyncio.open_connection("127.0.0.1", vhost_port)
        request = "GET /sub HTTP/1.1\r\nHost: web.test.local\r\n\r\n"
        writer.write(request.encode())
        await writer.drain()
        
        response = await reader.read(4096)
        response_text = response.decode("utf-8", errors="ignore")
        writer.close()
        await writer.wait_closed()

        assert "200 OK" in response_text, f"Expected 200 OK, got: {response_text[:200]}"
        assert "GET /sub HTTP/1.1" in response_text, f"Subdomain request not forwarded"
        logger.info("  Subdomain routing: PASS")

        # Test 404 for unknown host
        logger.info("Testing unknown host 404...")
        reader, writer = await asyncio.open_connection("127.0.0.1", vhost_port)
        request = "GET / HTTP/1.1\r\nHost: unknown.example.com\r\n\r\n"
        writer.write(request.encode())
        await writer.drain()
        
        response = await reader.read(4096)
        response_text = response.decode("utf-8", errors="ignore")
        writer.close()
        await writer.wait_closed()

        assert "404 Not Found" in response_text, f"Expected 404, got: {response_text[:200]}"
        logger.info("  Unknown host 404: PASS")

        logger.info("HTTP Proxy: ALL TESTS PASSED")
        return True

    except Exception as e:
        logger.error(f"HTTP proxy test failed: {e}")
        import traceback
        traceback.print_exc()
        return False

    finally:
        # Cleanup
        await frpc.stop()
        await frps.stop()
        local_server.close()
        await local_server.wait_closed()
        frpc_task.cancel()
        frps_task.cancel()
        try:
            await frpc_task
        except:
            pass
        try:
            await frps_task
        except:
            pass
        await asyncio.sleep(0.2)


async def test_stcp():
    """Test STCP (Secret TCP) proxy."""
    logger.info("\n=== Testing STCP ===")
    
    bind_port = 17001
    stcp_provider_local_port = 19001
    stcp_visitor_bind_port = 19002

    # Start local echo server (provider side)
    echo_server = await start_local_echo_server(stcp_provider_local_port)
    await asyncio.sleep(0.2)

    # Start frps
    frps_config_data = {
        "bind_port": bind_port,
        "log_level": "WARNING",
    }
    frps = FRPServer(frps_config_data)
    frps_task = asyncio.create_task(frps.start())
    await asyncio.sleep(0.3)

    # Start frpc - provider side
    provider_config = {
        "server_addr": "127.0.0.1",
        "server_port": bind_port,
        "log_level": "WARNING",
        "reconnect": False,
        "proxies": [
            {
                "name": "test_stcp",
                "type": "stcp",
                "local_port": stcp_provider_local_port,
                "local_ip": "127.0.0.1",
                "sk": "test_secret_key",
            }
        ],
    }
    provider = FRPClient(provider_config)
    provider_task = asyncio.create_task(provider.start())
    await asyncio.sleep(0.3)

    # Start frpc - visitor side
    visitor_config = {
        "server_addr": "127.0.0.1",
        "server_port": bind_port,
        "log_level": "WARNING",
        "reconnect": False,
        "proxies": [
            {
                "name": "test_stcp_visitor",
                "type": "stcp_visitor",
                "server_name": "test_stcp",
                "sk": "test_secret_key",
                "bind_port": stcp_visitor_bind_port,
                "bind_addr": "127.0.0.1",
            }
        ],
    }
    visitor = FRPClient(visitor_config)
    visitor_task = asyncio.create_task(visitor.start())
    await asyncio.sleep(0.5)

    try:
        # Test STCP echo through visitor port
        logger.info("Testing STCP echo...")
        reader, writer = await asyncio.open_connection("127.0.0.1", stcp_visitor_bind_port)
        
        test_data = b"Hello STCP! This is a test message."
        writer.write(test_data)
        await writer.drain()
        
        response = b""
        try:
            response = await asyncio.wait_for(reader.read(1024), timeout=5)
        except asyncio.TimeoutError:
            pass
        
        writer.close()
        await writer.wait_closed()

        assert response == test_data, f"Expected echo response, got: {response!r}"
        logger.info("  STCP echo: PASS")

        logger.info("STCP: ALL TESTS PASSED")
        return True

    except Exception as e:
        logger.error(f"STCP test failed: {e}")
        import traceback
        traceback.print_exc()
        return False

    finally:
        # Cleanup
        await provider.stop()
        await visitor.stop()
        await frps.stop()
        echo_server.close()
        await echo_server.wait_closed()
        provider_task.cancel()
        visitor_task.cancel()
        frps_task.cancel()
        try:
            await provider_task
        except:
            pass
        try:
            await visitor_task
        except:
            pass
        try:
            await frps_task
        except:
            pass
        await asyncio.sleep(0.2)


async def main():
    results = {}
    
    results["HTTP Proxy"] = await test_http_proxy()
    results["STCP"] = await test_stcp()
    
    logger.info("\n=== Test Summary ===")
    all_passed = True
    for name, passed in results.items():
        status = "PASS" if passed else "FAIL"
        logger.info(f"  {name}: {status}")
        if not passed:
            all_passed = False
    
    if all_passed:
        logger.info("\nAll tests passed!")
        return 0
    else:
        logger.error("\nSome tests failed!")
        return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
