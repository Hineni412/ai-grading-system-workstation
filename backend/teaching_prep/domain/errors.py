class TeachingPrepError(RuntimeError):
    """Base class for safe, expected teaching-prep failures."""


class TeachingPrepNotFoundError(TeachingPrepError):
    pass


class TeachingPrepConflictError(TeachingPrepError):
    pass


class TeachingPrepStateError(TeachingPrepError):
    pass


class TeachingPrepRetryAvailableError(TeachingPrepError):
    """The previous operation ended cleanly enough for a teacher retry."""


class TeachingPrepValidationError(TeachingPrepError):
    pass


class TeachingPrepModelResponseError(TeachingPrepValidationError):
    """A safe, classified failure while reading a model response."""

    def __init__(self, message: str, *, error_code: str) -> None:
        super().__init__(message)
        self.error_code = str(error_code)
