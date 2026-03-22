from __future__ import annotations

"""
Central version contracts for iwp_lint/iwp_build artifacts.

Keep protocol and artifact version constants here so writer/verifier/config
share one source of truth during upgrades.
"""

IWP_PROTOCOL_MAJOR_VERSION = 1
IWP_PROTOCOL_VERSION = "1.0"

IWC_JSON_FORMAT_VERSION = 1
IWC_MD_META_VERSION = 1

NODE_REGISTRY_FORMAT_VERSION = 1
NODE_CATALOG_FORMAT_VERSION = 1

SUPPORTED_IWC_JSON_VERSIONS: tuple[int, ...] = (IWC_JSON_FORMAT_VERSION,)

DEFAULT_SCHEMA_SOURCE = f"builtin:iwp-schema.v{IWP_PROTOCOL_MAJOR_VERSION}"
DEFAULT_NODE_REGISTRY_FILE = f".iwp/node_registry.v{NODE_REGISTRY_FORMAT_VERSION}.json"
DEFAULT_NODE_CATALOG_FILE = f".iwp/node_catalog.v{NODE_CATALOG_FORMAT_VERSION}.json"
DEFAULT_NODE_INDEX_DB_FILE = f".iwp/cache/node_index.v{NODE_CATALOG_FORMAT_VERSION}.sqlite"
