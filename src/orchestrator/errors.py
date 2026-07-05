class DomainError(Exception):
    def __init__(self, code: str, message: str, recovery: str | None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.recovery = recovery
