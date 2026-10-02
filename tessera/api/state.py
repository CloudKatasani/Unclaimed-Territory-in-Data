"""Process-wide platform instance shared by the API routers."""

from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager

from tessera.config import get_settings
from tessera.platform import Tessera
from tessera.seed.generate import install

_lock = threading.RLock()
_app: Tessera | None = None


def get_app() -> Tessera:
    global _app
    with _lock:
        if _app is None:
            settings = get_settings()
            if not settings.db_path.exists():
                install(settings)
            _app = Tessera()
        return _app


@contextmanager
def platform() -> Iterator[Tessera]:
    """Serialise requests (DuckDB connection + deterministic bus) and drain events afterwards."""
    with _lock:
        app = get_app()
        try:
            yield app
        finally:
            app.bus.drain()


def reset() -> Tessera:
    global _app
    with _lock:
        if _app is not None:
            _app.close()
            _app = None
        install(get_settings())
        return get_app()


def set_app(app: Tessera | None) -> None:
    """Test hook."""
    global _app
    with _lock:
        _app = app
