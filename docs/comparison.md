# Compared with other tools

## `python -m http.server`

Python's own server serves files as they are. It doesn't watch for changes, and adds no script to pages, so it can't reload them.

Crumpet is built on the same `http.server` module, and lists directories the same way.

## `livereload`

[`livereload`](https://github.com/lepture/python-livereload) is the established choice for reloading pages from Python, and it's clever about style sheets: it swaps changed CSS into an open page without reloading it. Crumpet just reloads the page.

### Reloading when files change

`livereload` ignores changes for 3 seconds after starting and after each reload, so that a build that writes many files doesn't reload the page once per file. A change within that window is dropped rather than delayed.

Crumpet waits for a burst of changes to finish instead. It reloads pages moments after the last change, and a long stream of changes only delays the reload briefly.

### Reloading from your own code

`livereload` reloads pages when the `GET /forcereload` endpoint is invoked, but the `serve()` function blocks, so the request has to come from another thread or process:

```python
from urllib.request import urlopen
from livereload import Server

server = Server()
server.serve(root="public")  # Blocks here until Ctrl+C
_rebuild_public_dir()  # Doesn't run while serving
urlopen("http://127.0.0.1:5500/forcereload")  # Too late: serving has ended
```

Crumpet serves for the duration of a `with` block, while your code carries on, and `server.reload()` reloads every open page:

```python
from crumpet import Server

with Server("public", port=8000) as server:
    _rebuild_public_dir()  # Runs while Crumpet serves
    server.reload()  # Reload every open page
    server.wait()  # Wait for Ctrl+C, if you want to

# Serving stops when the `with` block ends
```

### Strict Content Security Policies

A Content Security Policy like `default-src 'self'` restricts a page to loading files from its own host, and stops it running inline scripts, as a defence against injected code:

```html
<head>
  <meta http-equiv="Content-Security-Policy" content="default-src 'self'">
</head>
```

`livereload` embeds its reload script inline, which this policy blocks, so those pages never reload.

Crumpet loads its reload script from the server, which the policy allows.

### Directories

`livereload` has no directory listings: a directory without an `index.html` is a 404.

Crumpet lists directories without index pages, and reloads the listing when the directory changes.

### Types and platforms

`livereload` has no type hints, and its classifiers name only Linux and macOS.

Crumpet is fully typed and checked with pyright's strict mode, and every change is tested on Linux, macOS, and Windows.
