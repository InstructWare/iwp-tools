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
        self._assigned_uids: set[str] = set()
        self._current_entries: dict[str, dict[str, Any]] = {}
        self._load()

    def assign_uid(self, signature: NodeSignature, override_uid: str | None = None) -> str:
        if override_uid:
            return self._reserve(override_uid, signature)

        exact_key = signature.canonical_key()
        exact_candidates = self._exact_index.get(exact_key, [])
        for item in exact_candidates:
            uid = str(item.get("uid", ""))
            if uid and uid not in self._assigned_uids:
                return self._reserve(uid, signature)

        fuzzy_uid = self._match_fuzzy_uid(signature)
        if fuzzy_uid:
            return self._reserve(fuzzy_uid, signature)

        return self._reserve(self._new_uid(), signature)

    def flush(self) -> None:
        payload = {
            "version": 1,
            "entries": sorted(
                self._current_entries.values(),
                key=lambda item: (str(item["signature"]["source_path"]), str(item["uid"])),
            ),
        }
        self.registry_path.parent.mkdir(parents=True, exist_ok=True)
        self.registry_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def _reserve(self, uid: str, signature: NodeSignature) -> str:
        self._assigned_uids.add(uid)
        self._current_entries[uid] = {
            "uid": uid,
            "signature_key": signature.canonical_key(),
            "signature": signature.to_dict(),
        }
        return uid

    def _match_fuzzy_uid(self, signature: NodeSignature) -> str | None:
        pool_key = (signature.source_path, signature.section_key, signature.node_type)
        candidates = self._pool_index.get(pool_key, [])
        best_uid: str | None = None
        best_score = 0.0
        for item in candidates:
            uid = str(item.get("uid", ""))
            if not uid or uid in self._assigned_uids:
                continue
            candidate_sig = item.get("signature", {})
            anchor_score = _sim(signature.anchor_text, str(candidate_sig.get("anchor_text", "")))
            parent_score = _sim(signature.parent_chain, str(candidate_sig.get("parent_chain", "")))
            score = (0.8 * anchor_score) + (0.2 * parent_score)
            if score > best_score:
                best_score = score
                best_uid = uid
        return best_uid if best_score >= 0.84 else None

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
            if not uid or not isinstance(signature, dict):
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
            signature_obj = NodeSignature(
                source_path=str(signature["source_path"]),
                file_type_id=str(signature["file_type_id"]),
                section_key=str(signature["section_key"]),
                node_type=str(signature["node_type"]),
                parent_chain=str(signature["parent_chain"]),
                anchor_text=str(signature["anchor_text"]),
            )
            entry = {
                "uid": uid,
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

    @staticmethod
    def _new_uid() -> str:
        # Keep node IDs short and protocol-compatible: lowercase + dot + hex.
        return f"n.{uuid4().hex[:16]}"


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
