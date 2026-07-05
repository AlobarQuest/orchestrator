class DomainError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        recovery: str | None,
        *,
        current_state: str | None = None,
        current_version: int | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.recovery = recovery
        self.current_state = current_state
        self.current_version = current_version
