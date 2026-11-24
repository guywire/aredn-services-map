"""
Updates from GitHub. Once a day the map looks up the latest release of
guywire/aredn-services-map (one request to api.github.com, when this box has
internet); the page says when a newer one is out.

To install it: `aredn-services-map --update` and restart the service, or set
updates.auto_install: true and it installs the new release and restarts
itself.

How a release is installed (nothing runs as root; the files must be writable
by the account that runs the map, as the README's install leaves them):
  * a git checkout (the README's install): fetch the release's tag and
    fast-forward to it. Refused if the checkout has local changes.
  * otherwise: the release's source archive from github.com over HTTPS,
    checked to unpack only inside its own folder and to be the version the
    release names, then copied over the running package.
Then `pip install` of the new copy, for any new dependency, and a restart.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
from pathlib import Path

log = logging.getLogger(__name__)

GITHUB_REPO = "guywire/aredn-services-map"
CHECK_TTL = 24 * 3600
RETRY_TTL = 3600
FIRST_DELAY_S = 600
MAX_ARCHIVE = 50 * 1024 * 1024
_VER_RE = re.compile(r"^\s*v?(\d+)\.(\d+)\.(\d+)\s*$")
_cache: dict = {"ts": 0.0, "ttl": 0.0, "latest": "", "url": "", "error": ""}


def parse_version(text: str):
    m = _VER_RE.match(text or "")
    return tuple(int(x) for x in m.groups()) if m else None


def is_newer(latest: str, current: str) -> bool:
    a, b = parse_version(latest), parse_version(current)
    return bool(a and b and a > b)


def current_version() -> str:
    from aredn_services_map import __version__
    return __version__


async def check(force: bool = False) -> dict:
    """Is a newer release out? Cached for a day; never raises."""
    import httpx
    now = time.time()
    if force or now - _cache["ts"] >= _cache["ttl"]:
        latest, url, error = "", "", ""
        try:
            async with httpx.AsyncClient(timeout=15, headers={"User-Agent": "aredn-services-map",
                                                              "Accept": "application/vnd.github+json"}) as c:
                r = await c.get(f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest")
                r.raise_for_status()
                j = r.json()
            latest, url = str(j.get("tag_name") or ""), str(j.get("html_url") or "")
            if parse_version(latest) is None:
                error = f"unrecognised release tag {latest!r}"
        except Exception as exc:                      # no internet, GitHub down, no release yet
            error = f"{type(exc).__name__}: {exc}"[:200]
        _cache.update(ts=now, ttl=RETRY_TTL if error else CHECK_TTL,
                      latest="" if error else latest.lstrip("v"), url=url, error=error)
    cur = current_version()
    return {"available": is_newer(_cache["latest"], cur), "current": cur, "latest": _cache["latest"],
            "url": _cache["url"], "checked_at": _cache["ts"] or None, "error": _cache["error"]}


def _root() -> Path:
    return Path(__file__).resolve().parent.parent


def _git(root: Path, *args: str) -> str:
    r = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, timeout=300)
    if r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}: {(r.stderr or r.stdout).strip()[:300]}")
    return r.stdout


def safe_extract(archive: Path, dest: Path) -> Path:
    """Unpack, refusing anything that would land outside dest or isn't a plain
    file or folder; returns the archive's single top folder."""
    dest = dest.resolve()
    with tarfile.open(archive) as tf:
        members = tf.getmembers()
        for m in members:
            target = (dest / m.name).resolve()
            if (dest not in target.parents and target != dest) or not (m.isfile() or m.isdir()):
                raise RuntimeError(f"refusing archive member {m.name!r}")
        try:
            tf.extractall(dest, members=members, filter="data")
        except TypeError:          # Python without PEP 706: checked above
            tf.extractall(dest, members=members)
    tops = [p for p in dest.iterdir() if p.is_dir()]
    if len(tops) != 1:
        raise RuntimeError("unexpected archive layout")
    return tops[0]


def version_in(src: Path) -> str:
    m = re.search(r'__version__\s*=\s*"([^"]+)"', (src / "aredn_services_map" / "__init__.py").read_text(encoding="utf-8"))
    return m.group(1) if m else ""


def _copy_package(new_pkg: Path, live_pkg: Path) -> None:
    shutil.copytree(new_pkg, live_pkg, dirs_exist_ok=True)
    keep = {p.relative_to(new_pkg) for p in new_pkg.rglob("*") if p.is_file()}
    for p in list(live_pkg.rglob("*")):
        if p.is_file() and (p.suffix == ".pyc" or p.relative_to(live_pkg) not in keep):
            p.unlink()


def install(version: str) -> list[str]:
    """Install release `version` (blocking). Returns what was done."""
    tag = "v" + version.lstrip("v")
    if parse_version(tag) is None:
        raise RuntimeError(f"not a release version: {version!r}")
    root, done = _root(), []
    tmp = Path(tempfile.mkdtemp(prefix="asm-update-"))
    try:
        if (root / ".git").exists() and shutil.which("git"):
            if _git(root, "status", "--porcelain", "--untracked-files=no").strip():
                raise RuntimeError(f"{root} has local changes; update it by hand (git pull)")
            _git(root, "fetch", "--quiet", "origin", f"refs/tags/{tag}:refs/tags/{tag}")
            _git(root, "merge", "--ff-only", "--quiet", tag)
            src = root
            done.append(f"git checkout {root} fast-forwarded to {tag}")
        else:
            import httpx
            url = f"https://github.com/{GITHUB_REPO}/archive/refs/tags/{tag}.tar.gz"
            with httpx.Client(timeout=120, follow_redirects=True, headers={"User-Agent": "aredn-services-map"}) as c:
                r = c.get(url)
                r.raise_for_status()
            if len(r.content) > MAX_ARCHIVE:
                raise RuntimeError("archive too large")
            (tmp / "src.tar.gz").write_bytes(r.content)
            src = safe_extract(tmp / "src.tar.gz", tmp / "x")
            if version_in(src) != tag[1:]:
                raise RuntimeError(f"the {tag} archive says it is version {version_in(src)!r}")
            _copy_package(src / "aredn_services_map", Path(__file__).resolve().parent)
            done.append(f"{url} unpacked over {Path(__file__).resolve().parent}")
        if version_in(src) != tag[1:]:
            raise RuntimeError(f"after the update the code says version {version_in(src)!r}, not {tag[1:]}")
        pip = subprocess.run([sys.executable, "-m", "pip", "install", "--quiet", "--upgrade", str(src)],
                             capture_output=True, text=True, timeout=900)
        done.append("pip install: " + ("ok" if pip.returncode == 0 else
                                       f"failed (the old dependencies stay): {(pip.stderr or '').strip()[-300:]}"))
        return done
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def restart() -> None:
    """Replace this process with a fresh copy of itself (same arguments)."""
    logging.shutdown()
    os.execv(sys.executable, [sys.executable, "-m", "aredn_services_map", *sys.argv[1:]])


async def loop(cfg: dict) -> None:
    """The daily check; with auto_install, install a newer release and restart."""
    await asyncio.sleep(FIRST_DELAY_S)
    while True:
        u = await check()
        if u["available"]:
            log.info("Update available: %s (this is %s) %s", u["latest"], u["current"], u["url"])
            if cfg.get("auto_install"):
                try:
                    for line in await asyncio.to_thread(install, u["latest"]):
                        log.info("update: %s", line)
                    log.info("update: restarting on %s", u["latest"])
                    restart()
                except Exception as exc:
                    log.warning("update to %s failed: %s", u["latest"], exc)
        await asyncio.sleep(CHECK_TTL)
