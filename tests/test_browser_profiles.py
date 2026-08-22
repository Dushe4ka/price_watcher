from __future__ import annotations

import multiprocessing
import stat
import tempfile
import unittest
from pathlib import Path

from src.browser.profiles import (
    BrowserProcessIsolationError,
    ProfileInUseError,
    ProfileLock,
    validate_single_browser_worker,
)


def _hold_profile_lock(
    profile_dir: str,
    ready: multiprocessing.Queue,
    release: multiprocessing.Event,
) -> None:
    lock = ProfileLock(Path(profile_dir))
    try:
        lock.acquire()
        ready.put('ready')
        release.wait(timeout=10)
    finally:
        lock.release()


class ProfileLockTests(unittest.TestCase):
    def test_profile_directory_is_private(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            profile_dir = Path(temporary_directory, 'api', 'ozon')
            lock = ProfileLock(profile_dir)
            lock.acquire()
            try:
                mode = stat.S_IMODE(profile_dir.stat().st_mode)
            finally:
                lock.release()

        self.assertEqual(0o700, mode)

    def test_second_process_cannot_open_the_same_profile(self) -> None:
        context = multiprocessing.get_context('spawn')
        ready = context.Queue()
        release = context.Event()
        with tempfile.TemporaryDirectory() as temporary_directory:
            profile_dir = Path(temporary_directory, 'bot', 'wildberries')
            process = context.Process(
                target=_hold_profile_lock,
                args=(str(profile_dir), ready, release),
            )
            process.start()
            try:
                self.assertEqual('ready', ready.get(timeout=5))
                competing_lock = ProfileLock(profile_dir)
                with self.assertRaises(ProfileInUseError):
                    competing_lock.acquire()
            finally:
                release.set()
                process.join(timeout=5)
                if process.is_alive():
                    process.terminate()
                    process.join(timeout=5)

        self.assertEqual(0, process.exitcode)

    def test_web_concurrency_must_be_exactly_one(self) -> None:
        validate_single_browser_worker({'WEB_CONCURRENCY': '1'})

        for value in (None, '0', '2', 'invalid'):
            with self.subTest(value=value):
                environment = (
                    {}
                    if value is None
                    else {'WEB_CONCURRENCY': value}
                )
                with self.assertRaises(BrowserProcessIsolationError):
                    validate_single_browser_worker(environment)

    def test_environment_mapping_is_read_without_mutation(self) -> None:
        environment = {'WEB_CONCURRENCY': '1'}

        validate_single_browser_worker(environment)

        self.assertEqual({'WEB_CONCURRENCY': '1'}, environment)


if __name__ == '__main__':
    unittest.main()
