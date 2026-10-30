"""Explicit failures at application/storage boundaries; pure functions stay simple."""
from dataclasses import dataclass
from typing import Generic, Literal, TypeVar

T = TypeVar("T")
ErrorCode = Literal["database_unavailable", "database_busy", "schema_mismatch",
                    "writer_active", "run_not_found", "invalid_input", "storage_failure"]


@dataclass(frozen=True)
class Failure:
    code: ErrorCode
    message: str
    retryable: bool = False


@dataclass(frozen=True)
class Ok(Generic[T]):
    value: T
    ok: Literal[True] = True


@dataclass(frozen=True)
class Err:
    error: Failure
    ok: Literal[False] = False


Result = Ok[T] | Err


class StorageFailure(RuntimeError):
    def __init__(self, error: Failure):
        self.error = error
        super().__init__(error.message)


def require(result: Result[T]) -> T:
    if isinstance(result, Err):
        raise StorageFailure(result.error)
    return result.value
