"""Exceptions for the Obsidian Local REST API connector."""

class ObsidianConnectorError(Exception):
    """Base exception for all Obsidian connector errors."""
    pass

class ObsidianTimeoutError(ObsidianConnectorError):
    """Raised when a request to Obsidian times out."""
    pass

class ObsidianUnauthorizedError(ObsidianConnectorError):
    """Raised when Obsidian rejects authentication (401/403)."""
    pass

class ObsidianConfigurationError(ObsidianConnectorError):
    """Raised when Obsidian env vars are not properly configured."""
    pass
