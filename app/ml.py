from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import joblib

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class MlDecision:
    spam: bool
    score: float
    model_version: str


class SpamModel:
    def __init__(self, path: Path, threshold: float, enabled: bool, min_text_length: int) -> None:
        self.path = path
        self.threshold = threshold
        self.enabled = enabled
        self.min_text_length = min_text_length
        self.model: Any | None = None
        self.version = "none"
        self.load()

    def load(self) -> None:
        if not self.enabled or not self.path.exists():
            return
        payload = joblib.load(self.path)
        if isinstance(payload, dict) and "model" in payload:
            self.model = payload["model"]
            self.version = str(payload.get("version", self.path.name))
        else:
            self.model = payload
            self.version = self.path.name
        logger.info("Loaded ML spam model %s from %s", self.version, self.path)

    @property
    def ready(self) -> bool:
        return bool(self.enabled and self.model is not None)

    def predict(self, text: str, threshold: float | None = None) -> MlDecision | None:
        if not self.ready or len(text.strip()) < self.min_text_length:
            return None
        if hasattr(self.model, "predict_proba"):
            score = float(self.model.predict_proba([text])[0][1])
        elif hasattr(self.model, "decision_function"):
            raw = float(self.model.decision_function([text])[0])
            score = 1.0 / (1.0 + pow(2.718281828, -raw))
        else:
            score = float(self.model.predict([text])[0])
        limit = self.threshold if threshold is None else threshold
        return MlDecision(spam=score >= limit, score=score, model_version=self.version)
