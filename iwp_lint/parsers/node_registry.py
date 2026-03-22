from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher
from hashlib import sha1
from pathlib import Path
from typing import Any
from uuid import uuid4

from ..versioning import NODE_REGISTRY_FORMAT_VERSION


def normalize_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    normalized = re.sub(r"`+", "", normalized)
    normalized = re.sub(r"[^\w]+", " ", normalized, flags=re.UNICODE)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized


@dataclass(frozen=True)
class NodeSignature:
    source_path: str
    file_type_id: str
    section_key: str
    node_type: str
    parent_chain: str
    anchor_text: str

    def to_dict(self) -> dict[str, str]:
        return {
            "source_path": self.source_path,
            "file_type_id": self.file_type_id,
            "section_key": self.section_key,
            "node_type": self.node_type,
            "parent_chain": self.parent_chain,
            "anchor_text": self.anchor_text,
        }

    def canonical_key(self) -> str:
        payload = json.dumps(self.to_dict(), sort_keys=True, ensure_ascii=False)
        return sha1(payload.encode("utf-8")).hexdigest()


class NodeRegistry:
    def __init__(self, registry_path: Path) -> None:
        self.registry_path = registry_path
        self._entries: list[dict[str, Any]] = []
        self._exact_index: dict[str, list[dict[str, Any]]] = {}
        self._pool_index: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
        self._assigned_stable_keys: set[str] = set()
        self._current_entries: dict[str, dict[str, Any]] = {}
        self._loaded_stable_keys: set[str] = set()
        self._load()

    def assign_uid(self, signature: NodeSignature, override_uid: str | None = None) -> str:
        if override_uid:
            return self._reserve_stable_key(override_uid, signature)

        exact_key = signature.canonical_key()
        exact_candidates = self._exact_index.get(exact_key, [])
        for item in exact_candidates:
            stable_key = str(item.get("stable_key", ""))
            if stable_key and stable_key not in self._assigned_stable_keys:
                return self._reserve_stable_key(stable_key, signature)

        fuzzy_stable_key = self._match_fuzzy_stable_key(signature)
        if fuzzy_stable_key:
            return self._reserve_stable_key(fuzzy_stable_key, signature)

        return self._reserve_stable_key(self._new_stable_key(signature), signature)

    def flush(self) -> None:
        payload = {
            "version": NODE_REGISTRY_FORMAT_VERSION,
            "entries": sorted(
                self._current_entries.values(),
                key=lambda item: (
                    str(item["signature"]["source_path"]),
                    str(item["uid"]),
                ),
            ),
        }
        self.registry_path.parent.mkdir(parents=True, exist_ok=True)
        self.registry_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def _reserve_stable_key(self, stable_key: str, signature: NodeSignature) -> str:
        self._assigned_stable_keys.add(stable_key)
        self._current_entries[stable_key] = {
            "uid": stable_key,
            "stable_key": stable_key,
            "signature_key": signature.canonical_key(),
            "signature": signature.to_dict(),
        }
        return stable_key

    def set_canonical_uid(self, stable_key: str, canonical_uid: str) -> None:
        entry = self._current_entries.get(stable_key)
        if not entry:
            return
        entry["uid"] = canonical_uid

    def _match_fuzzy_stable_key(self, signature: NodeSignature) -> str | None:
        pool_key = (signature.source_path, signature.section_key, signature.node_type)
        candidates = self._pool_index.get(pool_key, [])
        best_stable_key: str | None = None
        best_score = 0.0
        for item in candidates:
            stable_key = str(item.get("stable_key", ""))
            if not stable_key or stable_key in self._assigned_stable_keys:
                continue
            candidate_sig = item.get("signature", {})
            anchor_score = _sim(signature.anchor_text, str(candidate_sig.get("anchor_text", "")))
            parent_score = _sim(signature.parent_chain, str(candidate_sig.get("parent_chain", "")))
            score = (0.8 * anchor_score) + (0.2 * parent_score)
            if score > best_score:
                best_score = score
                best_stable_key = stable_key
        return best_stable_key if best_score >= 0.84 else None

    def _load(self) -> None:
        if not self.registry_path.exists():
            return
        try:
            raw = json.loads(self.registry_path.read_text(encoding="utf-8"))
        except Exception:
            return
        entries = raw.get("entries", [])
        if not isinstance(entries, list):
            return
        for item in entries:
            if not isinstance(item, dict):
                continue
            uid = str(item.get("uid", "")).strip()
            signature = item.get("signature")
            if not isinstance(signature, dict):
                continue
            required_keys = {
                "source_path",
                "file_type_id",
                "section_key",
                "node_type",
                "parent_chain",
                "anchor_text",
            }
            if not required_keys.issubset(signature.keys()):
                continue
            stable_key = self._normalize_loaded_stable_key(item, uid, signature)
            if not stable_key:
                continue
            signature_obj = NodeSignature(
                source_path=str(signature["source_path"]),
                file_type_id=str(signature["file_type_id"]),
                section_key=str(signature["section_key"]),
                node_type=str(signature["node_type"]),
                parent_chain=str(signature["parent_chain"]),
                anchor_text=str(signature["anchor_text"]),
            )
            entry = {
                "uid": uid or stable_key,
                "stable_key": stable_key,
                "signature_key": signature_obj.canonical_key(),
                "signature": signature_obj.to_dict(),
            }
            self._entries.append(entry)
            self._exact_index.setdefault(entry["signature_key"], []).append(entry)
            pool_key = (
                signature_obj.source_path,
                signature_obj.section_key,
                signature_obj.node_type,
            )
            self._pool_index.setdefault(pool_key, []).append(entry)
            self._loaded_stable_keys.add(stable_key)

    def _normalize_loaded_stable_key(
        self,
        item: dict[str, Any],
        uid: str,
        signature: dict[str, Any],
    ) -> str:
        candidate = str(item.get("stable_key", "")).strip()
        if not candidate:
            return ""
        if candidate in self._loaded_stable_keys:
            candidate = sha1(f"{candidate}:{uid}".encode()).hexdigest()
        return candidate

    @staticmethod
    def _new_stable_key(signature: NodeSignature) -> str:
        return sha1(f"{signature.canonical_key()}:{uuid4().hex}".encode()).hexdigest()


def build_signature(
    source_path: str,
    file_type_id: str,
    section_key: str,
    node_type: str,
    parent_titles: list[str],
    anchor_text: str,
) -> NodeSignature:
    parent_chain = "|".join(normalize_text(item) for item in parent_titles if item.strip())
    return NodeSignature(
        source_path=source_path,
        file_type_id=file_type_id,
        section_key=section_key,
        node_type=node_type,
        parent_chain=parent_chain,
        anchor_text=normalize_text(anchor_text),
    )


def _sim(a: str, b: str) -> float:
    if a == b:
        return 1.0
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()


def build_short_node_ids_by_source(
    stable_keys_by_source: dict[str, list[str]],
    min_length: int = 4,
    reserved_ids_by_source: dict[str, set[str]] | None = None,
) -> dict[tuple[str, str], str]:
    out: dict[tuple[str, str], str] = {}
    min_len = max(1, int(min_length))

    for source_path, stable_keys in stable_keys_by_source.items():
        reserved_ids = reserved_ids_by_source.get(source_path, set()) if reserved_ids_by_source else set()
        unique_keys = sorted(set(stable_keys))
        hashed_values = {key: sha1(key.encode("utf-8")).hexdigest() for key in unique_keys}
        for key in unique_keys:
            key_hash = hashed_values[key]
            target_len = _shortest_unique_prefix_len(
                value=key_hash,
                values=list(hashed_values.values()),
                min_len=min_len,
                reserved_ids=reserved_ids,
            )
            resolved = f"n.{key_hash[:target_len]}"
            out[(source_path, key)] = resolved
            reserved_ids.add(resolved)
    return out


def _shortest_unique_prefix_len(
    value: str,
    values: list[str],
    min_len: int,
    reserved_ids: set[str],
) -> int:
    max_len = max([len(value), *[len(item) for item in values]])
    for size in range(min_len, max_len + 1):
        prefix = value[:size]
        collisions = [item for item in values if item.startswith(prefix)]
        if len(collisions) == 1 and f"n.{prefix}" not in reserved_ids:
            return size
    return len(value)
