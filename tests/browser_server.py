"""Loopback-only browser test host with disposable data and no live providers."""

import argparse
import os
import tempfile
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=18764)
    args = parser.parse_args()
    for key in list(os.environ):
        if key.startswith(("SCHOLARTRACE_", "OPENAI_", "ANTHROPIC_", "OLLAMA_", "DOCUMIND_")):
            os.environ.pop(key)
    with tempfile.TemporaryDirectory(prefix="scholartrace-browser-") as temporary:
        os.environ["SCHOLARTRACE_DATA_DIR"] = str(Path(temporary) / "data")
        os.environ["SCHOLARTRACE_PAID_ROUTES_ENABLED"] = "false"
        import uvicorn
        from fastapi.staticfiles import StaticFiles

        from scholartrace.api.app import app
        from scholartrace.delivery.models import TaskCreateRequest

        root = Path(__file__).resolve().parents[1]
        for i in range(14):
            app.state.m6_service.create_task(
                TaskCreateRequest(question=f"Synthetic pagination scenario {i + 1}"),
                idempotency_key=f"browser-seed-{i}",
            )
        app.mount("/", StaticFiles(directory=root / "frontend/dist", html=True))
        try:
            uvicorn.run(app, host="127.0.0.1", port=args.port, access_log=False)
        finally:
            app.state.m6_service.close()


if __name__ == "__main__":
    main()
