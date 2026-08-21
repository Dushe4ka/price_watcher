import unittest

from src.core.config import parse_source_chain
from src.marketplaces.contracts import SourceName


class SourceChainTests(unittest.TestCase):
    def test_empty_chain_uses_the_provided_default(self) -> None:
        default = (SourceName.BROWSER, SourceName.APIFY)

        result = parse_source_chain('   ', default)

        self.assertEqual(default, result)

    def test_unknown_source_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, 'not-a-source'):
            parse_source_chain('browser,not-a-source', ())

    def test_duplicate_source_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, 'duplicates'):
            parse_source_chain('browser,apify,browser', ())

    def test_source_chain_strips_individual_source_names(self) -> None:
        result = parse_source_chain(
            ' public, browser , apify ',
            (),
        )

        self.assertEqual(
            (SourceName.PUBLIC, SourceName.BROWSER, SourceName.APIFY),
            result,
        )
