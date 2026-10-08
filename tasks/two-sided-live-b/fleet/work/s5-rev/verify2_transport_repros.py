import os
import pathlib
import socket
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from types import SimpleNamespace
from unittest.mock import patch

SNAPSHOT = pathlib.Path(__file__).parent / 'ec9ebe90-snapshot-htst6swv'
sys.path[:0] = [str(SNAPSHOT / part) for part in ('src', 'scripts', 'tests')]
sys.path.insert(3, '/Users/dimabytes/work/polymarket/dota_2_bot/prediction-market-backtesting')
sys.dont_write_bytecode = True

original_connect = socket.socket.connect
original_connect_ex = socket.socket.connect_ex
blocked = []


def permitted(sock, address):
    return sock.family == socket.AF_UNIX or (
        isinstance(address, tuple) and address[0] in ('127.0.0.1', '::1', 'localhost')
    )


def local_connect(sock, address):
    if not permitted(sock, address):
        blocked.append(address)
        raise AssertionError('External connection forbidden by review harness')
    return original_connect(sock, address)


def local_connect_ex(sock, address):
    if not permitted(sock, address):
        blocked.append(address)
        raise AssertionError('External connection forbidden by review harness')
    return original_connect_ex(sock, address)


socket.socket.connect = local_connect
socket.socket.connect_ex = local_connect_ex
os.environ['NO_PROXY'] = os.environ['no_proxy'] = '127.0.0.1,localhost'

from trader import ctf_merge
from test_trader_ctf_merge import _cfg, CONDITION

assert pathlib.Path(ctf_merge.__file__).is_relative_to(SNAPSHOT)
print('Loaded pinned source:', ctf_merge.__file__)


def descriptor_repro():
    duplicated = []
    original_dup = os.dup

    def recording_dup(fd):
        duplicated_fd = original_dup(fd)
        duplicated.append(duplicated_fd)
        return duplicated_fd

    try:
        with patch.object(ctf_merge.os, 'dup', recording_dup):
            for _ in range(12):
                left, right = socket.socketpair()
                fp = left.makefile('rb')
                response = SimpleNamespace(
                    raw=SimpleNamespace(_original_response=SimpleNamespace(fp=fp)),
                    close=lambda: None,
                )
                try:
                    ctf_merge._shutdown_response(response)
                finally:
                    fp.close()
                    left.close()
                    right.close()
        still_open = []
        for fd in duplicated:
            try:
                os.fstat(fd)
            except OSError:
                pass
            else:
                still_open.append(fd)
        print('Deadline shutdowns: 12; orphaned duplicated descriptors:', len(still_open))
        assert len(still_open) == 12
    finally:
        for fd in duplicated:
            try:
                os.close(fd)
            except OSError:
                pass


class SlowHeaders(BaseHTTPRequestHandler):
    def do_GET(self):
        raw = b'{"nonce":"7"}'
        self.send_response(200)
        self.send_header('Content-Length', str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_POST(self):
        self.rfile.read(int(self.headers.get('Content-Length') or 0))
        self.wfile.write(b'HTTP/1.1 200 OK\r\nX-Delay: ')
        self.wfile.flush()
        for _ in range(24):
            self.wfile.write(b'.')
            self.wfile.flush()
            time.sleep(0.05)
        self.wfile.write(b'\r\nContent-Length: 2\r\n\r\n{}')
        self.wfile.flush()

    def log_message(self, *args):
        pass


def header_repro():
    server = HTTPServer(('127.0.0.1', 0), SlowHeaders)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    budget = 0.25
    started = time.monotonic()
    try:
        outcome = ctf_merge.merge_pairs_via_adapter(
            cfg=_cfg(builder_key='key', relayer_url=f'http://127.0.0.1:{server.server_port}'),
            condition_id=CONDITION,
            amount_raw=1,
            deadline_s=started + budget,
        )
        elapsed = time.monotonic() - started
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=1)
    print('Slow POST headers:', {'budget_s': budget, 'elapsed_s': round(elapsed, 3),
                                'status': outcome.status, 'reason': outcome.reason})
    assert elapsed > 1.0
    assert outcome.status == 'unknown'


def rpc_header_repro():
    server = HTTPServer(('127.0.0.1', 0), SlowHeaders)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    budget = 0.25
    started = time.monotonic()
    provider = ctf_merge._DeadlineHTTPProvider(
        f'http://127.0.0.1:{server.server_port}', started + budget
    )
    failure = None
    try:
        try:
            provider.make_request('eth_getTransactionReceipt', ['0x' + 'ab' * 32])
        except Exception as exc:
            failure = type(exc).__name__
        elapsed = time.monotonic() - started
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=1)
    print('Slow RPC headers:', {'budget_s': budget, 'elapsed_s': round(elapsed, 3),
                               'exception': failure})
    assert elapsed > 1.0
    assert failure is not None


if __name__ == '__main__':
    descriptor_repro()
    header_repro()
    rpc_header_repro()
    print('Forbidden external connections attempted:', len(blocked))
    assert not blocked
