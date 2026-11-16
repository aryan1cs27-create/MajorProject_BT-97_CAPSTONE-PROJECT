"""Optional semantic similarity (Sentence Transformers) with a real disabled mode.

The engine must never invent a semantic score. This module therefore:

* loads lazily on first use, once, with a lock;
* reports availability plus a human-readable reason (``MATCH_SEMANTIC_ENABLED=false``,
  package missing, model download failed, runtime error …);
* returns ``None`` whenever it cannot compute a score, so the scoring layer can
  drop the semantic component and redistribute its weight proportionally.

Everything is local: no paid API, no API key.
"""

from __future__ import annotations

import logging
import threading

import numpy as np

from app.core.config import get_settings

logger = logging.getLogger("matchwise.semantic")

_lock = threading.Lock()
_model = None
_state: dict[str, str | None] = {
    "status": "unknown",   # unknown | ready | disabled | error
    "reason": None,
}


def _load() -> None:
    global _model
    settings = get_settings()

    if not settings.semantic_enabled:
        _state.update(status="disabled", reason="Disabled by MATCH_SEMANTIC_ENABLED=false.")
        logger.info("Semantic similarity disabled by configuration.")
        return

    try:
        from sentence_transformers import SentenceTransformer
    except Exception as exc:  # ImportError or a broken torch install
        _state.update(
            status="disabled",
            reason=f"sentence-transformers is not available ({exc.__class__.__name__}). "
            "Install it with: pip install sentence-transformers",
        )
        logger.warning("Semantic similarity unavailable: %s", _state["reason"])
        return

    try:
        _model = SentenceTransformer(settings.semantic_model)
    except Exception as exc:
        _state.update(
            status="disabled",
            reason=f"Model '{settings.semantic_model}' could not be loaded "
            f"({exc.__class__.__name__}: {exc}).",
        )
        logger.warning("Semantic model unavailable: %s", _state["reason"])
        return

    _state.update(status="ready", reason=None)
    logger.info("Semantic similarity ready (model=%s).", settings.semantic_model)


def ensure_loaded() -> None:
    if _state["status"] == "unknown":
        with _lock:
            if _state["status"] == "unknown":
                _load()


def availability() -> tuple[bool, str | None]:
    """(available, reason-if-not)."""
    ensure_loaded()
    return _state["status"] == "ready", _state["reason"]


def reset() -> None:
    """Testing hook: forget the loaded model/state."""
    global _model
    with _lock:
        _model = None
        _state.update(status="unknown", reason=None)


def similarity(text_a: str, text_b: str) -> float | None:
    """Cosine similarity in 0..1, or None when semantic matching is unavailable.

    Empty input is a *missing fact*, not a zero: we return None so the caller
    redistributes weight instead of scoring an absent document as unrelated.
    """
    ok, _ = availability()
    if not ok or _model is None:
        return None
    if not text_a.strip() or not text_b.strip():
        return None

    try:
        vectors = _model.encode(
            [text_a, text_b],
            normalize_embeddings=True,
            show_progress_bar=False,
        )
        score = float(np.dot(vectors[0], vectors[1]))
    except Exception as exc:
        logger.warning("Semantic similarity failed (%s); disabling for this process.", exc.__class__.__name__)
        with _lock:
            _state.update(status="error", reason=f"Runtime error: {exc.__class__.__name__}.")
        return None

    # numerical noise guard
    return float(min(max(score, 0.0), 1.0))
