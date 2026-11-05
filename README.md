# crumpet

Cosy local HTTP hosting.

## Usage

`Server` is a context manager that serves a directory on `localhost` for the duration of the `with` block:

```python
from crumpet import Server

with Server("build", port=8000) as server:
    print(server.url)  # http://localhost:8000/
```
