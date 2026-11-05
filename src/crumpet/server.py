from errno import ENOENT, ENOTDIR
from os import PathLike, fspath, strerror
from stat import S_ISDIR

from rootle import status


class Server:
    """
    Serves a directory on `localhost`.

    Args:
        directory: Path to the directory to serve.
        port: Port to serve on.

    Raises:
        FileNotFoundError: When `directory` doesn't exist.
        NotADirectoryError: When `directory` isn't a directory.
        NotImplementedError: When `directory` is a directory, because serving isn't
            implemented yet.
        OSError: When the path to `directory` can't be followed.
    """

    def __init__(
        self,
        directory: PathLike[str] | str,
        *,
        port: int,
    ) -> None:
        directory_status = status(directory)

        if directory_status is None:
            raise FileNotFoundError(
                ENOENT,
                strerror(ENOENT),
                fspath(directory),
            )

        if not S_ISDIR(directory_status.st_mode):
            raise NotADirectoryError(
                ENOTDIR,
                strerror(ENOTDIR),
                fspath(directory),
            )

        raise NotImplementedError("Serving isn't implemented yet")
