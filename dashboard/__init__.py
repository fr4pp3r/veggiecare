"""VeggieCare dashboard package — Flask app factory."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from flask import Flask

from config import config_file_path
from state import SystemState


def create_app(
    *,
    state: SystemState,
    config: dict[str, Any],
    controller: Any = None,
    db: Any = None,
    config_path: str | Path | None = None,
) -> Flask:
    """Build and configure the Flask application."""
    app = Flask(
        __name__,
        template_folder="templates",
        static_folder="static",
    )
    app.config["SECRET_KEY"] = "veggiecare-dev"

    # Store shared objects on the app for route access.
    app.veggiecare_state = state  # type: ignore[attr-defined]
    app.veggiecare_config = config  # type: ignore[attr-defined]
    app.veggiecare_controller = controller  # type: ignore[attr-defined]
    app.veggiecare_db = db  # type: ignore[attr-defined]

    # Config file the Settings page reads from and saves to. Defaults to
    # the same resolution used by load_config (CLI/env/default).
    app.veggiecare_config_path = str(  # type: ignore[attr-defined]
        config_path if config_path is not None else config_file_path()
    )
    # Set by POST /api/settings when a save happened since boot — the
    # frontend shows "pending restart" until the service restarts.
    app.veggiecare_config_pending = None  # type: ignore[attr-defined]

    from dashboard.routes import bp
    app.register_blueprint(bp)

    return app