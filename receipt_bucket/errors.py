"""Exception types used across the receipt bucket."""


class ToolError(Exception):
    """A tool invocation failed for a reason the agent can fix."""


class ValidationError(ValueError, ToolError):
    """Bad input from the caller (agent or user)."""


class StorageError(ToolError, OSError):
    """Something went wrong reading or writing the bucket."""


class PathSafetyError(StorageError):
    """A storage path refused to stay inside the data directory."""


class CorruptBucketError(StorageError):
    """The bucket file exists but is not valid JSON in the expected shape."""


class ReceiptNotFoundError(ToolError, KeyError):
    """No receipt with the requested id exists in this bucket."""
