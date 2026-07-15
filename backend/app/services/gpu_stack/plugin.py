"""Optional external model adapters.

Drop a module that exports ``moderate(path: Path) -> list[dict]`` (or
``list[ModerationHit]``), then point settings at it:

  SADT_GPU_SAFEWATCH_PLUGIN=mypackage.safewatch_adapter
  # or place ``sadt_adapter.py`` inside the checkpoint directory
"""

from __future__ import annotations

import importlib
import importlib.util
import logging
from pathlib import Path
from typing import Any, Callable

from app.services.gpu_stack.types import ModerationHit

log = logging.getLogger(__name__)


def _dicts_to_hits(
    raw: list[Any],
    *,
    default_layer: str,
    default_backend: str,
) -> list[ModerationHit]:
    hits: list[ModerationHit] = []
    for item in raw:
        if isinstance(item, ModerationHit):
            hits.append(item)
            continue
        if not isinstance(item, dict):
            continue
        hits.append(
            ModerationHit(
                category=str(item.get("category", "konten_visual")),
                label=str(item.get("label", "policy hit")),
                confidence=float(item.get("confidence", 0.75)),
                layer_origin=str(item.get("layer_origin", default_layer)),
                evidence=str(item.get("evidence", ""))[:280],
                backend=str(item.get("backend", default_backend)),
            )
        )
    return hits


def load_moderate_fn(plugin: str | None, model_dir: str | None) -> Callable[[Path], list[Any]] | None:
    """Resolve plugin dotted path, then ``{model_dir}/sadt_adapter.py``."""
    if plugin and plugin.strip():
        try:
            mod = importlib.import_module(plugin.strip())
            fn = getattr(mod, "moderate", None)
            if callable(fn):
                return fn  # type: ignore[return-value]
            log.warning("GPU plugin %s has no moderate()", plugin)
        except Exception as exc:
            log.warning("GPU plugin import failed (%s): %s", plugin, exc)

    if model_dir:
        adapter = Path(model_dir) / "sadt_adapter.py"
        if adapter.is_file():
            try:
                spec = importlib.util.spec_from_file_location("sadt_gpu_adapter", adapter)
                if spec and spec.loader:
                    mod = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(mod)
                    fn = getattr(mod, "moderate", None)
                    if callable(fn):
                        return fn  # type: ignore[return-value]
            except Exception as exc:
                log.warning("sadt_adapter load failed (%s): %s", adapter, exc)
    return None


def run_plugin(
    path: Path,
    *,
    plugin: str | None,
    model_dir: str | None,
    default_layer: str,
    default_backend: str,
) -> list[ModerationHit] | None:
    """Return hits if a plugin ran; ``None`` if no plugin / empty skip to bridge."""
    fn = load_moderate_fn(plugin, model_dir)
    if fn is None:
        return None
    try:
        raw = fn(path)
        if raw is None:
            return None
        return _dicts_to_hits(list(raw), default_layer=default_layer, default_backend=default_backend)
    except Exception as exc:
        log.warning("GPU plugin moderate failed: %s", exc)
        return None
