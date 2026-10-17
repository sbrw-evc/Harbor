"""The flow collector (flow-collector/collector.py): goflow2 JSON → rows of flows.flows, IPv4 and IPv6 alike."""
import importlib.util
import ipaddress
import pathlib

import pytest

pytest.importorskip("maxminddb")
pytest.importorskip("httpx")

PATH = pathlib.Path(__file__).resolve().parents[2] / "flow-collector" / "collector.py"


@pytest.fixture(scope="module")
def collector():
    spec = importlib.util.spec_from_file_location("nc_collector", PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.LAN[:] = [ipaddress.ip_network("192.168.0.0/24")]
    mod.EXTRA_LAN[:] = [ipaddress.ip_network("2001:db8:10::/64")]

    class Geo:
        def lookup(self, ip):
            return ("DE", 3320, "DTAG") if ":" in ip else ("US", 15169, "GOOGLE")
    return mod, mod.Classifier(Geo())


def gf(**kw):
    base = {"type": "IPFIX", "time_received_ns": 1_760_000_000_000_000_000, "sampling_rate": 0, "sampler_address": "192.168.0.1",
            "bytes": 1500, "packets": 2, "src_addr": "192.168.0.10", "dst_addr": "8.8.8.8", "proto": "TCP", "src_port": 50000,
            "dst_port": 443, "in_if": 2, "out_if": 1, "tcp_flags": 24}
    base.update(kw)
    return base


def test_ipv4(collector):
    _, classify = collector
    r = classify(gf())
    assert r["Direction"] == "out" and r["LocalAddr"] == "::ffff:192.168.0.10" and r["RemoteAddr"] == "::ffff:8.8.8.8"
    assert r["ExporterAddress"] == "::ffff:192.168.0.1" and r["RemoteCountry"] == "US" and r["ServicePort"] == 443


def test_ipv6_outbound_and_inbound(collector):
    _, classify = collector
    out = classify(gf(src_addr="2001:db8:10::5", dst_addr="2a00:1450:4010:c05::8a", sampler_address="2001:db8:10::1"))
    assert out and out["Direction"] == "out" and out["LocalAddr"] == "2001:db8:10::5"
    assert out["RemoteAddr"] == "2a00:1450:4010:c05::8a" and out["ExporterAddress"] == "2001:db8:10::1" and out["RemoteAS"] == 3320
    inb = classify(gf(src_addr="2a00:1450:4010:c05::8a", dst_addr="2001:db8:10::5", src_port=443, dst_port=50000))
    assert inb["Direction"] == "in" and inb["LocalAddr"] == "2001:db8:10::5" and inb["LocalPort"] == 50000


def test_ipv6_local_and_special(collector):
    _, classify = collector
    assert classify(gf(src_addr="fd00::1", dst_addr="fd00::2"))["Direction"] == "internal"
    assert classify(gf(src_addr="fe80::1", dst_addr="fe80::2"))["Direction"] == "internal"
    assert classify(gf(src_addr="2001:db8:10::5", dst_addr="ff02::fb")) is None  # mDNS multicast
    assert classify(gf(src_addr="::", dst_addr="2001:db8:10::5")) is None
    mapped = classify(gf(src_addr="::ffff:192.168.0.10", dst_addr="::ffff:8.8.8.8", sampler_address="::ffff:192.168.0.1"))
    assert mapped["LocalAddr"] == "::ffff:192.168.0.10" and mapped["ExporterAddress"] == "::ffff:192.168.0.1"


def test_missing_exporter(collector):
    _, classify = collector
    assert classify(gf(sampler_address=""))["ExporterAddress"] == "::ffff:0.0.0.0"


def test_settings_from_the_panel(tmp_path):
    """collector.json, written by the panel, replaces the environment and is re-read while running."""
    import json
    spec = importlib.util.spec_from_file_location("nc_collector_cfg", PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    path = tmp_path / "collector.json"
    assert mod.load_config(str(path)) is False  # no file: the environment stays
    path.write_text(json.dumps({"lan_cidr": "10.20.0.0/16,bad,10.9.0.0/24", "gateway_ip": "10.20.0.2",
                                "awg_host": "203.0.113.10", "router_host": "10.20.0.1",
                                "clickhouse": {"url": "http://ch:8123", "user": "u", "password": "p", "database": "f"}}))
    assert mod.load_config(str(path)) is True
    assert mod.LAN == [ipaddress.ip_network("10.20.0.0/16"), ipaddress.ip_network("10.9.0.0/24")]
    assert (mod.GATEWAY, mod.VPN_HOST, mod.ROUTER) == ("10.20.0.2", "203.0.113.10", "10.20.0.1")
    assert (mod.CH_URL, mod.CH_USER, mod.CH_PASSWORD, mod.CH_DB) == ("http://ch:8123", "u", "p", "f")
    assert mod.is_local(ipaddress.ip_address("10.20.5.5"))
    assert mod.load_config(str(path)) is False  # the same ClickHouse: no reconnect
