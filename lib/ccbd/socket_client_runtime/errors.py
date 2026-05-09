from __future__ import annotations


class CcbdClientError(RuntimeError):
    def __init__(self, message: str | BaseException, *, errno: int | None = None) -> None:
        super().__init__(str(message))
        self.errno = errno if errno is not None else getattr(message, 'errno', None)


__all__ = ['CcbdClientError']
