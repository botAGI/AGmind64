"""Backward-compatible credentials API shim."""

from agmind.core.secrets import generate_secret, get_creds_path, mask_value, read_creds, write_creds

__all__ = ["generate_secret", "get_creds_path", "mask_value", "read_creds", "write_creds"]
