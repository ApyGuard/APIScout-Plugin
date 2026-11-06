from __future__ import annotations


class ApyGuardMcpError(RuntimeError):
    """Base error for actionable APIScout MCP failures."""


class InvalidProjectPathError(ApyGuardMcpError):
    """Raised when a project path is missing or is not a directory."""


class PathOutsideProjectError(ApyGuardMcpError):
    """Raised when a project-scoped path escapes the project root."""


class InvalidOpenApiDocumentError(ApyGuardMcpError):
    """Raised when an OpenAPI input cannot be parsed as an object."""


class EndpointNotFoundError(ApyGuardMcpError):
    """Raised when an endpoint is absent from a scan."""


class UnsupportedOutputFormatError(ApyGuardMcpError):
    """Raised when an export format is not JSON or YAML."""


class AuthenticationRequiredError(ApyGuardMcpError):
    """Raised when an APIScout operation requires an authenticated account."""


class AuthenticationPendingError(ApyGuardMcpError):
    """Raised when the user has not approved the sign-in code in the browser yet."""


class AuthenticationStorageError(ApyGuardMcpError):
    """Raised when local authentication state is missing, invalid, or insecure."""


class AuthenticationServiceError(ApyGuardMcpError):
    """Raised when ApyGuard cannot start or finish the sign-in."""


class AssetCreationServiceError(ApyGuardMcpError):
    """Raised when an ApyGuard asset cannot be created from scan results."""


class AssetCreationRefusedError(AssetCreationServiceError):
    """Raised when ApyGuard refuses the asset request (4xx): sent again, it is refused the same way."""


class AssetImportRunningError(AssetCreationRefusedError):
    """Raised when ApyGuard refuses the asset request while another import of the asset runs."""


class AssetListingServiceError(ApyGuardMcpError):
    """Raised when ApyGuard assets or API collections cannot be listed."""


class AssetNotFoundError(AssetListingServiceError):
    """Raised when ApyGuard answers 404: asked again, it answers the same way until the asset exists."""
