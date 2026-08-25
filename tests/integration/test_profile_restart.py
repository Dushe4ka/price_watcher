"""A restarted session manager keeps its persistent profile state.

Task 8 promised that closing and re-creating ``BrowserSessionManager`` keeps
cookies and ``localStorage`` for the marketplace origin. This proves it with
a real Chromium persistent profile in a temporary directory.
"""

from __future__ import annotations

import unittest

from src.browser.profiles import ProfileInUseError, ProfileLock
from tests.integration.fixture_server import fixture_server
from tests.integration.harness import (
    RealBrowserTestCase,
    read_profile_state,
    seed_profile_state,
)


class ProfileRestartTests(RealBrowserTestCase):
    async def test_cookies_and_local_storage_survive_a_restart(self) -> None:
        profile_dir = self.profile_dir()

        async with fixture_server('clean') as server:
            await seed_profile_state(
                server,
                profile_dir=profile_dir,
                cookie='controlled_session=task15',
                storage=('controlled_key', 'task15'),
            )
            state = await read_profile_state(
                server,
                profile_dir=profile_dir,
                storage_key='controlled_key',
            )

        self.assertIn('controlled_session=task15', state['cookie'])
        self.assertEqual('task15', state['storage'])

    async def test_a_second_opener_cannot_take_the_same_profile(self) -> None:
        profile_dir = self.profile_dir()

        with ProfileLock(profile_dir):
            with self.assertRaises(ProfileInUseError):
                ProfileLock(profile_dir).acquire()

        # Releasing the first lock hands the profile back cleanly.
        second = ProfileLock(profile_dir)
        second.acquire()
        second.release()


if __name__ == '__main__':
    unittest.main()
