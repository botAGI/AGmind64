"""Optional tool candidate catalog for homelab and enterprise profiles."""

from agmind.addons.candidates import (
    DEFAULT_TOOL_CANDIDATES_DIR,
    AdmissionContract,
    CandidateAdmission,
    CandidateCategory,
    CandidateDependencies,
    CandidateRuntime,
    CandidateScope,
    CandidateStatus,
    CandidateVerification,
    ToolCandidate,
    load_tool_candidates,
)
from agmind.addons.checks import validate_tool_candidates

__all__ = [
    "AdmissionContract",
    "CandidateAdmission",
    "CandidateCategory",
    "CandidateDependencies",
    "CandidateRuntime",
    "CandidateScope",
    "CandidateStatus",
    "CandidateVerification",
    "DEFAULT_TOOL_CANDIDATES_DIR",
    "ToolCandidate",
    "load_tool_candidates",
    "validate_tool_candidates",
]
