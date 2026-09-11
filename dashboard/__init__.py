"""VeggieCare dashboard package — Flask app factory."""

from __future__ import annotations

from typing import Any

from flask import Flask

from state import SystemState


def create_app(
    *,
    state: SystemState,
    config: dict[str, Any],
    controller: Any = None,
    db: Any = None,
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

    from dashboard.routes import bp
    app.register_blueprint(bp)

    return app