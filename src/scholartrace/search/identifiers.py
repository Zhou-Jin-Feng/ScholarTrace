"""Deterministic normalization for external paper identifiers and matching keys."""

from __future__ import annotations

import hashlib
import re
import unicodedata

ARXIV_DOI_PREFIX = "10.48550/arxiv."
ARXIV_PATTERN = re.compile(
    r"(?i)(?:arxiv:|https?://arxiv\.org/(?:abs|pdf)/)?"
    r"(?P<base>(?:\d{4}\.\d{4,5}|[a-z][a-z.\-]+/\d{7}))"
    r"(?:v(?P<version>\d+))?(?:\.pdf)?$"
)


def normalize_doi(value: str | None) -> str | None:
    if not value:
        return None
    normalized = unicodedata.normalize("NFKC", value).strip().lower()
    for prefix in ("https://doi.org/", "http://doi.org/", "doi:"):
        if normalized.startswith(prefix):
            normalized = normalized[len(prefix) :]
            break
    normalized = normalized.strip().rstrip(".,;)")
    return normalized or None


def parse_arxiv_id(value: str | None) -> tuple[str | None, int | None]:
    if not value:
        return None, None
    match = ARXIV_PATTERN.fullmatch(unicodedata.normalize("NFKC", value).strip())
    if not match:
        return None, None
    version = int(match.group("version")) if match.group("version") else None
    return match.group("base").lower(), version


def arxiv_id_from_doi(doi: str | None) -> str | None:
    normalized = normalize_doi(doi)
    if normalized and normalized.startswith(ARXIV_DOI_PREFIX):
        arxiv_id, _ = parse_arxiv_id(normalized[len(ARXIV_DOI_PREFIX) :])
        return arxiv_id
    return None


def normalize_openalex_id(value: str | None) -> str | None:
    if not value:
        return None
    normalized = value.strip().rstrip("/")
    if "/" in normalized:
        normalized = normalized.rsplit("/", 1)[-1]
    return normalized.upper() if re.fullmatch(r"(?i)W\d+", normalized) else None


def normalize_semantic_scholar_id(value: str | None) -> str | None:
    if not value:
        return None
    normalized = value.strip()
    return normalized.lower() if re.fullmatch(r"(?i)[a-f0-9]{40}", normalized) else normalized


def normalize_title(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    normalized = re.sub(r"[^\w]+", " ", normalized, flags=re.UNICODE)
    return " ".join(normalized.split())


def normalize_person(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    normalized = re.sub(r"[^\w]+", "", normalized, flags=re.UNICODE)
    return normalized


def candidate_id(source: str, source_id: str) -> str:
    digest = hashlib.sha256(f"{source}\0{source_id}".encode()).hexdigest()[:24]
    return f"candidate:{source}:{digest}"


def canonical_id(
    *,
    doi: str | None,
    arxiv_id: str | None,
    openalex_id: str | None,
    semantic_scholar_id: str | None,
) -> str:
    normalized_doi = normalize_doi(doi)
    normalized_arxiv, _ = parse_arxiv_id(arxiv_id)
    normalized_arxiv = normalized_arxiv or arxiv_id_from_doi(normalized_doi)
    normalized_openalex = normalize_openalex_id(openalex_id)
    normalized_s2 = normalize_semantic_scholar_id(semantic_scholar_id)
    if normalized_doi:
        return f"doi:{normalized_doi}"
    if normalized_arxiv:
        return f"arxiv:{normalized_arxiv}"
    if normalized_openalex:
        return f"openalex:{normalized_openalex}"
    if normalized_s2:
        return f"s2:{normalized_s2}"
    raise ValueError("paper candidate has no usable external identity")
