"""The MikroTik router over SSH (address list, WireGuard) and the SSH runner of the panel."""
import asyncio
import os
import stat

import pytest

from app.adapters import ssh as sshmod
from app.adapters.mikrotik import MikrotikSshRouter
from app.adapters.mikrotik_wg import MikrotikWireguard, RouterOsRunner, parse_duration, safe_comment
from app.adapters.ssh import KEY_SECRET, SshKeyProvider, SshRunner
from app.domain.errors import ExternalServiceError

KEY_A = "A" * 43 + "="
KEY_B = "B" * 43 + "="


class Runner:
    def __init__(self, answers=None):
        self.answers, self.ran = answers or {}, []

    async def run(self, cmd, stdin=None, timeout=30):
        self.ran.append(cmd)
        for needle, out in self.answers.items():
            if needle in cmd:
                return out
        return ""


def run(coro):
    return asyncio.run(coro)


STATUS = """count=1200
route=10.8.1.1|true|1
route=192.168.0.1|false|2
mangle=amnezia: mark|1000|10
mangle=amnezia: route||
cpu=7
uptime=1w2d
version=7.14.2 (stable)
board=RB5009UG+S+
garbage
"""


def test_router_status_and_lists():
    r = Runner({"count-only": STATUS, "dynamic=no] do=": "1.2.3.0/24\n5.6.7.8/32\n\n",
                "list=vpn-sites] do=": "1.2.3.0/24\nexample.com\n9.9.9.9/32\n"})
    router = MikrotikSshRouter(r)

    async def go():
        st = await router.status()
        assert st["count"] == 1200 and st["cpu"] == 7 and st["board"] == "RB5009UG+S+" and st["tunnel_active"]
        assert st["routes"][1] == {"gateway": "192.168.0.1", "active": False, "distance": 2}
        assert st["mangle"][1] == {"comment": "amnezia: route", "bytes": 0, "packets": 0}
        assert await router.list_addresses() == ["1.2.3.0/24", "9.9.9.9"]  # host names are left out
        await router.add_address("203.0.113.5/32")
        assert r.ran[-1] == "/ip firewall address-list add list=vpn-sites address=203.0.113.5 comment=custom"
        await router.remove_address("1.2.3.0/24")
        assert "remove [find list=vpn-sites address=1.2.3.0/24 dynamic=no]" in r.ran[-1]
        with pytest.raises(ValueError, match="bad address"):
            await router.add_address("1.2.3.4; /system reboot")
        n = await router.replace_address_list(["1.2.3.0/24", "10.0.0.0/8"] + [f"198.51.100.{i}" for i in range(1, 302)])
        assert n == 303
        removes = [c for c in r.ran if c.startswith("/ip firewall address-list {") and "remove" in c]
        adds = [c for c in r.ran if c.startswith("/ip firewall address-list {") and " add " in c]
        assert len(removes) == 1 and "address=5.6.7.8 " in removes[0] and len(adds) == 2  # 302 new: two batches
        with pytest.raises(ValueError):
            await router.replace_address_list(["not an address!"])
    run(go())
    assert run(MikrotikSshRouter(Runner({"count-only": "count=0\n"})).status())["tunnel_active"] is False


PEERS = (f"{KEY_A}|198.51.100.7|40000|1m30s|100|200|10.9.0.2/32;10.9.0.3/32|false|amnezia-control: phone\n"
         f"{KEY_B}|0.0.0.0||||||true|amnezia-control: tv\n"
         "|x|x|x|x|x|x|x|x\nshort|line\n")


def test_wireguard():
    r = Runner({'wireguard find where name="wg-nc"]; :if': "key=SRVPUB\nport=51820\nrunning=true\ndisabled=false\nmtu=1420\n",
                "/ip cloud get public-address": "203.0.113.9\n", ":foreach p in=": PEERS})
    wg = MikrotikWireguard(r, "wg-nc", "10.9.0.0/24", 51820)
    assert str(wg.server_ip) == "10.9.0.1"

    async def go():
        assert await wg.interface() == {"public_key": "SRVPUB", "listen_port": 51820, "running": True, "disabled": False, "mtu": 1420}
        assert await wg.public_address() == "203.0.113.9"
        await wg.setup()
        script = r.ran[-1]
        assert "/interface wireguard add name=wg-nc listen-port=51820 mtu=1420" in script
        assert "address=10.9.0.1/24 interface=wg-nc" in script and script.count("/ip firewall mangle add") == 2
        peers = await wg.peers()
        a, b = peers
        assert (a["endpoint"], a["address"], a["rx"], a["disabled"], a["comment"]) == ("198.51.100.7:40000", "10.9.0.2/32,10.9.0.3/32", 100, False, "amnezia-control: phone")
        assert a["latest_handshake"] > 0 and b["endpoint"] is None and b["latest_handshake"] == 0 and b["disabled"]
        rt = await wg.runtime()
        assert rt[KEY_A].allowed_ips == "10.9.0.2/32,10.9.0.3/32" and rt[KEY_A].tx == 200
        assert [(e.public_key, e.disabled) for e in await wg.peer_entries()] == [(KEY_A, False), (KEY_B, True)]
        await wg.add_peer(KEY_A, KEY_B, "10.9.0.4/32", "Anna's phone; /system reset")
        assert 'comment="amnezia-control: Anna_s phone_ _system reset"' in r.ran[-1]
        with pytest.raises(ValueError, match="bad wireguard key"):
            await wg.add_peer("short", KEY_B, "10.9.0.4/32", "x")
        with pytest.raises(ValueError):
            await wg.add_peer(KEY_A, KEY_B, "not-an-ip", "x")
        await wg.set_peer(KEY_A, disabled=True, new_key=KEY_B, name="tv")
        assert r.ran[-1].endswith(f'disabled=yes public-key="{KEY_B}" comment="amnezia-control: tv"')
        before = len(r.ran)
        await wg.set_peer(KEY_A)  # nothing to change: nothing sent
        assert len(r.ran) == before
        await wg.remove_peer(KEY_A)
        assert r.ran[-1].startswith("/interface wireguard peers remove [/interface wireguard peers find where")
    run(go())
    assert run(MikrotikWireguard(Runner(), "wg-nc", "10.9.0.0/24", 51820).interface()) is None
    with pytest.raises(ValueError, match="bad interface name"):
        MikrotikWireguard(Runner(), "wg nc; reboot", "10.9.0.0/24", 51820)


def test_routeros_errors_and_helpers():
    ros = RouterOsRunner(Runner({"bad": "line one\nfailure: already have such address\nmore"}))
    with pytest.raises(ExternalServiceError, match="RouterOS: failure: already have such address"):
        run(ros.run("bad"))
    assert run(ros.run("good")) == ""
    assert parse_duration("") is None and parse_duration("1w2d03:04:05") == 604800 + 2 * 86400 + 3 * 3600 + 4 * 60 + 5
    assert parse_duration("1h30m500ms") == 5400
    assert safe_comment("") == "amnezia-control: client" and len(safe_comment("x" * 100)) == len("amnezia-control: ") + 48


class Secrets:
    def __init__(self, data=None):
        self.d = dict(data or {})

    async def get(self, path):
        return self.d.get(path)

    async def put(self, path, data):
        self.d[path] = data


def test_key_provider(tmp_path):
    secrets = Secrets()
    keys = SshKeyProvider(secrets, str(tmp_path / "run" / "ssh_key"))

    async def go():
        with pytest.raises(ExternalServiceError, match="not found in OpenBao"):
            await keys.path()
        pub = await keys.ensure()
        assert pub.startswith("ssh-ed25519 ") and secrets.d[KEY_SECRET]["public_key"] == pub
        assert await keys.ensure() == pub  # kept, not created again
        path = await keys.path()
        assert open(path).read().startswith("-----BEGIN OPENSSH PRIVATE KEY-----")
        assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
        del secrets.d[KEY_SECRET]["public_key"]  # an older record: the public key is derived again
        assert await keys.ensure() == pub
    run(go())


class Proc:
    def __init__(self, code=0, out=b"ok\n", err=b"", hang=False):
        self.returncode, self._out, self._err, self._hang = code, out, err, hang
        self.killed = False
        self.stdin = None

    async def communicate(self, data=None):
        self.stdin = data
        if self._hang:
            await asyncio.sleep(10)
        return self._out, self._err

    def kill(self):
        self.killed = True


def test_ssh_runner(tmp_path, monkeypatch):
    calls = []
    procs = []

    async def exec_(*args, **kw):
        calls.append(args)
        return procs.pop(0)
    monkeypatch.setattr(sshmod.asyncio, "create_subprocess_exec", exec_)

    class Keys:
        async def path(self):
            return "/run/panel/ssh_key"
    r = SshRunner("192.0.2.1", "admin", Keys(), str(tmp_path / "known_hosts"))

    async def go():
        procs.append(Proc())
        assert await r.run("/system identity print", stdin="input") == "ok\n"
        args = calls[-1]
        assert args[0] == "ssh" and args[-2:] == ("admin@192.0.2.1", "/system identity print")
        assert f"UserKnownHostsFile={tmp_path / 'known_hosts'}" in args
        procs.append(Proc(code=255, err=b"Permission denied (publickey)."))
        with pytest.raises(ExternalServiceError, match="Permission denied"):
            await r.run("x")
        procs.append(Proc(code=1, out=b"", err=b""))
        with pytest.raises(ExternalServiceError, match="ssh exit 1"):
            await r.run("x")
        hung = Proc(hang=True)
        procs.append(hung)
        with pytest.raises(ExternalServiceError, match="timed out"):
            await r.run("x", timeout=0.05)
        assert hung.killed
    run(go())
    with pytest.raises(ExternalServiceError, match="address is not set"):
        run(SshRunner("", "admin", Keys(), "kh").run("x"))
