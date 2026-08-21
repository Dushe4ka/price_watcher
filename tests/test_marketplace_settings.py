from pathlib import Path
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
            make_settings(captcha_adapter='another-provider')
        with self.assertRaises(ValidationError):
            make_settings(smartcaptcha_mode='interactive')

    def test_retry_budget_cannot_exceed_two_transport_attempts(self) -> None:
        with self.assertRaises(ValidationError):
            make_settings(marketplace_retry_max_attempts=3)

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
