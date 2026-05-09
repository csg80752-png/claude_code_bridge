from __future__ import annotations

import errno
import json

import pytest

from ccbd.api_models import RpcRequest
from ccbd.socket_client_runtime import transport
from ccbd.socket_client_runtime.errors import CcbdClientError


class FakeSocket:
    def __init__(self, *, connect_errors=(), send_errors=(), recv_items=()) -> None:
        self.connect_errors = list(connect_errors)
        self.send_errors = list(send_errors)
        self.recv_items = list(recv_items)
        self.closed = False
        self.timeout = None

    def settimeout(self, value):
        self.timeout = value

    def connect(self, _path):
        if self.connect_errors:
            raise self.connect_errors.pop(0)

    def sendall(self, _payload):
        if self.send_errors:
            raise self.send_errors.pop(0)

    def recv(self, _size):
        item = self.recv_items.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item

    def close(self):
        self.closed = True


def _os_error(code: int) -> OSError:
    return OSError(code, errno.errorcode[code])


def _request() -> RpcRequest:
    return RpcRequest(op='ping', request={'target': 'ccbd'})


def test_connect_retries_eagain_once_then_succeeds(monkeypatch):
    sock = FakeSocket(connect_errors=[_os_error(errno.EAGAIN)])
    sleeps = []
    monkeypatch.setattr(transport.socket, 'socket', lambda *_args: sock)
    monkeypatch.setattr(transport.time, 'sleep', sleeps.append)

    result = transport.connect_socket('/tmp/ccbd.sock', timeout_s=1.0)

    assert result is sock
    assert sleeps == [0.05]
    assert not sock.closed


def test_connect_retries_eagain_until_final_failure(monkeypatch):
    sock = FakeSocket(connect_errors=[_os_error(errno.EAGAIN) for _ in range(5)])
    sleeps = []
    monkeypatch.setattr(transport.socket, 'socket', lambda *_args: sock)
    monkeypatch.setattr(transport.time, 'sleep', sleeps.append)

    with pytest.raises(CcbdClientError) as exc_info:
        transport.connect_socket('/tmp/ccbd.sock', timeout_s=1.0)

    assert exc_info.value.errno == errno.EAGAIN
    assert sleeps == [0.05, 0.075, 0.1125, 0.16875]
    assert sock.closed


def test_connect_econnrefused_raises_without_retry(monkeypatch):
    sock = FakeSocket(connect_errors=[_os_error(errno.ECONNREFUSED)])
    sleeps = []
    monkeypatch.setattr(transport.socket, 'socket', lambda *_args: sock)
    monkeypatch.setattr(transport.time, 'sleep', sleeps.append)

    with pytest.raises(CcbdClientError) as exc_info:
        transport.connect_socket('/tmp/ccbd.sock', timeout_s=1.0)

    assert exc_info.value.errno == errno.ECONNREFUSED
    assert sleeps == []
    assert sock.closed


def test_recv_retries_eintr_and_completes(monkeypatch):
    response = {'ok': True, 'payload': {'pong': True}, 'error': None}
    sock = FakeSocket(recv_items=[_os_error(errno.EINTR), json.dumps(response).encode('utf-8') + b'\n'])
    sleeps = []
    monkeypatch.setattr(transport.time, 'sleep', sleeps.append)

    assert transport.recv_response_line(sock) == json.dumps(response).encode('utf-8') + b'\n'
    assert sleeps == [0.05]


def test_send_happy_path_does_not_sleep(monkeypatch):
    sock = FakeSocket()
    sleeps = []
    monkeypatch.setattr(transport.time, 'sleep', sleeps.append)

    transport.send_request(sock, _request())

    assert sleeps == []
