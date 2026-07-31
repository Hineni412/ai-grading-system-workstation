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
