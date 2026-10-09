"""Load the same pure temporal contract shipped with AJ's ComfyUI nodes."""
import importlib.util
from pathlib import Path

_source = Path(__file__).resolve().parents[1] / "custom_nodes/aj_production/temporal.py"
_spec = importlib.util.spec_from_file_location("_aj_temporal_contract", _source)
_contract = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_contract)

FRAME_EXPRESSION = _contract.FRAME_EXPRESSION
LENGTH_EXPRESSION = _contract.LENGTH_EXPRESSION
SECTIONS = _contract.SECTIONS
duration_identity = _contract.duration_identity
validate_duration = _contract.validate_duration
milliseconds = _contract.milliseconds
timecode = _contract.timecode
canonical_plan = _contract.canonical_plan
normalize_plan = _contract.normalize_plan
final_answer = _contract.final_answer
final_timeline = _contract.final_timeline
