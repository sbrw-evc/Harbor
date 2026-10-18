import base64
import io
import json
import zlib

import qrcode
import qrcode.image.svg

from ..domain.models import ClientBundle
from .common import current
from .server import ServerConfig


def qr_svg(text: str) -> str:
    img = qrcode.make(text, image_factory=qrcode.image.svg.SvgPathImage, box_size=8, border=2)
    buf = io.BytesIO()
    img.save(buf)
    return buf.getvalue().decode()


def wg_conf(*, private_key: str, address: str, dns: str, server_public_key: str, psk: str | None, allowed_ips: str,
            endpoint: str, extra: dict | None = None, mtu: int | None = None) -> str:
    obfs = "".join(f"{k} = {v}\n" for k, v in (extra or {}).items())
    mtu_line = f"MTU = {mtu}\n" if mtu else ""
    dns_line = f"DNS = {dns}\n" if dns else ""
    psk_line = f"PresharedKey = {psk}\n" if psk else ""
    return (f"[Interface]\nAddress = {address}\n{dns_line}PrivateKey = {private_key}\n{mtu_line}{obfs}\n"
            f"[Peer]\nPublicKey = {server_public_key}\n{psk_line}AllowedIPs = {allowed_ips}\n"
            f"Endpoint = {endpoint}\nPersistentKeepalive = 25\n")


class ClientConfigBuilder:
    def __init__(self, container_name: str):
        self._container = container_name

    def build(self, *, name: str, server: ServerConfig, server_public_key: str, psk: str | None,
              private_key: str, public_key: str, address: str, endpoint_host: str, dns: str) -> ClientBundle:
        conf = wg_conf(private_key=private_key, address=address, dns=dns, server_public_key=server_public_key, psk=psk,
                       allowed_ips="0.0.0.0/0, ::/0", endpoint=f"{endpoint_host}:{server.port}",
                       extra=server.obfuscation)
        return ClientBundle(name=name, config=conf,
                            amnezia_key=self._amnezia_key(name, conf, server, server_public_key, psk, private_key,
                                                          public_key, address, endpoint_host, dns),
                            qr_svg=qr_svg(conf))

    def _amnezia_key(self, name, conf, server, server_pub, psk, priv, pub, address, host, dns) -> str:
        obfs = server.obfuscation
        dns_list = [d.strip() for d in dns.split(",") if d.strip()] or ["1.1.1.1"]  # NOSONAR: a public resolver
        last = dict(obfs, config=conf, hostName=host, port=int(server.port or 0), mtu="1376",
                    client_ip=address.split("/")[0], client_priv_key=priv, client_pub_key=pub,
                    server_pub_key=server_pub, psk_key=psk or "", clientId=pub)
        awg = dict(obfs, last_config=json.dumps(last, indent=4), port=str(server.port), transport_proto="udp")
        name = current(self._container)
        data = {"containers": [{"awg": awg, "container": name}], "defaultContainer": name,
                "description": name, "dns1": dns_list[0], "dns2": dns_list[-1], "hostName": host}
        raw = json.dumps(data).encode()
        packed = len(raw).to_bytes(4, "big") + zlib.compress(raw, 8)
        return "vpn://" + base64.urlsafe_b64encode(packed).decode().rstrip("=")
