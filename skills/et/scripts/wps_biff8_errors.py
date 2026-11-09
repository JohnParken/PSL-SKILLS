"""Stable BIFF8 refusal categories and structural assertions."""

class EncryptedError(ValueError):
    """An encrypted stream was identified; no decryption attempted."""
    status = "encrypted"

class ResourceLimitError(ValueError):
    """A bounded resource policy prevented extraction."""
    status = "resource_limit"

class UnsupportedError(ValueError):
    """A recognized record feature is outside the implemented subset."""
    status = "unsupported"

def require(condition,message):
    if not condition:
        raise ValueError(message)
