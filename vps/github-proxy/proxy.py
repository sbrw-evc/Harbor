import asyncio
import os
import socket

LISTEN = os.environ.get("LISTEN", "10.8.1.0")  # NOSONAR: the tunnel address of the VPS, only reachable through it
PORT = int(os.environ.get("PORT", "3128"))
CC = os.environ.get("CC", "bbr").encode()
ALLOWED_PORTS = {int(p) for p in os.environ.get("ALLOWED_PORTS", "443").split(",")}
BUF = 256 * 1024
PAC_DOMAINS = [d.strip() for d in os.environ.get(
    "PAC_DOMAINS", "github.com,githubusercontent.com,githubassets.com,ghcr.io").split(",") if d.strip()]
PAC_PROXIES = [p.strip() for p in os.environ.get("PAC_PROXIES", f"{LISTEN}:{PORT}").split(",") if p.strip()]


def pac() -> bytes:
    cond = " || ".join(f'host == "{d}" || dnsDomainIs(host, ".{d}")' for d in PAC_DOMAINS)
    chain = "; ".join(f"PROXY {p}" for p in PAC_PROXIES) + "; DIRECT"
    body = (f"function FindProxyForURL(url, host) {{\n"
            f"  if ({cond}) return \"{chain}\";\n"
            f"  return \"DIRECT\";\n}}\n").encode()
    return (b"HTTP/1.1 200 OK\r\nContent-Type: application/x-ns-proxy-autoconfig\r\n"
            b"Cache-Control: max-age=300\r\nContent-Length: " + str(len(body)).encode() + b"\r\n\r\n" + body)


def tune(writer: asyncio.StreamWriter) -> None:
    sock = writer.get_extra_info("socket")
    if sock is None:
        return
    for opt, val in ((socket.TCP_CONGESTION, CC), (socket.TCP_NODELAY, 1)):
        try:
            sock.setsockopt(socket.IPPROTO_TCP, opt, val)
        except OSError:
            pass


async def pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while data := await reader.read(BUF):
            writer.write(data)
            await writer.drain()
    except (ConnectionError, asyncio.IncompleteReadError, OSError):
        pass
    finally:
        try:
            writer.close()
        except Exception:
            pass


async def reply(writer: asyncio.StreamWriter, status: str) -> None:
    writer.write(f"HTTP/1.1 {status}\r\nContent-Length: 0\r\n\r\n".encode())
    try:
        await writer.drain()
    finally:
        writer.close()


async def handle(cr: asyncio.StreamReader, cw: asyncio.StreamWriter) -> None:
    tune(cw)
    try:
        head = await asyncio.wait_for(cr.readuntil(b"\r\n\r\n"), 15)
        method, target, _ = head.split(b"\r\n", 1)[0].decode("latin-1").split(" ", 2)
    except Exception:
        await reply(cw, "400 Bad Request")
        return
    if method == "GET" and target.split("?")[0] in ("/proxy.pac", "/wpad.dat"):
        cw.write(pac())
        await cw.drain()
        cw.close()
        return
    if method != "CONNECT":
        await reply(cw, "405 Method Not Allowed")
        return
    try:
        host, port = target.rsplit(":", 1)
        port = int(port)
    except ValueError:
        await reply(cw, "400 Bad Request")
        return
    if port not in ALLOWED_PORTS:
        await reply(cw, "403 Forbidden")
        return
    try:
        ur, uw = await asyncio.wait_for(asyncio.open_connection(host.strip("[]"), port), 15)
    except Exception:
        await reply(cw, "502 Bad Gateway")
        return
    cw.write(b"HTTP/1.1 200 Connection established\r\n\r\n")
    await cw.drain()
    await asyncio.gather(pipe(cr, uw), pipe(ur, cw))


async def main() -> None:
    server = await asyncio.start_server(handle, LISTEN, PORT, backlog=512)
    print(f"listening on {LISTEN}:{PORT}, cc={CC.decode()}, ports={sorted(ALLOWED_PORTS)}", flush=True)
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(main())
