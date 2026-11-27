"""Offline resilience: with every non-local network connection blocked, the whole app still starts and
answers (forecast, allocation, overflow routing, simulator, dispatch) and serves its full UI.

The judges' kill-switch is simulated by patching socket.socket.connect / connect_ex and DNS lookups to
refuse any host that is not this machine. The server state is rebuilt from scratch and every response
cache is cleared under the block, so each endpoint really computes offline. Attempts are also
recorded, so a library that swallows the error still fails the test.
"""
import hashlib
import json
import re
import socket
from pathlib import Path

import pytest

pytest.importorskip("httpx")
from fastapi.testclient import TestClient  # noqa: E402

from server import api  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web"
LOCAL = {"127.0.0.1", "::1", "localhost", "0.0.0.0", ""}
DATE = "2025-07-07"
CITIES = ["Chattogram", "Dhaka North", "Dhaka South", "Gazipur", "Narayanganj"]
CACHED = [api._ward_payload, api._city_payload, api._heatmap_payload, api._allocation_payload,
          api._overflow_payload, api._curves, api._simulate_payload, api._rank_payload, api._dispatch_payload]


class NetworkBlocked(OSError):
    pass


@pytest.fixture(scope="module")
def offline():
    attempts = []
    real_connect, real_connect_ex = socket.socket.connect, socket.socket.connect_ex
    real_getaddrinfo = socket.getaddrinfo

    def host_of(address):
        return address[0] if isinstance(address, tuple) else ""          # AF_UNIX paths are local

    def guard(host):
        if str(host).lower() not in LOCAL:
            attempts.append(host)
            raise NetworkBlocked(f"network blocked in offline test: {host}")

    def connect(self, address):
        guard(host_of(address))
        return real_connect(self, address)

    def connect_ex(self, address):
        guard(host_of(address))
        return real_connect_ex(self, address)

    def getaddrinfo(host, *a, **k):
        guard(host if host is not None else "")
        return real_getaddrinfo(host, *a, **k)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(socket.socket, "connect", connect)
        mp.setattr(socket.socket, "connect_ex", connect_ex)
        mp.setattr(socket, "getaddrinfo", getaddrinfo)
        # cold start under the block: rebuild state and empty every response cache
        api.STATE = None
        for f in CACHED:
            f.cache_clear()
        with TestClient(api.app) as client:
            yield client, attempts
    assert not attempts, f"the app tried to reach the network: {attempts}"


def test_the_block_really_blocks(offline):
    _, attempts = offline
    with pytest.raises(NetworkBlocked):
        socket.create_connection(("example.com", 80), timeout=1)
    assert attempts.pop() == "example.com"       # remove our own probe so the final check stays clean


def test_health_reports_offline_ready(offline):
    client, _ = offline
    h = client.get("/api/health").json()
    assert h["status"] == "ok" and h["ready"] is True and h["offline_ready"] is True
    assert all(h["components"].values()), h["components"]
    assert all(h["assets"].values()) and h["versions"]["lightgbm"]


@pytest.mark.parametrize("path,params", [
    ("/api/meta", {}),
    ("/api/ward", {"ward": "DNCC_Ward_01", "date": DATE}),
    ("/api/city", {"city": "Dhaka South", "date": DATE}),
    ("/api/model", {"h": 3}),
    ("/api/allocation", {"scope": "all", "date": DATE, "stock_pct": 60, "crews": 40}),
    ("/api/allocation", {"scope": "Chattogram", "date": DATE, "stock_pct": 30, "crews": 5, "floor": 20}),
    ("/api/overflow", {"city": "all", "date": DATE, "basis": "surge", "capacity_pct": 50}),
    ("/api/simulate", {"date": DATE, "crews": "DNCC_Ward_01:2,DSCC_Ward_06:3"}),
    ("/api/simulate", {"date": DATE, "auto": 20}),
    ("/api/simulate/rank", {"scope": "all", "date": DATE, "effect": 15}),
])
def test_core_endpoints_work_offline(offline, path, params):
    client, _ = offline
    r = client.get(path, params=params)
    assert r.status_code == 200, r.text
    assert r.json()


def test_simulate_post_works_offline(offline):
    client, _ = offline
    r = client.post("/api/simulate", json={"crews": {"NCC_Ward_05": 2}, "date": DATE})
    assert r.status_code == 200 and r.json()["crews_deployed"] == 2


@pytest.mark.parametrize("fmt,media", [("sms", "text/plain"), ("whatsapp", "text/plain"), ("brief", "text/html"),
                                       ("json", "application/json")])
@pytest.mark.parametrize("scope,ident", [("ward", "DNCC_Ward_01"), ("city", "Dhaka North")])
def test_dispatch_works_offline(offline, fmt, media, scope, ident):
    client, _ = offline
    r = client.get("/api/dispatch", params={"scope": scope, "id": ident, "date": DATE, "stock_pct": 60, "crews": 40,
                                            "basis": "surge", "pool": "all", "plan": "allocation", "format": fmt})
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith(media) and r.text


def own_web_files():
    return sorted(p for p in WEB.rglob("*") if p.is_file() and p.suffix in {".html", ".js", ".css", ".svg", ".woff2"})


# index.html is served with version stamps (?v=<hash> and an import map); removing them gives the file back
STAMPS = re.compile(rb'<script type="importmap">.*?</script>|\?v=[0-9a-f]{10}')


def test_every_ui_file_is_served_offline(offline):
    client, _ = offline
    files = own_web_files()
    assert any(p.name == "echarts.min.js" for p in files) and any(p.suffix == ".woff2" for p in files)
    for p in files:                                        # raw bytes: holds with core.autocrlf=true too
        rel = p.relative_to(WEB).as_posix()
        if rel == "index.html":
            continue
        r = client.get("/" + rel)
        assert r.status_code == 200 and r.content == p.read_bytes(), rel
    for path in ("/", "/index.html"):
        r = client.get(path)
        assert r.status_code == 200 and STAMPS.sub(b"", r.content) == (WEB / "index.html").read_bytes(), path


# ---------------------------------------------------------------- static guards (no network in the code)
EXTERNAL = re.compile(r"""(?:https?:)?//(?!www\.w3\.org/)[a-z0-9-]+\.[a-z0-9.-]+|@import\b""", re.I)


@pytest.mark.parametrize("path", [p for p in own_web_files() if "vendor" not in p.parts and p.suffix != ".woff2"],
                         ids=lambda p: p.relative_to(WEB).as_posix())
def test_ui_source_has_no_external_urls(path):
    text = path.read_text(encoding="utf-8")
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)     # comments are not requests
    hits = [m.group(0) for m in EXTERNAL.finditer(text)]
    assert not hits, f"{path.name} references the network: {hits}"


def test_vendored_charts_make_no_requests():
    js = (WEB / "vendor" / "echarts.min.js").read_text(encoding="utf-8")
    for bad in ("fonts.googleapis", "XMLHttpRequest(", "navigator.sendBeacon", "googletagmanager"):
        assert bad not in js


@pytest.mark.parametrize("path", sorted((ROOT / "src").glob("*.py")) + [ROOT / "server" / "api.py"],
                         ids=lambda p: p.name)
def test_python_core_has_no_network_clients(path):
    src = path.read_text(encoding="utf-8")
    hits = re.findall(r"^\s*(?:import|from)\s+(requests|httpx|urllib\.request|http\.client|socket|aiohttp)\b", src, re.M)
    assert not hits, f"{path.name} imports a network client: {hits}"


# ---------------------------------------------------------------- caching (stale modules after updates)
@pytest.mark.parametrize("path", ["/", "/index.html", "/styles.css", "/js/app.js", "/js/net.js", "/js/views/dispatch.js"])
def test_app_files_revalidate_every_load(offline, path):
    client, _ = offline
    r = client.get(path)
    assert r.status_code == 200 and r.headers["cache-control"] == "no-cache"
    assert r.headers.get("etag") and r.headers.get("last-modified")
    again = client.get(path, headers={"If-None-Match": r.headers["etag"]})
    assert again.status_code == 304                       # cheap revalidation, never a stale copy


def short_hash(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()[:10]


def served_index(client):
    html = client.get("/").text
    css = re.search(r'<link rel="stylesheet" href="([^"]+)">', html).group(1)
    entry = re.search(r'<script type="module" src="([^"]+)"></script>', html).group(1)
    imap = json.loads(re.search(r'<script type="importmap">(.*?)</script>', html).group(1))["imports"]
    return html, css, entry, imap


def test_index_loads_every_app_file_by_content_hash(offline):
    """A browser can't pair a new page with an old stylesheet or module: changed bytes = new URL."""
    client, _ = offline
    html, css, entry, imap = served_index(client)
    assert css == f"styles.css?v={short_hash(WEB / 'styles.css')}"
    assert entry == f"js/app.js?v={short_hash(WEB / 'js' / 'app.js')}"
    modules = sorted(p.relative_to(WEB).as_posix() for p in (WEB / "js").rglob("*.js"))
    assert sorted(k[2:] for k in imap) == modules                       # every module, nothing else
    for key, url in imap.items():
        rel = key[2:]
        assert url == f"./{rel}?v={short_hash(WEB / rel)}"
        r = client.get(url[1:])
        assert r.status_code == 200 and r.content == (WEB / rel).read_bytes(), url
    assert re.search(r'src="vendor/echarts\.min\.js"', html)           # vendored URLs stay plain (cacheable)
    assert html.index('type="importmap"') < html.index('type="module"')  # the map must precede any module


IMPORT = re.compile(r"""(?:\bfrom\s*|\bimport\s*\(?\s*)["']([^"']+)["']""")


@pytest.mark.parametrize("path", sorted((WEB / "js").rglob("*.js")), ids=lambda p: p.relative_to(WEB).as_posix())
def test_module_imports_resolve_through_the_import_map(offline, path):
    """Imports are plain relative paths (no hand-written ?v=) and each one is pinned by the import map."""
    client, _ = offline
    *_, imap = served_index(client)
    for spec in IMPORT.findall(path.read_text(encoding="utf-8")):
        assert spec.startswith(".") and "?" not in spec, f"{path.name}: {spec}"
        target = (path.parent / spec).resolve().relative_to(WEB.resolve()).as_posix()
        assert f"./{target}" in imap, f"{path.name} imports {spec}, not in the import map"


def test_version_changes_when_a_file_changes(tmp_path):
    (tmp_path / "js").mkdir()
    (tmp_path / "index.html").write_bytes(b'<link rel="stylesheet" href="styles.css">\n'
                                          b'<script type="module" src="js/app.js"></script>\n')
    (tmp_path / "styles.css").write_bytes(b".a{}")
    (tmp_path / "js" / "app.js").write_bytes(b'import "./x.js";')
    (tmp_path / "js" / "x.js").write_bytes(b"1")
    before = api.render_index(tmp_path)
    (tmp_path / "styles.css").write_bytes(b".a{color:red}")
    (tmp_path / "js" / "x.js").write_bytes(b"22")
    after = api.render_index(tmp_path)
    v0, v1 = api.asset_versions(tmp_path), before != after
    assert v1 and re.search(r"styles\.css\?v=(\w+)", before).group(1) != re.search(r"styles\.css\?v=(\w+)", after).group(1)
    assert '"./js/x.js":"./js/x.js?v=' in after and v0["js/app.js"] in after   # unchanged file keeps its hash


def test_index_without_known_entry_points_is_refused(tmp_path):
    (tmp_path / "index.html").write_bytes(b"<html></html>")
    (tmp_path / "styles.css").write_bytes(b"")
    with pytest.raises(RuntimeError):
        api.render_index(tmp_path)


def test_tooltips_are_hidden_without_css():
    """Tooltip text is `hidden` in the markup, so a missing or stale stylesheet never shows it inline."""
    ui = (WEB / "js" / "ui.js").read_text(encoding="utf-8")
    assert re.search(r'<span class="info__tip" role="tooltip" id="\$\{id\}" hidden>', ui)
    assert "tip.hidden = !show" in ui


@pytest.mark.parametrize("path", ["/vendor/echarts.min.js", "/vendor/fonts/Geist-Variable.woff2"])
def test_vendored_files_stay_cacheable(offline, path):
    client, _ = offline
    r = client.get(path)
    assert r.status_code == 200 and re.fullmatch(r"public, max-age=\d{5,}", r.headers["cache-control"])


def test_font_is_served_with_a_font_type(offline):
    client, _ = offline
    assert client.get("/vendor/fonts/Geist-Variable.woff2").headers["content-type"] == "font/woff2"


# ---------------------------------------------------------------- memory: every cache is bounded
def test_every_response_cache_is_bounded():
    found = [v for v in vars(api).values() if callable(v) and hasattr(v, "cache_info")]
    assert set(CACHED) <= set(found)
    for f in found:
        assert f.cache_info().maxsize is not None and f.cache_info().maxsize <= 128, f.__name__
