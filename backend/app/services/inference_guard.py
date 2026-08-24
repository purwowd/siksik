from __future__ import annotations

import threading
from collections.abc import Callable
from contextlib import contextmanager
from typing import TypeVar

T = TypeVar("T")

_inference_lock = threading.RLock()


@contextmanager
def gpu_inference_slot():
    _inference_lock.acquire()
    try:
        yield
    finally:
        _inference_lock.release()


def run_guarded(fn: Callable[..., T], /, *args, **kwargs) -> T:
    with gpu_inference_slot():
        return fn(*args, **kwargs)
