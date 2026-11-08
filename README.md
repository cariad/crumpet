# Crumpet

[![CI][ci-badge]][ci] [![Coverage][coverage-badge]][coverage]

[ci-badge]: https://github.com/cariad/crumpet/actions/workflows/ci.yml/badge.svg
[coverage-badge]: https://codecov.io/gh/cariad/crumpet/graph/badge.svg

[ci]: https://github.com/cariad/crumpet/actions/workflows/ci.yml
[coverage]: https://codecov.io/gh/cariad/crumpet

**A cosy web server for local files.**

Crumpet previews a static site while you build it, and reloads open pages when files change. Run it from the command line, or from inside your own Python program.

## Quick start

```bash
pip install crumpet
crumpet ./public
```

Open <http://localhost:8000/>, and every page reloads whenever anything in the `public` directory changes.

Or, serve from your own script, and reload pages when you're ready:

```python
from pathlib import Path

from crumpet import Server

site = Path("public")
site.mkdir(exist_ok=True)

with Server(site, port=8000) as server:
    input(f"Open {server.url}, then press Enter to build: ")
    (site / "index.html").write_text("<h1>Built</h1>")
    server.reload()  # The open page reloads, and shows "Built"
    input("Press Enter to stop: ")
```

## Features

- **Reloads open pages when files change.** Crumpet waits for a burst of changes to settle, so pages only show the finished build.
- **Runs inside your program.** Serve in a `with` block, rebuild, and call `server.reload()`.
- **Works with a strict Content Security Policy.** The reload script loads from the server, not inline.
- **Lists directories**, like `python -m http.server`, and reloads them too.
- **Never shows stale files.** Every response is sent with `Cache-Control: no-store`.
- **Small and typed.** It's built on Python's own `http.server`, checked with strict pyright, and tested on Linux, macOS, and Windows.

See how Crumpet [compares with livereload and `http.server`](https://cariad.github.io/crumpet/comparison/).

## Why NOT use Crumpet?

- **You want to host a public website.** Crumpet is a local development server *only*.
- **You use Python earlier than 3.14.** Crumpet supports modern Python *only*.
- **You want efficient replacement of style sheets.** `livereload` is clever about replacing style sheets without reloading pages. Crumpet will just reload the page.

## Usage

### Preview a directory from the command line

```text
crumpet [DIRECTORY] [--port PORT]
```

`DIRECTORY` defaults to the current working directory, and `PORT` to 8000. Crumpet serves until you press Ctrl+C.

For example, to preview your `public` subdirectory on port 8001:

```bash
crumpet public --port 8001
```

### Preview a directory from a Python script

`watch=True` reloads every open page whenever anything in the directory changes:

```python
from crumpet import Server

with Server("public", port=8001, watch=True) as server:
    print(f"Serving at {server.url}")
    server.wait()  # Serve until Ctrl+C
```

## Documentation

The full documentation, including the [Python API](https://cariad.github.io/crumpet/api/), is at [cariad.github.io/crumpet](https://cariad.github.io/crumpet/).

## Contributing

Ideas, questions and bug reports are always welcome as [issues](https://github.com/cariad/crumpet/issues), even if you don't want to write the code yourself.

If you'd like to make a change, please open an issue first so we can agree on the approach. I'm unlikely to accept a pull request for work that hasn't been discussed.

AI-assisted contributions are welcome. [CONTRIBUTING.md](https://github.com/cariad/crumpet/blob/main/CONTRIBUTING.md) explains how to set up a development environment with mise, and how to lint and test.

## Licence

MIT. See [LICENSE](https://github.com/cariad/crumpet/blob/main/LICENSE).
