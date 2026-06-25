from enum import StrEnum


class Marketplace(StrEnum):
    WILDBERRIES = 'wildberries'
    OZON = 'ozon'
    YANDEX_MARKET = 'yandex_market'


class TokenType(StrEnum):
    BEARER = 'bearer'


class ModerationStatus(StrEnum):
    PENDING = 'pending'
    APPROVED = 'approved'
    REJECTED = 'rejected'
    AUTO_POSTED = 'auto_posted'
    SKIPPED = 'skipped'


class DealModerationStatus(StrEnum):
    PENDING = 'pending'
    APPROVED = 'approved'
    REJECTED = 'rejected'
    AUTO_POSTED = 'auto_posted'
    SKIPPED = 'skipped'


class DealDecisionReason(StrEnum):
    WARMUP_PARSER_MATCH = 'warmup_parser_match'
    DB_AND_PARSER_MATCH = 'db_and_parser_match'
    DB_AVERAGE_ONLY = 'db_average_only'
    ADMIN_APPROVED = 'admin_approved'
    ADMIN_REJECTED = 'admin_rejected'
    BELOW_THRESHOLDS = 'below_thresholds'
    DUPLICATE = 'duplicate'
    SENT_TO_ADMIN = 'sent_to_admin'
