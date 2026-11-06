# crumpet

Cosy local HTTP hosting.

## Usage

### Command line

```text
crumpet [DIRECTORY] [--port PORT]
python -m crumpet [DIRECTORY] [--port PORT]
```

Serves `DIRECTORY` (default: the working directory) on `localhost` at `PORT` (default: 8000) until Ctrl+C.

### Python

`Server` is a context manager that serves a directory on `localhost` for the duration of the `with` block:

```python
from crumpet import Server

with Server("build", port=8000) as server:
    print(server.url)  # http://localhost:8000/
```
