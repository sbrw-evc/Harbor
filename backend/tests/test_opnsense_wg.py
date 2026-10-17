"""WireGuard on OPNsense: the adapter and the backend against a fake OPNsense REST API (httpx.MockTransport)."""
import asyncio
import base64
import contextlib
import json

import httpx
import pytest

from app.adapters.opnsense_wg import SECRET_PATH, OpnsenseWireguard
from app.domain.errors import DomainError, ExternalServiceError
from app.ports import SecretStore
from app.services.backends import OpnsenseBackend
from app.services.settings import DEFAULTS, validate

SERVER_KEY = "S" * 43 + "="
INSTANCE = "a1b2c3d4-0000-4000-8000-000000000001"
OTHER = "a1b2c3d4-0000-4000-8000-000000000002"


def key(ch: str) -> str:
    return ch * 43 + "="


class MemorySecrets(SecretStore):
    def __init__(self, data: dict | None = None):
        self.data = dict(data or {})

    async def get(self, path):
        return self.data.get(path)

    async def put(self, path, data):
        self.data[path] = data

    async def delete(self, path):
        self.data.pop(path, None)

    async def healthy(self):
        return True


class FakeSettings:
    def __init__(self, **values):
        self.values = {**DEFAULTS, "opn_host": "192.168.0.50", **values}

    async def get(self, k):
        return self.values[k]

    async def all(self):
        return dict(self.values)


class FakeLock:
    @contextlib.asynccontextmanager
    async def hold(self):
        yield


class FakeOpnsense:
    """The parts of /api/wireguard the panel uses, as OPNsense 26.7 answers them."""

    def __init__(self):
        self.servers = {
            INSTANCE: {"enabled": "1", "name": "wg0", "instance": "0", "port": "51821", "pubkey": SERVER_KEY,
                       "tunneladdress": "10.10.0.1/24"},
            OTHER: {"enabled": "1", "name": "site2site", "instance": "1", "port": "51822", "pubkey": key("O"),
                    "tunneladdress": "10.20.0.1/24"},
        }
        self.clients = {
            "c-1": {"enabled": "1", "name": "phone", "pubkey": key("A"), "psk": "", "tunneladdress": "10.10.0.2/32",
                    "servers": INSTANCE},
            "c-2": {"enabled": "0", "name": "laptop", "pubkey": key("B"), "psk": "", "tunneladdress": "10.10.0.3/32",
                    "servers": INSTANCE},
            "c-3": {"enabled": "1", "name": "branch", "pubkey": key("C"), "psk": "", "tunneladdress": "10.20.0.2/32",
                    "servers": OTHER},
        }
        self.calls = []
        self.reconfigured = 0
        self.auth = ("k", "s")

    def _names(self, uuids: str) -> str:
        return ",".join(self.servers[u]["name"] for u in uuids.split(",") if u in self.servers)

    def handler(self, req: httpx.Request) -> httpx.Response:
        path = req.url.path
        body = json.loads(req.content) if req.content else {}
        self.calls.append((req.method, path, body))
        expected = "Basic " + base64.b64encode(f"{self.auth[0]}:{self.auth[1]}".encode()).decode()
        if req.headers.get("authorization") != expected:
            return httpx.Response(401, json={"status": 401, "message": "Authentication Failed"})
        if path == "/api/wireguard/server/searchServer":
            return httpx.Response(200, json={"rows": [
                {"uuid": u, "enabled": s["enabled"], "name": s["name"], "port": s["port"],
                 "tunneladdress": s["tunneladdress"], "interface": f"wg{s['instance']}"}
                for u, s in self.servers.items()], "total": len(self.servers)})
        if path.startswith("/api/wireguard/server/getServer/"):
            s = self.servers[path.rsplit("/", 1)[1]]
            return httpx.Response(200, json={"server": {
                **s, "tunneladdress": {s["tunneladdress"]: {"value": s["tunneladdress"], "selected": 1}}}})
        if path == "/api/wireguard/client/searchClient":
            # The search shows the instances by their names.
            return httpx.Response(200, json={"rows": [
                {"uuid": u, "enabled": c["enabled"], "name": c["name"], "pubkey": c["pubkey"],
                 "tunneladdress": c["tunneladdress"], "servers": self._names(c["servers"])}
                for u, c in self.clients.items()]})
        if path.startswith("/api/wireguard/client/getClient/"):
            c = self.clients[path.rsplit("/", 1)[1]]
            return httpx.Response(200, json={"client": {**c, "servers": {
                u: {"value": s["name"], "selected": int(u in c["servers"].split(","))}
                for u, s in self.servers.items()}}})
        if path == "/api/wireguard/client/addClient":
            c = body["client"]
            if not c.get("pubkey"):
                return httpx.Response(200, json={"result": "failed", "validations": {"client.pubkey": "required"}})
            uuid = f"c-{len(self.clients) + 1}"
            self.clients[uuid] = {**c}
            return httpx.Response(200, json={"result": "saved", "uuid": uuid})
        if path.startswith("/api/wireguard/client/setClient/"):
            c = self.clients[path.rsplit("/", 1)[1]]
            c.update(body["client"])
            if "servers" not in body["client"]:
                c["servers"] = ""
            return httpx.Response(200, json={"result": "saved"})
        if path.startswith("/api/wireguard/client/delClient/"):
            self.clients.pop(path.rsplit("/", 1)[1])
            return httpx.Response(200, json={"result": "deleted"})
        if path == "/api/wireguard/service/reconfigure":
            assert req.method == "POST"
            self.reconfigured += 1
            return httpx.Response(200, json={"status": "ok"})
        if path == "/api/wireguard/service/show":
            return httpx.Response(200, json={"records": [
                {"if": "wg0", "type": "interface", "public-key": SERVER_KEY, "listen-port": "51821"},
                {"if": "wg0", "type": "peer", "public-key": key("A"), "endpoint": "198.51.100.7:40000",
                 "allowed-ips": "10.10.0.2/32", "latest-handshake": "1760000000", "transfer-rx": "1000",
                 "transfer-tx": "2000"},
                {"if": "wg0", "type": "peer", "public-key": key("B"), "endpoint": "(none)",
                 "allowed-ips": "10.10.0.3/32", "latest-handshake": "0", "transfer-rx": "0", "transfer-tx": "0"},
                {"if": "wg1", "type": "peer", "public-key": key("C"), "endpoint": "203.0.113.9:51822",
                 "allowed-ips": "10.20.0.2/32", "latest-handshake": "1760000000", "transfer-rx": "5",
                 "transfer-tx": "6"},
            ], "status": "ok"})
        return httpx.Response(404, json={"errorMessage": "Endpoint not found"})


def make(fake: FakeOpnsense | None = None, secrets: dict | None = None, **settings):
    fake = fake or FakeOpnsense()
    store = MemorySecrets({SECRET_PATH: {"key": "k", "secret": "s"}} if secrets is None else secrets)
    st = FakeSettings(**settings)
    wg = OpnsenseWireguard(st.values["opn_host"], st.values["opn_wg_instance"], False, store,
                           transport=httpx.MockTransport(fake.handler))

    async def public_address():
        return "203.0.113.1"

    backend = OpnsenseBackend(wg, st, store, FakeLock(), lambda: st.values["opn_host"], public_address)
    return fake, backend, store


def test_instance_and_peers():
    fake, b, _ = make()

    async def go():
        assert await b.configured() and await b.ready()
        inst = await b.wg.instance()
        assert inst["uuid"] == INSTANCE and inst["device"] == "wg0" and inst["port"] == 51821
        assert inst["network"] == "10.10.0.0/24" and inst["server_ip"] == "10.10.0.1"
        assert inst["public_key"] == SERVER_KEY
        peers = await b.peers()
        assert {(p.public_key, p.address, p.disabled) for p in peers} == {
            (key("A"), "10.10.0.2/32", False), (key("B"), "10.10.0.3/32", True)}
        assert await b.names() == {key("A"): "phone", key("B"): "laptop"}
        rt = await b.runtime()
        assert set(rt) == {key("A"), key("B")}
        assert rt[key("A")].rx == 1000 and rt[key("A")].tx == 2000 and rt[key("A")].latest_handshake == 1760000000
        assert rt[key("A")].endpoint == "198.51.100.7:40000" and rt[key("B")].endpoint is None
        info = await b.info()
        assert info["found"] and info["credentials"] and info["peers_total"] == 2 and info["peers_disabled"] == 1
        assert info["public_address"] == "203.0.113.1" and info["error"] is None

    asyncio.run(go())


def test_instance_by_device_name():
    fake, b, _ = make(opn_wg_instance="wg1")

    async def go():
        inst = await b.wg.instance()
        assert inst["uuid"] == OTHER and inst["name"] == "site2site"
        assert set(await b.names()) == {key("C")}

    asyncio.run(go())


def test_add_peer_takes_a_free_address_and_binds_to_the_instance():
    fake, b, _ = make()

    async def go():
        peer = await b.add_peer(key("D"), "Новый телефон!")
        assert peer.address == "10.10.0.4/32" and peer.preshared_key
        added = next(c for c in fake.clients.values() if c["pubkey"] == key("D"))
        assert added["servers"] == INSTANCE and added["tunneladdress"] == "10.10.0.4/32"
        assert added["psk"] == peer.preshared_key and added["enabled"] == "1"
        assert added["name"] == "_____ ________"
        assert fake.reconfigured == 1

    asyncio.run(go())


def test_changes_keep_the_peer_on_its_instance():
    fake, b, _ = make()

    async def go():
        await b.set_disabled(key("A"), True)
        assert fake.clients["c-1"]["enabled"] == "0" and fake.clients["c-1"]["servers"] == INSTANCE
        await b.set_disabled(key("A"), False)
        await b.rename_peer(key("A"), "phone 2", "10.10.0.2/32")
        await b.rekey_peer(key("A"), key("E"))
        c = fake.clients["c-1"]
        assert (c["enabled"], c["name"], c["pubkey"], c["servers"]) == ("1", "phone 2", key("E"), INSTANCE)
        sets = [body for m, p, body in fake.calls if p.startswith("/api/wireguard/client/setClient/")]
        assert sets and all("servers" in body["client"] for body in sets)
        await b.remove_peer(key("E"))
        assert "c-1" not in fake.clients
        assert fake.reconfigured == 5
        with pytest.raises(DomainError):
            await b.remove_peer(key("C"))  # a peer of another instance is not ours

    asyncio.run(go())


def test_bundle():
    fake, b, _ = make(opn_endpoint_host="vpn.example.com", opn_allowed_ips="0.0.0.0/0, ::/0")

    async def go():
        bundle = await b.bundle(name="phone", private_key=key("P"), public_key=key("A"), address="10.10.0.2/32",
                                preshared_key=key("Q"))
        conf = bundle.config
        assert f"PublicKey = {SERVER_KEY}" in conf and "Endpoint = vpn.example.com:51821" in conf
        assert f"PresharedKey = {key('Q')}" in conf and "DNS = 10.10.0.1" in conf
        assert "AllowedIPs = 0.0.0.0/0, ::/0" in conf and bundle.qr_svg.startswith("<")

    asyncio.run(go())


def test_bundle_falls_back_to_the_router_address():
    fake, b, _ = make(opn_client_dns="192.168.0.1")

    async def go():
        conf = (await b.bundle(name="x", private_key=key("P"), public_key=key("A"), address="10.10.0.2/32",
                               preshared_key=None)).config
        assert "Endpoint = 203.0.113.1:51821" in conf and "DNS = 192.168.0.1" in conf and "PresharedKey" not in conf

    asyncio.run(go())


def test_credentials_only_in_the_secret_store():
    fake, b, store = make(secrets={})

    async def go():
        assert not await b.configured()
        assert not (await b.info())["credentials"]
        with pytest.raises(DomainError):
            await b.add_peer(key("D"), "x")
        with pytest.raises(ExternalServiceError):
            await b.set_credentials("wrong", "wrong")
        assert SECRET_PATH not in store.data
        await b.set_credentials(" k ", "s")
        assert store.data[SECRET_PATH] == {"key": "k", "secret": "s"}
        assert await b.ready()

    asyncio.run(go())


def test_unreachable_and_denied():
    fake, b, _ = make()
    fake.auth = ("other", "key")

    async def go():
        with pytest.raises(ExternalServiceError, match="denied"):
            await b.runtime()
        assert (await b.info())["error"]

    asyncio.run(go())

    def down(req):
        raise httpx.ConnectError("refused")

    store = MemorySecrets({SECRET_PATH: {"key": "k", "secret": "s"}})
    wg = OpnsenseWireguard("192.168.0.50", "wg0", False, store, transport=httpx.MockTransport(down))
    with pytest.raises(ExternalServiceError, match="unreachable"):
        asyncio.run(wg.runtime())


def test_settings_validation():
    out = validate({"opn_host": "192.168.0.50", "opn_wg_instance": "wg0", "opn_verify_tls": "false",
                    "opn_client_dns": "192.168.0.1 1.1.1.1", "opn_allowed_ips": "0.0.0.0/0,10.10.0.0/24",
                    "opn_endpoint_host": "VPN.example.com"})
    assert out["opn_verify_tls"] is False and out["opn_client_dns"] == "192.168.0.1, 1.1.1.1"
    assert out["opn_allowed_ips"] == "0.0.0.0/0, 10.10.0.0/24" and out["opn_endpoint_host"] == "vpn.example.com"
    for bad in ({"opn_wg_instance": "wg 0"}, {"opn_allowed_ips": ""}, {"opn_client_dns": "dns.example"}):
        with pytest.raises(ValueError):
            validate(bad)
