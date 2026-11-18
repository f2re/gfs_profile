"""Explicit module-scoped opt-in for dormant WN3 regression tests, not production."""
import os
import unittest
from unittest.mock import patch


def enable_for_module():
    switch = patch.dict(os.environ, {'WEATHERNEXT3_ENABLED': '1'})
    switch.start()
    unittest.addModuleCleanup(switch.stop)
