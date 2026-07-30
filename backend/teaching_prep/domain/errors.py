class TeachingPrepError(RuntimeError):
    """Base class for safe, expected teaching-prep failures."""


class TeachingPrepNotFoundError(TeachingPrepError):
    pass


class TeachingPrepConflictError(TeachingPrepError):
    pass


class TeachingPrepStateError(TeachingPrepError):
    pass


class TeachingPrepValidationError(TeachingPrepError):
    pass
