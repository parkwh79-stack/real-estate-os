"""Local workspace histories loaded only when their project is opened."""
from collections.abc import MutableMapping
from copy import deepcopy
from threading import RLock


class LazyProjectCollection(MutableMapping):
    """Keep the startup key index and mutable, per-project repository values.

    This is an in-process compatibility store, not a cross-process cache or a
    PostgreSQL repository. Failed reads are never cached as empty histories.
    """

    def __init__(self, project_ids, load_project):
        self._keys = dict.fromkeys(project_ids)
        self._loaded = {}
        self._load_project = load_project
        self._lock = RLock()

    def __getitem__(self, key):
        with self._lock:
            if key not in self._keys:
                raise KeyError(key)
            if key not in self._loaded:
                self._loaded[key] = self._load_project(key)
            return self._loaded[key]

    def __setitem__(self, key, value):
        with self._lock:
            self._keys[key] = None
            self._loaded[key] = value

    def __delitem__(self, key):
        with self._lock:
            del self._keys[key]
            self._loaded.pop(key, None)

    def __iter__(self):
        with self._lock:
            return iter(tuple(self._keys))

    def __len__(self):
        with self._lock:
            return len(self._keys)

    def __contains__(self, key):
        with self._lock:
            return key in self._keys

    def clear(self):
        with self._lock:
            self._keys.clear()
            self._loaded.clear()

    def __deepcopy__(self, memo):
        # Explicit snapshots/full saves intentionally materialize all projects.
        return deepcopy(dict(self.items()), memo)
