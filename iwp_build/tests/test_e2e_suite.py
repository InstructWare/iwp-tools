from __future__ import annotations

import unittest

from iwp_build.tests.e2e.test_bootstrap_no_baseline_no_links import (
    BootstrapNoBaselineNoLinksBuildE2E,
)
from iwp_build.tests.e2e.test_bootstrap_official_schema import BootstrapOfficialSchemaBuildE2E
from iwp_build.tests.e2e.test_feature_add_node import FeatureAddNodeBuildE2E
from iwp_build.tests.e2e.test_feature_delete_node import FeatureDeleteNodeBuildE2E
from iwp_build.tests.e2e.test_feature_modify_node import FeatureModifyNodeBuildE2E

# Re-export test classes so unittest can load from this single module entrypoint.
__all__ = [
    "FeatureAddNodeBuildE2E",
    "FeatureDeleteNodeBuildE2E",
    "FeatureModifyNodeBuildE2E",
    "BootstrapNoBaselineNoLinksBuildE2E",
    "BootstrapOfficialSchemaBuildE2E",
]


if __name__ == "__main__":
    unittest.main()
