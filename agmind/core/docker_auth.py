"""Docker CLI auth helpers shared by install and deploy paths."""

from __future__ import annotations

import os


def user_docker_config_dir() -> str | None:
    """Return the invoking user's Docker config dir, if it exists.

    When AGmind runs Docker through sudo, plain `sudo docker` would use root's
    empty `/root/.docker` and pull anonymously. Passing `DOCKER_CONFIG` preserves
    the operator's `docker login` credentials.
    """
    candidate = os.environ.get("DOCKER_CONFIG") or os.path.join(os.path.expanduser("~"), ".docker")
    return candidate if os.path.isdir(candidate) else None
