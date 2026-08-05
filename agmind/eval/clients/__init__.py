"""Network boundary for the evaluation harness.

This is the ONLY package under ``agmind/eval/`` permitted to perform network I/O, and that rule
is enforced mechanically by ``tests/eval/test_no_unguarded_egress.py`` (an AST walk, not a grep).

Every client in here takes an :class:`agmind.eval.endpoints.EndpointVerdict` as a REQUIRED
constructor argument. There is deliberately no constructor that accepts a bare URL: the zero-egress
promise cannot depend on each call site remembering to validate first, and the existing A8 gate
cannot see CLI-supplied endpoints at all (it inspects service-descriptor env keys).
"""

from __future__ import annotations

__all__: list[str] = []
