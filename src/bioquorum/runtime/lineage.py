"""Optional kernel-side I/O observations; they supplement declared, CAS-backed inputs."""

LINEAGE_MIME = "application/vnd.bioquorum.lineage+json"

PRELUDE = r"""
def __bq_install_lineage():
    from functools import wraps
    from importlib import import_module
    from os import fspath
    from IPython.display import publish_display_data

    def wrap(target, method, direction):
        original = getattr(target, method, None)
        if original is None:
            return

        @wraps(original)
        def observed(*args, **kwargs):
            result = original(*args, **kwargs)
            try:
                offset = int(hasattr(target, "__mro__"))
                path = args[offset] if len(args) > offset else next(
                    kwargs[k] for k in ("path", "filename", "fname", "filepath_or_buffer", "path_or_buf")
                    if k in kwargs
                )
                path = fspath(path)
                if isinstance(path, str) and len(path) <= 1024:
                    publish_display_data({__MIME__: {
                        "path": path, "direction": direction, "function": method,
                    }})
            except Exception:
                pass
            return result

        setattr(target, method, observed)

    for module, owner, method, direction in (
        ("pandas", None, "read_csv", "input"),
        ("pandas", None, "read_table", "input"),
        ("pandas", None, "read_parquet", "input"),
        ("pandas", "DataFrame", "to_csv", "output"),
        ("pandas", "DataFrame", "to_parquet", "output"),
        ("anndata", None, "read_h5ad", "input"),
        ("anndata", "AnnData", "write_h5ad", "output"),
        ("matplotlib.figure", "Figure", "savefig", "output"),
    ):
        try:
            library = import_module(module)
            wrap(getattr(library, owner) if owner else library, method, direction)
        except Exception:
            pass

__bq_install_lineage()
del __bq_install_lineage
""".replace("__MIME__", repr(LINEAGE_MIME))


# Record user-facing files written by a cell without scanning the project directory.
OUTPUT_PRELUDE = r"""
def __bq_watch_outputs():
    import os, sys
    from IPython import get_ipython
    from IPython.display import publish_display_data

    root = os.path.realpath(os.getcwd())
    extensions = {".csv", ".tsv", ".md", ".markdown", ".txt", ".png", ".jpg", ".jpeg",
                  ".svg", ".webp", ".gif", ".parquet", ".h5ad", ".rds", ".pdb", ".cif", ".mmcif",
                  ".fa", ".faa", ".fna", ".fasta", ".mol", ".sdf", ".bed"}
    pending = {}
    active = False

    def stamp(path):
        try:
            st = os.stat(path)
            return st.st_ino, st.st_size, st.st_mtime_ns
        except OSError:
            return None

    def remember(event, args):
        if not active or len(pending) >= 64:
            return
        if event == "open" and args[2] & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC):
            path = args[0]
        elif event == "os.rename" and args[3] == -1:
            path = args[1]
        else:
            return
        if not isinstance(path, str) or os.path.splitext(path)[1].lower() not in extensions:
            return
        try:
            relative = os.path.relpath(os.path.realpath(path), root)
            if len(relative) <= 1024 and not any(p.startswith(".") for p in relative.split(os.sep)):
                if relative not in pending:
                    pending[relative] = stamp(os.path.join(root, relative))
        except (OSError, ValueError):
            pass

    def begin(*_):
        nonlocal active
        pending.clear()
        active = True

    def finish(*_):
        nonlocal active
        active = False
        for relative, before in pending.items():
            after = stamp(os.path.join(root, relative))
            if after is not None and after != before:
                publish_display_data({__MIME__: {"path": relative, "direction": "output",
                                                 "function": "file_write"}})
        pending.clear()

    sys.addaudithook(remember)
    get_ipython().events.register("pre_run_cell", begin)
    get_ipython().events.register("post_run_cell", finish)

__bq_watch_outputs()
del __bq_watch_outputs
""".replace("__MIME__", repr(LINEAGE_MIME))
