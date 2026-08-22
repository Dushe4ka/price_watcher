from __future__ import annotations

import os
import unittest


@unittest.skipUnless(
    os.environ.get('LIVE_MARKETPLACE_TESTS') == '1',
    'set LIVE_MARKETPLACE_TESTS=1 to enable live marketplace probes',
)
class SmartCaptchaLiveTests(unittest.TestCase):
    def test_live_probe_requires_an_approved_page_harness(self) -> None:
        self.skipTest(
            'no approved live SmartCaptcha target is configured for this task'
        )


if __name__ == '__main__':
    unittest.main()
