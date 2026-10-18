"""The GitHub proxy of the VPS (vps/github-proxy/proxy.py): PAC file, refusals and a real CONNECT tunnel."""
import asyncio
import importlib.util
import pathlib

import pytest

PATH = pathlib.Path(__file__).resolve().parents[2] / "vps" / "github-proxy" / "proxy.py"


@pytest.fixture
def proxy():
    spec = importlib.util.spec_from_file_location("gh_proxy", PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_pac(proxy):
    text = proxy.pac().decode()
    head, _, body = text.partition("\r\n\r\n")
    assert head.startswith("HTTP/1.1 200 OK") and f"Content-Length: {len(body)}" in head
    assert 'dnsDomainIs(host, ".github.com")' in body and f"PROXY {proxy.LISTEN}:{proxy.PORT}; DIRECT" in body


def test_requests(proxy):
    async def go():
        async def echo(reader, writer):
            data = await reader.read(100)
            writer.write(b"echo:" + data)
            await writer.drain()
            writer.close()
        upstream = await asyncio.start_server(echo, "127.0.0.1", 0)
        up_port = upstream.sockets[0].getsockname()[1]
        proxy.ALLOWED_PORTS = {up_port}
        server = await asyncio.start_server(proxy.handle, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]

        async def ask(raw: bytes, then: bytes = b"") -> bytes:
            r, w = await asyncio.open_connection("127.0.0.1", port)
            w.write(raw)
            await w.drain()
            if then:
                head = await r.readuntil(b"\r\n\r\n")
                w.write(then)
                await w.drain()
                return head + await r.read(1000)
            out = await r.read(10000)
            w.close()
            return out
        try:
            assert b"FindProxyForURL" in await ask(b"GET /proxy.pac HTTP/1.1\r\nHost: x\r\n\r\n")
            assert b"405" in await ask(b"POST / HTTP/1.1\r\n\r\n")
            assert b"400" in await ask(b"nonsense\r\n\r\n")
            assert b"400" in await ask(b"CONNECT github.com HTTP/1.1\r\n\r\n")
            assert b"403" in await ask(b"CONNECT github.com:22 HTTP/1.1\r\n\r\n")
            tunnel = await ask(f"CONNECT 127.0.0.1:{up_port} HTTP/1.1\r\n\r\n".encode(), b"hello")
            assert tunnel.startswith(b"HTTP/1.1 200 Connection established") and tunnel.endswith(b"echo:hello")
            upstream.close()
            await upstream.wait_closed()
            assert b"502" in await ask(f"CONNECT 127.0.0.1:{up_port} HTTP/1.1\r\n\r\n".encode())
        finally:
            server.close()
            await server.wait_closed()
    asyncio.run(go())


def test_tune_without_a_socket(proxy):
    class Writer:
        def get_extra_info(self, name):
            return None
    proxy.tune(Writer())  # nothing to tune
