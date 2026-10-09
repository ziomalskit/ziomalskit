"""Runtime secrets must never enter durable diagnostics or error text."""
import json
import os

SECRET_NAMES = ("CONTAINER_API_KEY", "VAST_API_KEY", "HF_TOKEN", "HF_HUB_TOKEN", "HUGGING_FACE_HUB_TOKEN", "H3_PANEL_PASSWORD")


def redact(value):
    if isinstance(value, str):
        secrets = {variant for name in SECRET_NAMES if os.environ.get(name)
                   for variant in (os.environ[name], os.environ[name].strip()) if variant}
        for secret in sorted(secrets, key=len, reverse=True):
            for representation in (secret, json.dumps(secret)[1:-1], repr(secret)[1:-1]):
                value = value.replace(representation, "[REDACTED]")
        return value
    if isinstance(value, dict):
        return {redact(key): redact(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [redact(item) for item in value]
    return value
