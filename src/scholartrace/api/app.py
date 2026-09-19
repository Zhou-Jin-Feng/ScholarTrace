"""Default Demo ASGI entry point; production uses an explicit factory."""

from scholartrace.api.factory import create_app as create_app

app = create_app()
