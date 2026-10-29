class LocalWorkerError(Exception):
    """Base class for expected local worker failures."""


class ConfigError(LocalWorkerError):
    """Raised when settings are missing or invalid."""


class ScrapeError(LocalWorkerError):
    """Raised when a source cannot be fetched or parsed."""


class ContentError(LocalWorkerError):
    """Raised when content normalization or validation fails."""


class ModelValidationError(LocalWorkerError):
    """Raised when a data contract is violated."""
