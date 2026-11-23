"""
The web server: one page (the map, offices, people, cameras and services
tabs) and the JSON it reads, plus the background jobs that fill it.

  /                 the page          /cameras/  the page on the cameras
  /map.json         MeshPhone map     /aredn/    the page on the services
  /services.json    AREDN services    /health    status of the background jobs
  /cameras.json     AREDN cameras

By default only mesh / LAN addresses are served (private or loopback, and not
through a reverse proxy): the white pages hold other operators' names and
contact details, published on the mesh for the mesh.
"""
from __future__ import annotations

import ipaddress
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from aredn_services_map import __version__
from aredn_services_map.aredn_cameras import service as cameras
from aredn_services_map.aredn_services import service as services
from aredn_services_map.directory import status as directory
from aredn_services_map.meshphone_map import service as mp_map

log = logging.getLogger(__name__)
HERE = Path(__file__).parent
NO_CACHE = {"Cache-Control": "no-store"}

DEFAULTS = {
    "listen": "0.0.0.0", "port": 8088, "data_dir": "./data", "allow_internet": False,
    "node_url": "", "own_office": "", "dns_server": "auto", "geocode": True,
    "meshphone": {}, "services": {},
}


def load_config(path: str | None) -> dict:
    cfg = dict(DEFAULTS)
    if path:
        import yaml
        with open(path, encoding="utf-8") as f:
            cfg.update(yaml.safe_load(f) or {})
    return cfg


def module_config(cfg: dict) -> dict:
    """The settings shape the map modules read (they came from a bigger app)."""
    data_dir = os.path.abspath(str(cfg.get("data_dir") or "./data"))
    os.makedirs(data_dir, exist_ok=True)
    mp = dict(cfg.get("meshphone") or {})
    mp.setdefault("hosts_url", cfg.get("node_url") or "")
    mp.setdefault("own_office", cfg.get("own_office") or "")
    mp.setdefault("dns_server", cfg.get("dns_server") or "auto")
    mp.setdefault("geocode", bool(cfg.get("geocode", True)))
    svc = dict(cfg.get("services") or {})
    return {"database": {"path": os.path.join(data_dir, "aredn-services-map.db")},
            "radio_directory": {"meshphone": mp, "aredn_services": svc,
                                "aredn_cameras": {"enabled": svc.get("enabled", True)}}}


def _local(request: Request) -> bool:
    """A direct request from a private or loopback address: the LAN or the
    AREDN mesh (10/8), not the internet and not through a proxy."""
    try:
        addr = ipaddress.ip_address(request.client.host)
        if addr.version == 6 and addr.ipv4_mapped:
            addr = addr.ipv4_mapped
    except (AttributeError, TypeError, ValueError):
        return False
    return ((addr.is_private or addr.is_loopback)
            and not request.headers.get("x-forwarded-for") and not request.headers.get("forwarded"))


def create_app(cfg: dict | None = None, start_jobs: bool = True) -> FastAPI:
    cfg = cfg or dict(DEFAULTS)
    mcfg = module_config(cfg)

    @asynccontextmanager
    async def lifespan(app):
        if start_jobs:
            if not mcfg["radio_directory"]["meshphone"]["hosts_url"]:
                log.warning("node_url is not set: the map has no mesh host list to work from")
            await directory.start(mcfg)
            await mp_map.start(mcfg)
            await services.start(mcfg)
        else:
            directory.configure(mcfg)
        yield

    app = FastAPI(title="AREDN Services Map", version=__version__, lifespan=lifespan,
                  docs_url=None, redoc_url=None, openapi_url=None)

    @app.middleware("http")
    async def mesh_only(request: Request, call_next):
        if not cfg.get("allow_internet") and not _local(request):
            return JSONResponse({"detail": "Only served on the mesh / local network"}, status_code=403)
        return await call_next(request)

    app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")

    @app.get("/", response_class=HTMLResponse)
    async def page():
        return HTMLResponse((HERE / "templates" / "index.html").read_text(encoding="utf-8"), headers=NO_CACHE)

    @app.get("/cameras")
    @app.get("/cameras/")
    async def cameras_alias():
        return RedirectResponse(url="/?show=cameras", status_code=302)

    @app.get("/aredn")
    @app.get("/aredn/")
    @app.get("/services/")
    async def services_alias():
        return RedirectResponse(url="/?show=services", status_code=302)

    @app.get("/map.json")
    async def map_json():
        m = mp_map.data
        if m and m.get("people"):
            people, created = m["people"], m.get("people_created", "")
        else:
            d = directory.meshphone()
            loc = {o["name"].upper(): o.get("location", "") for o in d["offices"]}
            people = [{**{k: p.get(k, "") for k in ("callsign", "name", "number", "pbx")},
                       "location": loc.get((p.get("pbx") or "").upper(), "")} for p in d["people"]]
            created = directory.mp_people_created
        return JSONResponse({"map": m, "map_error": mp_map.last_error, "people": people, "people_created": created,
                             "offices_refreshed_at": directory.mp_offices_refreshed_at}, headers=NO_CACHE)

    @app.get("/services.json")
    async def services_json():
        d = services.data or {}
        return JSONResponse({"services": d.get("services", []), "categories": d.get("categories", []),
                             "generated_at": d.get("generated_at"), "summary": d.get("summary", {}),
                             "error": services.last_error, "running": services.running}, headers=NO_CACHE)

    @app.get("/cameras.json")
    async def cameras_json():
        d = cameras.data or {}
        return JSONResponse({"cameras": d.get("cameras", []), "generated_at": d.get("generated_at"),
                             "summary": d.get("summary", {}), "error": cameras.last_error,
                             "running": cameras.running}, headers=NO_CACHE)

    @app.get("/health")
    async def health():
        return {"version": __version__,
                "directory": {"offices": len(directory.mp_offices), "people": len(directory.mp_people),
                              "errors": directory.errors},
                "meshphone_map": {"generated_at": (mp_map.data or {}).get("generated_at"), "error": mp_map.last_error},
                "services": {"generated_at": (services.data or {}).get("generated_at"), "running": services.running,
                             "error": services.last_error}}

    return app
