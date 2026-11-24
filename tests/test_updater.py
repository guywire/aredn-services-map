"""Updates from GitHub releases: version compare, safe unpacking, file copy."""
import io
import tarfile

import pytest

from aredn_services_map import updater


def test_versions():
    assert updater.parse_version("v0.2.0") == (0, 2, 0)
    assert updater.parse_version("0.10.1") == (0, 10, 1)
    assert updater.parse_version("latest") is None
    assert updater.is_newer("0.10.0", "0.9.9") and not updater.is_newer("0.2.0", "0.2.0")
    assert not updater.is_newer("", "0.2.0") and not updater.is_newer("junk", "0.2.0")


def _tar(path, members):
    with tarfile.open(path, "w:gz") as tf:
        for name, data, kind in members:
            ti = tarfile.TarInfo(name)
            if kind == "dir":
                ti.type = tarfile.DIRTYPE
                tf.addfile(ti)
            elif kind == "link":
                ti.type, ti.linkname = tarfile.SYMTYPE, "/etc/passwd"
                tf.addfile(ti)
            else:
                ti.size = len(data)
                tf.addfile(ti, io.BytesIO(data))


def test_safe_extract_and_version(tmp_path):
    init = b'"""x"""\n__version__ = "0.3.0"\n'
    _tar(tmp_path / "ok.tgz", [("asm-0.3.0", b"", "dir"), ("asm-0.3.0/aredn_services_map", b"", "dir"),
                               ("asm-0.3.0/aredn_services_map/__init__.py", init, "file")])
    top = updater.safe_extract(tmp_path / "ok.tgz", tmp_path / "x")
    assert top.name == "asm-0.3.0" and updater.version_in(top) == "0.3.0"


@pytest.mark.parametrize("member", [("../evil.py", b"x", "file"), ("asm/link", b"", "link")])
def test_safe_extract_refuses_escapes(tmp_path, member):
    _tar(tmp_path / "bad.tgz", [("asm", b"", "dir"), member])
    with pytest.raises(RuntimeError, match="refusing"):
        updater.safe_extract(tmp_path / "bad.tgz", tmp_path / "y")


def test_copy_package_replaces_and_removes_stale_files(tmp_path):
    new, live = tmp_path / "new", tmp_path / "live"
    (new / "templates").mkdir(parents=True)
    (live / "__pycache__").mkdir(parents=True)
    (new / "a.py").write_text("new a")
    (new / "templates" / "index.html").write_text("page")
    (live / "a.py").write_text("old a")
    (live / "gone.py").write_text("removed upstream")
    (live / "__pycache__" / "a.cpython-312.pyc").write_bytes(b"\0")
    updater._copy_package(new, live)
    assert (live / "a.py").read_text() == "new a" and (live / "templates" / "index.html").exists()
    assert not (live / "gone.py").exists() and not list(live.rglob("*.pyc"))


def test_install_refuses_a_non_version():
    with pytest.raises(RuntimeError, match="not a release version"):
        updater.install("main")
