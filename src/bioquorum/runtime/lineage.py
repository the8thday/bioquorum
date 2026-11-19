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
