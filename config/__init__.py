"""VeggieCare configuration package.

Provides loading, deep-merging over defaults and strict validation of the
YAML configuration file (config/config.yaml). Configuration is applied only
after validation succeeds.
"""

from config.config import (
    ConfigError,
    DEFAULT_CONFIG_PATH,
    PROJECT_ROOT,
    deep_merge,
    expand_paths,
    load_config,
    validate,
)

__all__ = [
    "ConfigError",
    "DEFAULT_CONFIG_PATH",
    "PROJECT_ROOT",
    "deep_merge",
    "expand_paths",
    "load_config",
    "validate",
]