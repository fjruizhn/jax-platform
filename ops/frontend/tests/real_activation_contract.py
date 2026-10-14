"""Root activation-receipt canary, invoked explicitly after the private build.

This file intentionally lives outside the `test_*.py` discovery contract: it
requires a real user manager, passwordless test sudo and P100_TEST_RELEASE_DIR.
"""
from __future__ import annotations
import os
import subprocess
import sys
import unittest

if not os.environ.get('P100_TEST_RELEASE_DIR'):
    raise SystemExit('P100_TEST_RELEASE_DIR is required for the activation receipt canary')
if subprocess.run(['sudo','-n','true']).returncode:
    raise SystemExit('passwordless test sudo is required for the activation receipt canary')

from test_origin_contract import _PrivateActivationReceiptMoved


class PrivateActivationReceipt(_PrivateActivationReceiptMoved,unittest.TestCase):
    pass


if __name__=='__main__': unittest.main(verbosity=2)
