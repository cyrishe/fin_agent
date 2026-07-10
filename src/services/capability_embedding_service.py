from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
from threading import RLock
from typing import Any, Dict, List, Sequence

from src.utils.ai_service import DEFAULT_EMBEDDING_MODEL, create_llm_embeddings


class CapabilityEmbeddingService:
    EMBEDDING_BATCH_SIZE = 10
    VALID_PROVIDERS = {"maas", "local", "fallback"}

    def __init__(
        self,
        *,
        provider: str = "",
        model_name: str = "",
        cache_path: str = "",
        model_path: str = "",
    ) -> None:
        self.provider = self._normalize_provider(
            str(provider or os.getenv("CAPABILITY_EMBEDDING_PROVIDER") or "maas")
        )
        self.model_name = str(model_name or os.getenv("CAPABILITY_EMBEDDING_MODEL") or DEFAULT_EMBEDDING_MODEL).strip() or DEFAULT_EMBEDDING_MODEL
        self.model_path = str(model_path or os.getenv("CAPABILITY_LOCAL_EMBEDDING_MODEL_PATH") or "").strip()
        self.cache_path = self._resolve_cache_path(cache_path)
        self._lock = RLock()
        self._local_model = None
        self._local_model_ready = False
        self._cache = self._load_cache()

    def score(self, *, query: str, documents: Sequence[str]) -> List[float]:
        normalized_query = str(query or "").strip()
        normalized_docs = [str(item or "").strip() for item in documents]
        if not normalized_query or not normalized_docs:
            return [0.0 for _ in normalized_docs]

        try:
            query_vector = self.embed_query(normalized_query)
            doc_vectors = self.embed_documents(normalized_docs, persist=True)
            return [round(self._cosine_similarity(query_vector, item), 6) for item in doc_vectors]
        except Exception:
            return self._fallback_score(query=normalized_query, documents=normalized_docs)

    def embed_query(self, text: str) -> List[float]:
        normalized = str(text or "").strip()
        if not normalized:
            return []
        vectors = self._embed_batch([normalized])
        if not vectors:
            return []
        return self._normalize_vector(vectors[0])

    def embed_documents(self, documents: Sequence[str], *, persist: bool = True) -> List[List[float]]:
        normalized_docs = [str(item or "").strip() for item in documents]
        if not normalized_docs:
            return []

        vectors: List[List[float] | None] = [None for _ in normalized_docs]
        missing_indexes: List[int] = []
        missing_texts: List[str] = []

        with self._lock:
            for index, text in enumerate(normalized_docs):
                cached = self._get_cached_vector(text)
                if cached is None:
                    missing_indexes.append(index)
                    missing_texts.append(text)
                else:
                    vectors[index] = cached

        if missing_texts:
            normalized_fresh: List[List[float]] = []
            for start in range(0, len(missing_texts), self.EMBEDDING_BATCH_SIZE):
                batch = missing_texts[start : start + self.EMBEDDING_BATCH_SIZE]
                fresh_vectors = self._embed_batch(batch)
                normalized_fresh.extend(self._normalize_vector(item) for item in fresh_vectors)
            if len(normalized_fresh) != len(missing_indexes):
                raise RuntimeError("embedding result count mismatch")
            with self._lock:
                for index, text, vector in zip(missing_indexes, missing_texts, normalized_fresh):
                    vectors[index] = vector
                    if persist:
                        self._set_cached_vector(text, vector)
                if persist:
                    self._save_cache()

        return [item or [] for item in vectors]

    def warm_texts(self, texts: Sequence[str]) -> Dict[str, Any]:
        normalized = [str(item or "").strip() for item in texts if str(item or "").strip()]
        if not normalized:
            return {"model": self.model_name, "count": 0, "cached": 0, "new": 0, "cache_path": str(self.cache_path)}
        cached = 0
        missing: List[str] = []
        with self._lock:
            for text in normalized:
                if self._get_cached_vector(text) is None:
                    missing.append(text)
                else:
                    cached += 1
        if missing:
            self.embed_documents(missing, persist=True)
        return {
            "provider": self.provider,
            "model": self.model_name,
            "count": len(normalized),
            "cached": cached,
            "new": len(missing),
            "cache_path": str(self.cache_path),
        }

    @classmethod
    def _normalize_provider(cls, value: str) -> str:
        normalized = str(value or "").strip().lower() or "maas"
        if normalized not in cls.VALID_PROVIDERS:
            return "maas"
        return normalized

    def _resolve_cache_path(self, cache_path: str) -> Path:
        normalized = str(cache_path or os.getenv("CAPABILITY_EMBEDDING_CACHE_PATH") or "").strip()
        if normalized:
            return Path(normalized)
        suffix = "maas" if self.provider == "maas" else "local" if self.provider == "local" else "fallback"
        return Path(f"data/capability_embeddings_{suffix}.json")

    def _embed_batch(self, texts: Sequence[str]) -> List[List[float]]:
        rows = [str(item or "").strip() for item in texts if str(item or "").strip()]
        if not rows:
            return []
        if self.provider == "fallback":
            return [self._fallback_dense_vector(item) for item in rows]
        if self.provider == "local":
            model = self._get_local_model()
            if model is not None:
                return model.encode(rows, normalize_embeddings=True).tolist()
            return [self._fallback_dense_vector(item) for item in rows]
        vectors, _usage = create_llm_embeddings(rows, model=self.model_name)
        return [list(item or []) for item in vectors]

    def _get_local_model(self):
        if self._local_model_ready:
            return self._local_model
        self._local_model_ready = True
        try:
            from sentence_transformers import SentenceTransformer
        except Exception:
            self._local_model = None
            return None
        for path in self._candidate_local_model_paths():
            try:
                self._local_model = SentenceTransformer(path)
                return self._local_model
            except Exception:
                continue
        self._local_model = None
        return None

    def _candidate_local_model_paths(self) -> List[str]:
        candidates: List[str] = []
        if self.model_path:
            candidates.append(self.model_path)
        env_path = str(os.getenv("CAPABILITY_EMBEDDING_MODEL_PATH") or "").strip()
        if env_path:
            candidates.append(env_path)
        candidates.extend(
            [
                "/home/che/cyris/model_bank/maidalun/bce-embedding-base_v1/",
                "/storage/cyris/model_bank/maidalun/bce-embedding-base_v1/",
                "/data/cyris/model_bank/maidalun/bce-embedding-base_v1/",
                "/Users/chenghe/work/naodong_ai/model_bank/maidalun/bce-embedding-base_v1/",
            ]
        )
        rows: List[str] = []
        seen = set()
        for item in candidates:
            normalized = str(item or "").strip()
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            if Path(normalized).exists():
                rows.append(normalized)
        return rows

    def _load_cache(self) -> Dict[str, Any]:
        if not self.cache_path.exists():
            return {"model": self.model_name, "items": {}}
        try:
            payload = json.loads(self.cache_path.read_text(encoding="utf-8"))
        except Exception:
            return {"model": self.model_name, "items": {}}
        items = payload.get("items") if isinstance(payload, dict) and isinstance(payload.get("items"), dict) else {}
        return {
            "model": str(payload.get("model") or self.model_name) if isinstance(payload, dict) else self.model_name,
            "items": items,
        }

    def _save_cache(self) -> None:
        payload = {
            "model": self.model_name,
            "items": self._cache.get("items") if isinstance(self._cache.get("items"), dict) else {},
        }
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        self.cache_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def _cache_key(self, text: str) -> str:
        base = f"{self.model_name}\n{text}".encode("utf-8")
        return hashlib.sha256(base).hexdigest()

    def _get_cached_vector(self, text: str) -> List[float] | None:
        key = self._cache_key(text)
        items = self._cache.get("items") if isinstance(self._cache.get("items"), dict) else {}
        row = items.get(key) if isinstance(items.get(key), dict) else None
        vector = row.get("vector") if isinstance(row, dict) else None
        if not isinstance(vector, list):
            return None
        try:
            return [float(x) for x in vector]
        except Exception:
            return None

    def _set_cached_vector(self, text: str, vector: Sequence[float]) -> None:
        key = self._cache_key(text)
        items = self._cache.setdefault("items", {})
        items[key] = {
            "text": text,
            "vector": [float(x) for x in vector],
        }

    @staticmethod
    def _normalize_vector(vector: Sequence[float]) -> List[float]:
        rows = [float(x) for x in (vector or [])]
        if not rows:
            return []
        norm = math.sqrt(sum(x * x for x in rows))
        if norm <= 0:
            return rows
        return [x / norm for x in rows]

    @classmethod
    def _cosine_similarity(cls, left: Sequence[float], right: Sequence[float]) -> float:
        if not left or not right:
            return 0.0
        size = min(len(left), len(right))
        if size <= 0:
            return 0.0
        return float(sum(float(left[i]) * float(right[i]) for i in range(size)))

    def _fallback_score(self, *, query: str, documents: Sequence[str]) -> List[float]:
        query_terms = self._tokenize(query)
        scores: List[float] = []
        for doc in documents:
            doc_terms = self._tokenize(doc)
            if not query_terms or not doc_terms:
                scores.append(0.0)
                continue
            overlap = len(query_terms & doc_terms)
            score = overlap / math.sqrt(len(query_terms) * len(doc_terms))
            scores.append(round(score, 6))
        return scores

    def _fallback_dense_vector(self, text: str) -> List[float]:
        tokens = sorted(self._tokenize(text))
        if not tokens:
            return []
        dims = 256
        vector = [0.0] * dims
        for token in tokens:
            digest = hashlib.sha256(token.encode("utf-8")).digest()
            bucket = int.from_bytes(digest[:2], "big") % dims
            sign = 1.0 if digest[2] % 2 == 0 else -1.0
            vector[bucket] += sign
        return self._normalize_vector(vector)

    @staticmethod
    def _tokenize(text: str) -> set[str]:
        normalized = str(text or "").strip().lower()
        if not normalized:
            return set()
        rows: set[str] = set()
        current = []
        for ch in normalized:
            if ch.isalnum() or "\u4e00" <= ch <= "\u9fff":
                current.append(ch)
            else:
                if current:
                    token = "".join(current).strip()
                    if len(token) >= 2:
                        rows.add(token)
                    current = []
        if current:
            token = "".join(current).strip()
            if len(token) >= 2:
                rows.add(token)
        for size in (2, 3, 4):
            for index in range(0, max(0, len(normalized) - size + 1)):
                token = normalized[index : index + size].strip()
                if all("\u4e00" <= ch <= "\u9fff" for ch in token):
                    rows.add(token)
        return rows
