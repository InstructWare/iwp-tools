from __future__ import annotations

import unittest

from iwp_lint.tests.e2e.test_code_only_and_compiled import CodeOnlyAndCompiledLintE2E
from iwp_lint.tests.e2e.test_deleted_node import DeletedNodeLintE2E
from iwp_lint.tests.e2e.test_i18n_and_schema import I18nAndSchemaLintE2E

__all__ = [
    "CodeOnlyAndCompiledLintE2E",
    "I18nAndSchemaLintE2E",
    "DeletedNodeLintE2E",
]


if __name__ == "__main__":
    unittest.main()
