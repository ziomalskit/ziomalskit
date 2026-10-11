"""Compatibility imports for existing v20 tools; production uses adapters."""
from .workflow_graph import all_nodes, instruction_snapshot, set_widget, remove_passthrough
from .workflow_adapters import get_adapter, compose

_descriptor = get_adapter("h3_v20").descriptor
BASELINE_FILE = _descriptor["baseline"]
BASELINE_BYTES = _descriptor["baseline_size_bytes"]
BASELINE_SHA256 = _descriptor["baseline_sha256"]


def verified_baseline(path):
    return get_adapter("h3_v20").verify_baseline(path)
