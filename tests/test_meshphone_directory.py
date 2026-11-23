"""
tests/test_meshphone_directory.py
---------------------------------
AREDN MeshPhone offices + white pages (aredn_services_map/meshphone_directory.py) and
the endpoints that serve them. All data below is made up; it only mirrors
the shape of the WT0F network_json export and N2MH's WhitePages XML.
"""

import asyncio
import socket
import struct
import threading

import pytest

from aredn_services_map import meshphone_directory as mp

OFFICES_JSON = [
    {"_id": "x1", "name": "TESTVILLE", "location": "Testville, ZZ", "admin_callsign": "n0aaa",
     "admin_phone": "555-4111", "admin_email": "a@example.com", "admin_name": "A Person",
     "ip_address": "10.1.2.3", "pbx_status": "Active", "checkin_at": "2026-11-01T00:00:00Z",
     "office_codes": [{"office_code": "55541", "npa": "555", "dialplans": ["_55541xx"]}],
     "trunks": [{"pbx_name": "OTHERTOWN", "office_code": "55599"}]},
    {"_id": "x2", "name": "OTHERTOWN", "location": "Othertown, ZZ", "admin_phone": "",
     "office_codes": [{"office_code": "55599", "dialplans": ["_55599xx"]}], "trunks": []},
    {"_id": "x3"},   # nameless: skipped
]

WP_XML = """<?xml version="1.0" encoding="UTF-8"?>
<WhitePages>
  <administration><author>TEST</author><created>2025-10-13 02:27:01 -0400</created></administration>
  <listing><callsign>n0aaa</callsign><opname>A+Person</opname><meshphone>5554101</meshphone>
    <pbx>TESTVILLE</pbx><meshmail>a%40testville.local.mesh</meshmail><notes>private+note</notes></listing>
  <listing><callsign>N0BBB</callsign><opname>B</opname><meshphone>555-9901</meshphone><pbx>OTHERTOWN</pbx></listing>
  <listing><callsign></callsign><meshphone>5550000</meshphone></listing>
  <listing><callsign>N0CCC</callsign><meshphone>123</meshphone></listing>
</WhitePages>"""


def test_parse_offices_keeps_only_directory_fields():
    offs = mp.parse_offices(OFFICES_JSON)
    assert [o["name"] for o in offs] == ["OTHERTOWN", "TESTVILLE"]
    t = offs[1]
    assert t["office_codes"] == ["55541"] and t["dialplans"] == ["_55541xx"]
    assert t["trunks"] == ["OTHERTOWN"] and t["admin_callsign"] == "N0AAA"
    assert t["admin_phone"] == "5554111"
    flat = repr(offs)
    assert t["admin_email"] == "a@example.com"        # the contact the office published
    assert t["admin_name"] == "A Person"              # names are public on the mesh, like callsigns
    # the PBX address is kept: the MeshPhone map (meshphone_map.py) measures it,
    # and WT0F's own map already publishes it to the whole mesh
    assert t["ip"] == "10.1.2.3"


def test_parse_offices_rejects_non_list():
    with pytest.raises(ValueError):
        mp.parse_offices({"error": "nope"})


def test_parse_whitepages():
    people, created = mp.parse_whitepages(WP_XML)
    assert created.startswith("2025-10-13")
    assert people == [
        {"callsign": "N0AAA", "name": "A Person", "number": "5554101", "pbx": "TESTVILLE"},
        {"callsign": "N0BBB", "name": "B", "number": "5559901", "pbx": "OTHERTOWN"},
    ]
    assert "private" not in repr(people) and "local.mesh" not in repr(people)


def test_parse_whitepages_refuses_dtd():
    evil = '<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaa">]><WhitePages></WhitePages>'
    with pytest.raises(ValueError):
        mp.parse_whitepages(evil)


def test_parse_whitepages_wrong_root():
    with pytest.raises(ValueError):
        mp.parse_whitepages("<html><body/></html>")


def test_dial_string():
    assert mp.dial_string("5554101") == "785554101"


def test_mesh_dns_from_leases(tmp_path):
    (tmp_path / "dhclient.eth0.leases").write_text(
        'lease {\n  option domain-name "example.lan";\n  option domain-name-servers 192.168.1.1;\n}\n')
    (tmp_path / "dhclient.vlan.leases").write_text(
        'lease {\n  option domain-name "local.mesh";\n  option domain-name-servers 10.9.9.1;\n}\n'
        'lease {\n  option domain-name-servers 10.99.0.1;\n  option domain-name "local.mesh";\n}\n')
    assert mp.mesh_dns_from_leases(str(tmp_path / "dhclient*.leases")) == "10.99.0.1"
    assert mp.mesh_dns_from_leases(str(tmp_path / "none*.leases")) is None


def test_resolve_url_passthrough_for_normal_hosts():
    assert mp.resolve_url("https://example.org/x", "auto") == ("https://example.org/x", {})


@pytest.fixture
def fake_dns():
    """A one-shot UDP DNS server on 127.0.0.1 answering any A query with 10.20.30.40."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("127.0.0.1", 0))
    sock.settimeout(5)
    port = sock.getsockname()[1]

    def serve():
        try:
            data, addr = sock.recvfrom(512)
        except OSError:
            return
        ans = bytes([0xC0, 0x0C]) + struct.pack(">HHIH", 1, 1, 60, 4) + socket.inet_aton("10.20.30.40")
        sock.sendto(data[:2] + struct.pack(">HHHHH", 0x8180, 1, 1, 0, 0) + data[12:] + ans, addr)

    t = threading.Thread(target=serve, daemon=True)
    t.start()
    yield port
    t.join(6)
    sock.close()


def test_dns_query_and_resolve(monkeypatch, fake_dns):
    monkeypatch.setattr(mp, "DNS_PORT", fake_dns)
    url, hdrs = mp.resolve_url("http://WT0F-freepbx.local.mesh/whitepages.xml", "127.0.0.1")
    assert url == "http://10.20.30.40/whitepages.xml"
    assert hdrs == {"Host": "wt0f-freepbx.local.mesh"}   # lowercased; HTTP Host matching is case-insensitive


def test_resolve_url_without_mesh_dns(monkeypatch):
    monkeypatch.setattr(mp, "mesh_dns_from_leases", lambda *a: None)
    with pytest.raises(RuntimeError):
        mp.resolve_url("http://x.local.mesh/", "auto")


# ── refresh integration ──

class _Resp:
    def __init__(self, body, code=200):
        self._body, self.status_code = body, code
        self.text = body if isinstance(body, str) else ""

    def json(self):
        return self._body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")
