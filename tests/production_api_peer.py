"""Run the actual ASGI application over a loopback socket for integration tests."""

import socket
import threading
import time
from contextlib import contextmanager

import httpx
import uvicorn


@contextmanager
def production_api_peer(app):
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        port = listener.getsockname()[1]
        server = uvicorn.Server(uvicorn.Config(app, access_log=False, log_level='error'))
        worker = threading.Thread(target=server.run, kwargs={'sockets': [listener]}, daemon=True)
        worker.start()
        try:
            deadline = time.monotonic() + 10
            while not server.started:
                if not worker.is_alive() or time.monotonic() > deadline:
                    raise RuntimeError('loopback API failed to start')
                time.sleep(0.01)
            with httpx.Client(base_url=f'http://127.0.0.1:{port}', trust_env=False,
                              timeout=20) as client:
                yield client
        finally:
            server.should_exit = True
            worker.join(timeout=15)
            if worker.is_alive():
                raise RuntimeError('loopback API did not stop')
