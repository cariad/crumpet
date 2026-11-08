from collections.abc import Generator
from contextlib import contextmanager
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from uuid import UUID

from pytest import MonkeyPatch, mark, param

from crumpet import Server
from tests.marks import needs_ipv6
from tests.pages import Response, fetch

# Sent with every response.
_NO_STORE = ("Cache-Control", "no-store")


@contextmanager
def _serve_like_python(directory: Path) -> Generator[str]:
    """Serve `directory` the way `python -m http.server` does, and yield its URL."""
    with ThreadingHTTPServer(
        ("127.0.0.1", 0),
        partial(
            SimpleHTTPRequestHandler,
            directory=directory,
        ),
    ) as server:
        thread = Thread(target=server.serve_forever)
        thread.start()

        try:
            yield f"http://127.0.0.1:{server.server_port}/"

        finally:
            server.shutdown()
            thread.join()


@mark.parametrize(
    (
        "path",
        "file",
        "content",
    ),
    [
        (
            "",
            "index.html",
            "<!DOCTYPE html><html><head></head><body><p>Index</p></body></html>",
        ),
        (
            "",
            "index.htm",
            "<p>Index without head or body</p>",
        ),
        (
            "PAGE.HTML",
            "PAGE.HTML",
            "<HTML><HEAD></HEAD><BODY><P>Upper case</P></BODY></HTML>",
        ),
        (
            "large.html",
            "large.html",
            "<p>Large</p>" * 100_000,
        ),
        # A directory without an index page is listed.
        (
            "",
            "notes.txt",
            "Notes",
        ),
    ],
)
def test_server_adds_reload_script_to_html(
    content: str,
    file: str,
    free_port: int,
    monkeypatch: MonkeyPatch,
    path: str,
    tmp_path: Path,
) -> None:
    """
    Assert that a server serves HTML files and listings like Python's own server does,
    with the reload script appended, however the HTML is written.
    """
    (tmp_path / file).write_text(content)

    monkeypatch.setattr(
        "crumpet._reloader.uuid4",
        lambda: UUID(int=0),
    )

    script = (
        f'<script src="http://localhost:{free_port}/__crumpet__/reload.js'
        f'?reloads=0&amp;run={UUID(int=0).hex}"></script>'
    ).encode()

    with (
        Server(
            tmp_path,
            port=free_port,
        ) as server,
        _serve_like_python(tmp_path) as python_url,
    ):
        result = fetch(server.url + path)
        python = fetch(python_url + path)

    length = str(len(python.body) + len(script))

    expect = Response(
        status=python.status,
        headers=[
            (
                name,
                length if name == "Content-Length" else value,
            )
            for name, value in python.headers
        ]
        + [_NO_STORE],
        body=python.body + script,
    )

    assert result == expect


@mark.parametrize(
    "path",
    [
        "__crumpet__/notes.txt",
        "%5f_crumpet__/notes.txt",
        "docs/../__crumpet__/notes.txt",
        "__CRUMPET__/notes.txt",
        "link/notes.txt",
        "__crumpet__/link.txt",
    ],
)
def test_server_refuses_reserved_paths(
    free_port: int,
    path: str,
    tmp_path: Path,
) -> None:
    """Assert that a file under the reserved prefix isn't served."""
    (tmp_path / "__crumpet__").mkdir()
    (tmp_path / "__crumpet__" / "notes.txt").write_text("Notes")
    (tmp_path / "link").symlink_to("__crumpet__")
    (tmp_path / "notes.txt").write_text("Notes")
    (tmp_path / "__crumpet__" / "link.txt").symlink_to("../notes.txt")

    with Server(
        tmp_path,
        port=free_port,
    ) as server:
        result = fetch(server.url + path).status

    expect = 404

    assert result == expect


def test_server_refuses_reserved_paths_where_symlink_leads_now(
    free_port: int,
    tmp_path: Path,
) -> None:
    """
    Assert that a file under the reserved prefix isn't served from the directory that a
    symlink to the served directory was changed to lead to.
    """
    before = tmp_path / "before"
    before.mkdir()

    after = tmp_path / "after"
    (after / "__crumpet__").mkdir(parents=True)
    (after / "__crumpet__" / "notes.txt").write_text("Notes")
    (after / "alias").symlink_to("__crumpet__")

    link = tmp_path / "link"
    link.symlink_to(before)

    with Server(
        link,
        port=free_port,
    ) as server:
        link.unlink()
        link.symlink_to(after)

        result = fetch(server.url + "alias/notes.txt").status

    expect = 404

    assert result == expect


def test_server_serves_directory_after_working_directory_changes(
    free_port: int,
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    """
    Assert that a server that's given a relative path keeps serving the directory that
    it led to when the working directory changes.
    """
    (tmp_path / "public").mkdir()
    (tmp_path / "public" / "notes.txt").write_text("Notes")
    (tmp_path / "elsewhere" / "public").mkdir(parents=True)
    monkeypatch.chdir(tmp_path)

    with Server(
        "public",
        port=free_port,
    ) as server:
        monkeypatch.chdir(tmp_path / "elsewhere")
        result = fetch(server.url + "notes.txt").body

    expect = b"Notes"

    assert result == expect


@mark.parametrize(
    "path",
    [
        # A directory named without a trailing slash is redirected.
        "docs",
        "notes.txt",
        "missing.txt",
        # Symlinks are followed wherever they lead.
        "linked.txt",
        "linked/elsewhere.txt",
    ],
)
def test_server_serves_like_python(
    free_port: int,
    path: str,
    served: Path,
) -> None:
    """
    Assert that a server serves files, redirects and errors the way Python's own server
    does.
    """
    with (
        Server(
            served,
            port=free_port,
        ) as server,
        _serve_like_python(served) as python_url,
    ):
        result = fetch(server.url + path)
        python = fetch(python_url + path)

    expect = python._replace(
        headers=[*python.headers, _NO_STORE],
    )

    assert result == expect


@mark.parametrize(
    "directory",
    [
        # The served directory is the symlink.
        "link",
        # A symlink leads to the served directory's parent.
        "link/site",
    ],
)
def test_server_serves_where_symlink_leads_now(
    directory: str,
    free_port: int,
    tmp_path: Path,
) -> None:
    """
    Assert that a server serves from where a symlink on the path to its directory
    leads, after it's changed to lead elsewhere.
    """
    for name in ("before", "after"):
        (tmp_path / name / "site").mkdir(parents=True)
        (tmp_path / name / "notes.txt").write_text(name)
        (tmp_path / name / "site" / "notes.txt").write_text(name)

    link = tmp_path / "link"
    link.symlink_to(tmp_path / "before")

    with Server(
        tmp_path / directory,
        port=free_port,
    ) as server:
        before = fetch(server.url + "notes.txt").body

        link.unlink()
        link.symlink_to(tmp_path / "after")

        after = fetch(server.url + "notes.txt").body

    result = (
        before,
        after,
    )

    expect = (
        b"before",
        b"after",
    )

    assert result == expect


@mark.parametrize(
    "host",
    [
        "127.0.0.1",
        param(
            "[::1]",
            marks=needs_ipv6,
        ),
    ],
)
def test_server_serves_wherever_localhost_leads(
    free_port: int,
    host: str,
    tmp_path: Path,
) -> None:
    """Assert that a server serves at every address that `localhost` can mean."""
    with Server(
        tmp_path,
        port=free_port,
    ):
        result = fetch(f"http://{host}:{free_port}/").status

    expect = 200

    assert result == expect


@mark.parametrize(
    ("path", "body"),
    [
        param(
            "reload.js",
            b"{\n"
            b"  const src = new URL(document.currentScript.src);\n"
            b"  const page = {\n"
            b'    run: src.searchParams.get("run"),\n'
            b'    served: Number(src.searchParams.get("reloads")),\n'
            b"  };\n"
            b'  if (typeof SharedWorker === "function") {\n'
            b'    const url = new URL("/__crumpet__/worker.js", src);\n'
            b'    const port = new SharedWorker(url, "runs").port;\n'
            b"    port.onmessage = () => location.reload();\n"
            b"    port.postMessage(page);\n"
            b'    addEventListener("pagehide", () => port.postMessage(false));\n'
            b'    addEventListener("pageshow", (event) => {\n'
            b"      if (event.persisted) port.postMessage(page);\n"
            b"    });\n"
            b"  } else {\n"
            b'    const url = new URL("/__crumpet__/events", src);\n'
            b"    const events = new EventSource(url);\n"
            b"    events.onmessage = (event) => {\n"
            b"      const run = event.lastEventId;\n"
            b"      if (run !== page.run || page.served < Number(event.data)) {\n"
            b"        location.reload();\n"
            b"      }\n"
            b"    };\n"
            b"  }\n"
            b"}\n",
            id="reload",
        ),
        param(
            "worker.js",
            b"const pages = new Map();\n"
            b"let events = null;\n"
            b"let run = null;\n"
            b"let reloads = null;\n"
            b"const outdated = (page) =>\n"
            b"  run !== null && (page.run !== run || page.served < reloads);\n"
            b"onconnect = (event) => {\n"
            b"  const port = event.ports[0];\n"
            b"  port.onmessage = ({ data }) => {\n"
            b"    if (data === false) return pages.delete(port);\n"
            b"    pages.set(port, data);\n"
            b"    if (outdated(data)) port.postMessage(null);\n"
            b"  };\n"
            b"  if (events) return;\n"
            b'  events = new EventSource("/__crumpet__/events");\n'
            b"  events.onerror = () => {\n"
            b"    run = null;\n"
            b"  };\n"
            b"  events.onmessage = (event) => {\n"
            b"    run = event.lastEventId;\n"
            b"    reloads = Number(event.data);\n"
            b"    for (const [port, page] of pages) {\n"
            b"      if (outdated(page)) port.postMessage(null);\n"
            b"    }\n"
            b"  };\n"
            b"};\n",
            id="worker",
        ),
    ],
)
def test_server_serves_reload_scripts(
    body: bytes,
    free_port: int,
    path: str,
    tmp_path: Path,
) -> None:
    """Assert that a server serves the scripts that reload HTML files."""
    with Server(
        tmp_path,
        port=free_port,
    ) as server:
        response = fetch(server.url + "__crumpet__/" + path)

    headers = dict(response.headers)

    result = (
        response.status,
        headers["Content-type"],
        headers["Cache-Control"],
        response.body,
    )

    expect = (
        200,
        "text/javascript",
        "no-store",
        body,
    )

    assert result == expect
