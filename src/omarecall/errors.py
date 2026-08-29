"""Domain errors exposed by the OmaRecall CLI."""


class OmaRecallError(Exception):
    """Base class for expected OmaRecall failures."""

    code = "omarecall_error"


class InvalidDataPathError(OmaRecallError):
    """Raised when a storage path is unsafe or invalid."""

    code = "invalid_data_path"


class InvalidSessionError(OmaRecallError):
    """Raised when session input does not satisfy the storage contract."""

    code = "invalid_session"


class SessionExistsError(OmaRecallError):
    """Raised when a generated or supplied session ID already exists."""

    code = "session_exists"


class SessionNotFoundError(OmaRecallError):
    """Raised when a requested session cannot be found."""

    code = "session_not_found"


class StoreCorruptError(OmaRecallError):
    """Raised when persisted JSON cannot be decoded or validated."""

    code = "store_corrupt"


class InvalidContextRequestError(OmaRecallError):
    """Raised when a context mode is missing required selectors."""

    code = "invalid_context_request"


class UnsupportedAgentError(OmaRecallError):
    """Raised when an agent or its executable cannot be launched safely."""

    code = "unsupported_agent"
