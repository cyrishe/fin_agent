#!/usr/bin/env python3
"""Evaluate catalog-entry retrieval with a real DashScope embedding model.

This is an experiment-only evaluator.  It deliberately calls DashScope
directly and fails closed: no lexical or local fallback is allowed, because a
silent fallback would make the reported embedding accuracy meaningless.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import requests


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CASES = (
    ROOT
    / "outputs"
    / "report_routing_embedding_experiment_20260831"
    / "cases_seed20260831_n100.json"
)
DEFAULT_CATALOG = ROOT / "src" / "tools" / "finance_data" / "catalog" / "api_view_catalog.json"
DEFAULT_OUTPUT = (
    ROOT
    / "outputs"
    / "report_routing_embedding_experiment_20260831"
    / "qwen37_embedding_top3.json"
)
DEFAULT_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _load_dotenv_value(path: Path, key: str) -> str:
    if not path.exists():
        return ""
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        if name.strip() != key:
            continue
        return value.strip().strip('"').strip("'")
    return ""


def _chunks(items: list[str], size: int) -> Iterable[list[str]]:
    for start in range(0, len(items), size):
        yield items[start : start + size]


def _catalog_documents(catalog: dict[str, Any]) -> list[dict[str, str]]:
    documents: list[dict[str, str]] = []
    for subject, subject_body in catalog.get("subjects", {}).items():
        if not isinstance(subject_body, dict):
            continue
        subject_desc = str((subject_body.get("_meta") or {}).get("desc") or "").strip()
        for dataview, view_body in subject_body.items():
            if dataview == "_meta" or not isinstance(view_body, dict):
                continue
            entry = f"{subject}.{dataview}"
            view_desc = str(view_body.get("desc") or "").strip()
            documents.append(
                {
                    "entry": entry,
                    "subject": subject,
                    "dataview": dataview,
                    "subject_desc": subject_desc,
                    "dataview_desc": view_desc,
                    "embedding_text": (
                        f"数据入口：{entry}\n"
                        f"主体说明：{subject_desc}\n"
                        f"视图用途：{view_desc}"
                    ),
                }
            )
    return documents


class DashScopeEmbeddings:
    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model: str,
        dimensions: int,
        timeout_seconds: float,
        max_retries: int,
    ) -> None:
        self._session = requests.Session()
        self._session.headers.update(
            {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        )
        self.endpoint = base_url.rstrip("/") + "/embeddings"
        self.model = model
        self.dimensions = dimensions
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries
        self.calls: list[dict[str, Any]] = []
        self.verified_models: set[str] = set()

    def embed(self, inputs: list[str], *, purpose: str) -> list[list[float]]:
        payload = {
            "model": self.model,
            "input": inputs,
            "dimensions": self.dimensions,
            "encoding_format": "float",
        }
        last_error = ""
        for attempt in range(1, self.max_retries + 2):
            started = time.perf_counter()
            response: requests.Response | None = None
            try:
                response = self._session.post(
                    self.endpoint, json=payload, timeout=self.timeout_seconds
                )
                elapsed_ms = round((time.perf_counter() - started) * 1000.0, 3)
                if response.status_code >= 400:
                    body = response.text[:1000]
                    raise RuntimeError(
                        f"DashScope embeddings HTTP {response.status_code}: {body}"
                    )
                result = response.json()
                response_model = str(result.get("model") or "").strip()
                if response_model:
                    self.verified_models.add(response_model)
                rows = sorted(result.get("data") or [], key=lambda item: item.get("index", 0))
                vectors = [item.get("embedding") for item in rows]
                if len(vectors) != len(inputs):
                    raise RuntimeError(
                        f"embedding count mismatch: expected={len(inputs)} actual={len(vectors)}"
                    )
                if any(not isinstance(vector, list) for vector in vectors):
                    raise RuntimeError("embedding response contains a non-vector item")
                actual_dimensions = {len(vector) for vector in vectors}
                if actual_dimensions != {self.dimensions}:
                    raise RuntimeError(
                        f"embedding dimension mismatch: expected={self.dimensions} actual={sorted(actual_dimensions)}"
                    )
                self.calls.append(
                    {
                        "purpose": purpose,
                        "input_count": len(inputs),
                        "attempt": attempt,
                        "elapsed_ms": elapsed_ms,
                        "http_status": response.status_code,
                        "response_model": response_model,
                    }
                )
                return vectors
            except Exception as exc:
                elapsed_ms = round((time.perf_counter() - started) * 1000.0, 3)
                status = response.status_code if response is not None else None
                last_error = str(exc)
                self.calls.append(
                    {
                        "purpose": purpose,
                        "input_count": len(inputs),
                        "attempt": attempt,
                        "elapsed_ms": elapsed_ms,
                        "http_status": status,
                        "error": last_error,
                    }
                )
                retryable = status in {408, 409, 429, 500, 502, 503, 504} or status is None
                if attempt > self.max_retries or not retryable:
                    break
                time.sleep(min(4.0, 0.5 * (2 ** (attempt - 1))))
        raise RuntimeError(
            f"DashScope embedding failed after {self.max_retries + 1} attempts: {last_error}"
        )


def _cosine(left: list[float], right: list[float]) -> float:
    dot = sum(a * b for a, b in zip(left, right))
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if left_norm == 0 or right_norm == 0:
        return 0.0
    return dot / (left_norm * right_norm)


def _mean(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 3) if values else None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--model", default="qwen3.7-text-embedding")
    parser.add_argument("--dimensions", type=int, default=1024)
    parser.add_argument("--catalog-batch-size", type=int, default=20)
    parser.add_argument("--query-batch-size", type=int, default=1)
    parser.add_argument("--timeout", type=float, default=60.0)
    parser.add_argument("--max-retries", type=int, default=2)
    args = parser.parse_args()

    cases_payload = json.loads(args.cases.read_text(encoding="utf-8"))
    cases = list(cases_payload.get("cases") or [])
    catalog = json.loads(args.catalog.read_text(encoding="utf-8"))
    documents = _catalog_documents(catalog)
    if not documents:
        raise SystemExit("catalog contains no subject.dataview documents")

    env_path = ROOT / ".env"
    api_key = os.environ.get("DASHSCOPE_API_KEY") or _load_dotenv_value(
        env_path, "DASHSCOPE_API_KEY"
    )
    if not api_key:
        raise SystemExit("DASHSCOPE_API_KEY is required")
    base_url = (
        os.environ.get("DASHSCOPE_BASE_URL")
        or _load_dotenv_value(env_path, "DASHSCOPE_BASE_URL")
        or DEFAULT_BASE_URL
    )
    client = DashScopeEmbeddings(
        api_key=api_key,
        base_url=base_url,
        model=args.model,
        dimensions=args.dimensions,
        timeout_seconds=args.timeout,
        max_retries=args.max_retries,
    )

    started = time.perf_counter()
    document_vectors: list[list[float]] = []
    doc_texts = [item["embedding_text"] for item in documents]
    for index, batch in enumerate(_chunks(doc_texts, args.catalog_batch_size), start=1):
        document_vectors.extend(client.embed(batch, purpose=f"catalog_batch_{index}"))
    catalog_elapsed_ms = round((time.perf_counter() - started) * 1000.0, 3)

    query_vectors: list[list[float]] = []
    questions = [str(case.get("question") or "").strip() for case in cases]
    query_started = time.perf_counter()
    for index, batch in enumerate(_chunks(questions, args.query_batch_size), start=1):
        query_vectors.extend(client.embed(batch, purpose=f"query_batch_{index}"))
    query_elapsed_ms = round((time.perf_counter() - query_started) * 1000.0, 3)

    results: list[dict[str, Any]] = []
    for case, query_vector in zip(cases, query_vectors):
        ranked = sorted(
            (
                {
                    "entry": document["entry"],
                    "score": round(_cosine(query_vector, vector), 8),
                }
                for document, vector in zip(documents, document_vectors)
            ),
            key=lambda item: item["score"],
            reverse=True,
        )
        top3 = ranked[:3]
        acceptable = set(case.get("acceptable_first_entries") or [])
        required = set(case.get("required_entries") or [])
        top_entries = [item["entry"] for item in top3]
        required_hits_top2 = required.intersection(top_entries[:2])
        results.append(
            {
                "case_id": case.get("case_id"),
                "source_ordinal": case.get("source_ordinal"),
                "question": case.get("question"),
                "category": case.get("category"),
                "primary_entry": case.get("primary_entry"),
                "acceptable_first_entries": list(acceptable),
                "required_entries": list(required),
                "top1": top3[0],
                "top2": top3[:2],
                "top3": top3,
                "top1_margin": round(top3[0]["score"] - top3[1]["score"], 8),
                "acceptable_hit_at_1": bool(acceptable.intersection(top_entries[:1])),
                "acceptable_hit_at_2": bool(acceptable.intersection(top_entries[:2])),
                "acceptable_hit_at_3": bool(acceptable.intersection(top_entries[:3])),
                "primary_hit_at_1": case.get("primary_entry") in top_entries[:1],
                "primary_hit_at_2": case.get("primary_entry") in top_entries[:2],
                "primary_hit_at_3": case.get("primary_entry") in top_entries[:3],
                "required_hits_at_2": sorted(required_hits_top2),
                "required_coverage_at_2": round(
                    len(required_hits_top2) / len(required), 6
                )
                if required
                else None,
                "all_required_hit_at_2": required.issubset(set(top_entries[:2])),
            }
        )

    query_calls = [item for item in client.calls if item["purpose"].startswith("query_batch_") and not item.get("error")]
    summary = {
        "case_count": len(results),
        "catalog_entry_count": len(documents),
        "acceptable_hit_at_1_count": sum(item["acceptable_hit_at_1"] for item in results),
        "acceptable_hit_at_1_rate": round(
            sum(item["acceptable_hit_at_1"] for item in results) / len(results), 6
        ),
        "acceptable_hit_at_2_count": sum(item["acceptable_hit_at_2"] for item in results),
        "acceptable_hit_at_2_rate": round(
            sum(item["acceptable_hit_at_2"] for item in results) / len(results), 6
        ),
        "acceptable_hit_at_3_count": sum(item["acceptable_hit_at_3"] for item in results),
        "acceptable_hit_at_3_rate": round(
            sum(item["acceptable_hit_at_3"] for item in results) / len(results), 6
        ),
        "primary_hit_at_1_rate": round(
            sum(item["primary_hit_at_1"] for item in results) / len(results), 6
        ),
        "primary_hit_at_2_rate": round(
            sum(item["primary_hit_at_2"] for item in results) / len(results), 6
        ),
        "all_required_hit_at_2_rate": round(
            sum(item["all_required_hit_at_2"] for item in results) / len(results), 6
        ),
        "mean_required_coverage_at_2": round(
            sum(item["required_coverage_at_2"] or 0.0 for item in results)
            / len(results),
            6,
        ),
        "catalog_embedding_elapsed_ms": catalog_elapsed_ms,
        "query_embedding_elapsed_ms": query_elapsed_ms,
        "mean_query_request_elapsed_ms": _mean(
            [float(item["elapsed_ms"]) for item in query_calls]
        ),
    }
    catalog_hash = hashlib.sha256(
        json.dumps(documents, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()
    output = {
        "experiment": "catalog_embedding_routing_v1",
        "generated_at": _now_iso(),
        "backend": "dashscope_openai_compatible_embeddings",
        "base_url_host": requests.utils.urlparse(base_url).netloc,
        "requested_model": args.model,
        "verified_models": sorted(client.verified_models),
        "dimensions": args.dimensions,
        "fallback_used": False,
        "catalog_document_policy": "entry_id + subject._meta.desc + dataview.desc",
        "catalog_hash": catalog_hash,
        "cases_source": str(args.cases.resolve()),
        "catalog_source": str(args.catalog.resolve()),
        "summary": summary,
        "embedding_calls": client.calls,
        "catalog_documents": documents,
        "cases": results,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"output={args.output.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
