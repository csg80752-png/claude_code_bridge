from __future__ import annotations

from pathlib import Path
import errno
import json
import socket
import time

from ccbd.api_models import RpcRequest, RpcResponse

from .errors import CcbdClientError


_TRANSIENT_ERRNOS = {errno.EAGAIN, errno.EWOULDBLOCK, errno.EINTR}
_RETRY_DELAYS_S = (0.05, 0.075, 0.1125, 0.16875)


def connect_socket(socket_path: Path, *, timeout_s: float):
    if not hasattr(socket, 'AF_UNIX'):
        raise CcbdClientError('unix domain sockets are not supported on this platform')
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(timeout_s)
    try:
        _retry_transient(lambda: sock.connect(str(socket_path)))
    except CcbdClientError:
        sock.close()
        raise
    return sock


def send_request(sock, request: RpcRequest) -> None:
    payload = json.dumps(request.to_record(), ensure_ascii=False) + '\n'
    _retry_transient(lambda: sock.sendall(payload.encode('utf-8')))


def recv_response_line(sock) -> bytes:
    raw = b''
    while b'\n' not in raw:
        chunk = _retry_transient(lambda: sock.recv(65536))
        if not chunk:
            break
        raw += chunk
    return raw


def decode_response(raw: bytes) -> RpcResponse:
    line = raw.split(b'\n', 1)[0].decode('utf-8')
    return RpcResponse.from_record(json.loads(line))


def _retry_transient(fn):
    for attempt in range(len(_RETRY_DELAYS_S) + 1):
        try:
            return fn()
        except OSError as exc:
            if exc.errno not in _TRANSIENT_ERRNOS or attempt == len(_RETRY_DELAYS_S):
                raise CcbdClientError(exc, errno=exc.errno) from exc
            time.sleep(_RETRY_DELAYS_S[attempt])
    raise RuntimeError('unreachable')


__all__ = ['connect_socket', 'decode_response', 'recv_response_line', 'send_request']
