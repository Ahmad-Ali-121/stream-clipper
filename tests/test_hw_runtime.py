import sys

import pytest

from clipper import hw

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="the C++ runtime check is for Windows")


def test_old_or_missing_runtime_is_named_with_the_fix():
    old = hw.runtime_problem((14, 36, 32532, 0), check=False)
    assert "14.36.32532.0" in old and "too old" in old and hw.VC_DOWNLOAD in old
    assert "missing" in hw.runtime_problem(None, check=False)


def test_current_runtime_is_fine():
    assert hw.runtime_problem((14, 40, 0, 0), check=False) == ""
    assert hw.runtime_problem((14, 50, 35719, 0), check=False) == ""
