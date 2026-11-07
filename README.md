# crumpet

Crumpet is a cosy little Python package and command-line tool for serving a directory as a local website.

## Installation

Crumpet requires Python 3.14 or later, and is tested on Linux, macOS, and Windows.

Install `crumpet` via your favourite package manager:

- ```bash
  pip install crumpet
  ```

- ```bash
  uv add crumpet
  ```

- ```bash
  poetry add crumpet
  ```

Or run Crumpet without installing, using `pipx`:

- ```bash
  pipx run crumpet
  ```

## Running on the command line

```text
crumpet [DIRECTORY] [--port PORT]
```

- `DIRECTORY` — the directory to serve. Defaults to the current working directory.
- `PORT` — the port to serve on. Defaults to 8000.

So, for example, to serve the current directory at <http://localhost:8000>, just run:

```bash
crumpet
```

To serve the `public` subdirectory:

```bash
crumpet public
```

To serve the `public` subdirectory on port 8001:

```bash
crumpet public --port 8001
```

Crumpet reloads every open HTML page whenever anything in the directory changes. Press Ctrl+C to stop serving.

## Usage as a library

`Server` is a context manager that serves for the duration of the `with` block:

```python
from crumpet import Server

with Server("public", port=8001) as server:
    print(server.url)  # http://localhost:8001/
    server.reload()  # Reload every open HTML page
```

Pass `watch=True` to reload open HTML pages whenever anything in the directory changes:

```python
with Server("public", port=8001, watch=True) as server:
    ...
```

## Related packages

Crumpet is built on these other packages that I wrote:

- [`notwork`](https://github.com/cariad/notwork) — Python functions for when your network might notwork
- [`rootle`](https://github.com/cariad/rootle) — Python functions to rootle around in your file system
