import os
from pathlib import Path
import subprocess
import sys
import tempfile
from unittest import TestCase

from pydantic import SecretStr, ValidationError

from src.core.config import Settings
from src.marketplaces.contracts import SourceName


def make_settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        'db_dialect': 'postgresql',
        'db_driver': 'asyncpg',
        'secret': 'application-secret',
        'first_superuser_email': 'admin@example.invalid',
        'first_superuser_password': 'superuser-password',
        'postgres_user': 'postgres-user',
        'postgres_password': 'postgres-password',
        'postgres_db': 'price-watcher',
        'postgres_port': '5432',
        'postgres_host': 'localhost',
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


class MarketplaceSettingsTests(TestCase):
    def test_default_chains_match_approved_topology(self) -> None:
        settings = make_settings()

        self.assertEqual(
            (SourceName.BROWSER, SourceName.APIFY),
            settings.source_chain('wildberries'),
        )
        self.assertEqual(
            (SourceName.BROWSER, SourceName.APIFY),
            settings.source_chain('ozon'),
        )
        self.assertEqual(
            (SourceName.PUBLIC, SourceName.BROWSER, SourceName.APIFY),
            settings.source_chain('yandex_market'),
        )

    def test_profile_paths_are_role_and_marketplace_isolated(self) -> None:
        settings = make_settings(browser_profile_root='/profiles')

        self.assertEqual(
            Path('/profiles/bot/ozon'),
            settings.profile_dir('bot', 'ozon'),
        )
        self.assertNotEqual(
            settings.profile_dir('bot', 'ozon'),
            settings.profile_dir('api', 'ozon'),
        )

    def test_profile_dir_rejects_path_traversal(self) -> None:
        settings = make_settings(browser_profile_root='/profiles')

        with self.assertRaisesRegex(ValueError, 'role'):
            settings.profile_dir('../outside', 'ozon')
        with self.assertRaisesRegex(ValueError, 'marketplace'):
            settings.profile_dir('bot', '../outside')

    def test_invalid_marketplace_source_chain_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValidationError, 'not-a-source'):
            make_settings(ozon_source_chain='browser,not-a-source')

    def test_duplicate_marketplace_source_chain_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValidationError, 'duplicates'):
            make_settings(wildberries_source_chain='browser,browser')

    def test_runtime_and_captcha_modes_are_limited_to_supported_values(
        self,
    ) -> None:
        with self.assertRaises(ValidationError):
            make_settings(runtime_role='scheduler')
        with self.assertRaises(ValidationError):
            make_settings(captcha_adapter_mode='another-provider')
        with self.assertRaises(ValidationError):
            make_settings(smartcaptcha_mode='interactive')

    def test_captcha_adapter_mode_uses_the_documented_field_name(self) -> None:
        settings = make_settings(captcha_adapter_mode='ohmycaptcha')

        self.assertEqual('ohmycaptcha', settings.captcha_adapter_mode)

        with self.assertRaises(ValidationError):
            make_settings(captcha_adapter='ohmycaptcha')

    def test_retry_budget_cannot_exceed_two_transport_attempts(self) -> None:
        with self.assertRaises(ValidationError):
            make_settings(marketplace_retry_max_attempts=3)

    def test_total_timeout_and_content_limit_respect_controller_bounds(
        self,
    ) -> None:
        settings = make_settings(
            marketplace_total_timeout_sec=300,
            marketplace_max_content_bytes=10_485_760,
        )

        self.assertEqual(300, settings.marketplace_total_timeout_sec)
        self.assertEqual(10_485_760, settings.marketplace_max_content_bytes)

        for field, value in (
            ('marketplace_total_timeout_sec', 0),
            ('marketplace_total_timeout_sec', 301),
            ('marketplace_max_content_bytes', 0),
            ('marketplace_max_content_bytes', 10_485_761),
        ):
            with self.subTest(field=field, value=value):
                with self.assertRaises(ValidationError):
                    make_settings(**{field: value})

    def test_operation_timeout_is_independent_of_the_per_source_timeout(
        self,
    ) -> None:
        settings = make_settings(
            marketplace_total_timeout_sec=30,
            marketplace_operation_timeout_sec=900,
        )

        self.assertEqual(30, settings.marketplace_total_timeout_sec)
        self.assertEqual(900, settings.marketplace_operation_timeout_sec)

        for field, value in (
            ('marketplace_operation_timeout_sec', 0),
            ('marketplace_operation_timeout_sec', 901),
        ):
            with self.subTest(field=field, value=value):
                with self.assertRaises(ValidationError):
                    make_settings(**{field: value})

    def test_secret_values_are_redacted_in_settings_and_validation_errors(
        self,
    ) -> None:
        sentinel = 'apify-token-sentinel'
        settings = make_settings(apify_api_token=sentinel)

        self.assertIsInstance(settings.apify_api_token, SecretStr)
        self.assertNotIn(sentinel, repr(settings))

        with self.assertRaises(ValidationError) as context:
            make_settings(apify_api_token=sentinel, runtime_role='invalid')

        self.assertNotIn(sentinel, repr(context.exception))

    def test_legacy_secrets_and_proxy_are_redacted_and_remain_strings(
        self,
    ) -> None:
        sentinels = {
            'secret': 'application-secret-sentinel',
            'first_superuser_password': 'superuser-password-sentinel',
            'postgres_password': 'postgres-password-sentinel',
            'telegram_bot_token': 'telegram-token-sentinel',
            'proxy_list': 'proxy-url-sentinel',
        }
        settings = make_settings(**sentinels)

        for sentinel in sentinels.values():
            self.assertNotIn(sentinel, repr(settings))
        self.assertEqual(sentinels['secret'], settings.secret)
        self.assertEqual(sentinels['proxy_list'], settings.proxy_list)

        with self.assertRaises(ValidationError) as context:
            make_settings(
                **sentinels,
                marketplace_retry_max_attempts=3,
            )

        for sentinel in sentinels.values():
            self.assertNotIn(sentinel, repr(context.exception))

    def test_direct_import_loads_only_a_local_synthetic_dotenv_file(
        self,
    ) -> None:
        dotenv = '\n'.join(
            (
                'DB_DIALECT=postgresql',
                'DB_DRIVER=asyncpg',
                'SECRET=dotenv-secret',
                'FIRST_SUPERUSER_EMAIL=dotenv@example.invalid',
                'FIRST_SUPERUSER_PASSWORD=dotenv-password',
                'POSTGRES_USER=dotenv-user',
                'POSTGRES_PASSWORD=dotenv-password',
                'POSTGRES_DB=dotenv-db',
                'POSTGRES_PORT=5432',
                'POSTGRES_HOST=dotenv-host',
            ),
        )
        required_names = (
            'DB_DIALECT',
            'DB_DRIVER',
            'SECRET',
            'FIRST_SUPERUSER_EMAIL',
            'FIRST_SUPERUSER_PASSWORD',
            'POSTGRES_USER',
            'POSTGRES_PASSWORD',
            'POSTGRES_DB',
            'POSTGRES_PORT',
            'POSTGRES_HOST',
        )
        environment = os.environ.copy()
        for name in required_names:
            environment.pop(name, None)
        environment.pop('PYTHON_DOTENV_DISABLED', None)
        repository_root = Path(__file__).resolve().parents[1]
        environment['PYTHONPATH'] = str(repository_root)

        with tempfile.TemporaryDirectory() as temporary_directory:
            dotenv_path = Path(temporary_directory, '.env')
            dotenv_path.write_text(dotenv, encoding='utf-8')
            result = subprocess.run(
                (
                    sys.executable,
                    '-c',
                    'from src.core.config import settings; '
                    "assert settings.postgres_host == 'dotenv-host'",
                ),
                capture_output=True,
                check=False,
                cwd=temporary_directory,
                env=environment,
                text=True,
            )

        self.assertEqual(0, result.returncode, result.stderr)
