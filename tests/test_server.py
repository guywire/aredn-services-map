"""The web server: mesh / LAN only by default, the aliases, the JSON shapes."""
import asyncio

from httpx import ASGITransport, AsyncClient

from aredn_services_map import server
from aredn_services_map.aredn_cameras import service as cameras
from aredn_services_map.aredn_services import service as services


def _get(app, path, ip="10.99.0.86", headers=None):
    async def run():
        async with AsyncClient(transport=ASGITransport(app=app, client=(ip, 5000)), base_url="http://t") as c:
            return await c.get(path, headers=headers or {})
    return asyncio.run(run())


def _app(tmp_path, **over):
    return server.create_app(dict(server.DEFAULTS, data_dir=str(tmp_path), **over), start_jobs=False)


def test_page_and_json_on_the_mesh(tmp_path):
    app = _app(tmp_path)
    r = _get(app, "/")
    assert r.status_code == 200 and "AREDN Services Map" in r.text and "static/leaflet/leaflet.js" in r.text
    assert _get(app, "/static/leaflet/leaflet.js").status_code == 200
    d = _get(app, "/map.json").json()
    assert set(d) >= {"map", "people", "people_created"}
    services.data = {"generated_at": 1.0, "summary": {}, "categories": [],
                     "services": [{"name": "BPQ BBS", "category": "packet"}]}
    cameras.data = {"generated_at": 1.0, "summary": {}, "cameras": [{"name": "Yard", "status": "open"}]}
    try:
        assert _get(app, "/services.json").json()["services"][0]["category"] == "packet"
        assert _get(app, "/cameras.json").json()["cameras"][0]["name"] == "Yard"
    finally:
        services.data = cameras.data = None
    assert _get(app, "/health").json()["version"]


def test_aliases(tmp_path):
    app = _app(tmp_path)
    assert _get(app, "/cameras/").headers["location"] == "/?show=cameras"
    assert _get(app, "/aredn/").headers["location"] == "/?show=services"
    assert _get(app, "/services/").headers["location"] == "/?show=services"


def test_internet_and_proxied_requests_are_refused(tmp_path):
    app = _app(tmp_path)
    assert _get(app, "/", ip="8.8.8.8").status_code == 403
    assert _get(app, "/map.json", ip="192.168.1.50", headers={"X-Forwarded-For": "8.8.8.8"}).status_code == 403
    assert _get(app, "/", ip="127.0.0.1").status_code == 200
    assert _get(_app(tmp_path, allow_internet=True), "/", ip="8.8.8.8").status_code == 200


def test_module_config_shape(tmp_path):
    m = server.module_config(dict(server.DEFAULTS, data_dir=str(tmp_path), node_url="http://n/a/sysinfo",
                                  own_office="hometown", services={"refresh_hours": 12}))
    assert m["radio_directory"]["meshphone"]["hosts_url"] == "http://n/a/sysinfo"
    assert m["radio_directory"]["meshphone"]["own_office"] == "hometown"
    assert m["radio_directory"]["aredn_services"]["refresh_hours"] == 12
    assert m["database"]["path"].startswith(str(tmp_path))


def test_44_net_is_on_the_mesh(tmp_path):
    app = _app(tmp_path)
    assert _get(app, "/", ip="44.63.1.2").status_code == 200
    assert _get(app, "/", ip="44.130.0.9").status_code == 200
    assert _get(app, "/", ip="44.200.0.1").status_code == 403          # 44.192/10: sold, not amateur
    assert _get(app, "/", ip="45.0.0.1").status_code == 403


def test_peer_endpoints(tmp_path):
    import time
    from aredn_services_map import sharing
    app = _app(tmp_path)
    info = _get(app, "/peer/v1/info").json()
    assert info["app"] == "aredn-services-map" and info["key"]
    assert info["layers"]["services"] == {"scheme": sharing.SCHEME, "regions": {}}
    assert info["layers"]["tiles"]["max_zoom"] == 19
    assert _get(app, "/peer/v1/services/region/1").status_code == 404
    doc = sharing.make_doc(services.identity, "k1map", 0, time.time(), [])
    services.regions[0] = {"doc": doc, "scanned_at": time.time(), "station": "k1map", "count": 0, "services": []}
    try:
        assert _get(app, "/peer/v1/services/region/1").json() == doc          # exactly as signed
        assert _get(app, "/peer/v1/info").json()["layers"]["services"]["regions"]["1"]["station"] == "k1map"
        assert [r["region"] for r in _get(app, "/services.json").json()["regions"]] == [1, 2, 3, 4]
    finally:
        services.regions.clear()
    off = _app(tmp_path, sharing={"enabled": False})
    assert _get(off, "/peer/v1/info").status_code == 404


def test_update_check_can_be_off(tmp_path):
    r = _get(_app(tmp_path, updates={"check": False}), "/update.json").json()
    assert r["available"] is False and r["error"] == "update check off"


def test_tile_route(tmp_path):
    app = _app(tmp_path, tiles={"internet": False})
    assert _get(app, "/tiles/30/0/0.png").status_code == 404
    assert _get(app, "/peer/v1/tiles/3/1/1.png").status_code == 404
    assert _get(app, "/tiles/1/0/0.png", ip="8.8.8.8").status_code == 403
    assert "tiles/{z}/{x}/{y}.png" in _get(app, "/").text
