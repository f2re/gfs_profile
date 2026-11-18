from __future__ import annotations


class MessengerError(RuntimeError):
    pass


class MessengerInputError(MessengerError):
    pass


class LocationNotFoundError(MessengerInputError):
    pass


class AmbiguousLocationError(MessengerInputError):
    pass


class RunUnavailableError(MessengerError):
    pass


class ProductUnavailableError(MessengerError):
    pass


class ProductExecutionError(MessengerError):
    pass


class CancelledError(MessengerError):
    pass


class PlatformError(MessengerError):
    pass


class PlatformAuthError(PlatformError):
    pass


class PlatformRateLimitError(PlatformError):
    pass


class PlatformTemporaryError(PlatformError):
    pass


class PlatformPermanentError(PlatformError):
    pass
