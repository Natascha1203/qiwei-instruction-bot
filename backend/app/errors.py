class AppError(Exception):
    """Base application error."""


class ParseError(AppError):
    """Input content parsing failed."""


class ValidationError(AppError):
    """Extracted field validation failed."""


class ContractNotFoundError(AppError):
    """Contract symbol is invalid or not found."""


class NotifyError(AppError):
    """Webhook callback failed."""

