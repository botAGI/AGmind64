"""Diagnostics: preflight, doctor, health checks.

Используется CLI команды `agmind doctor` и установщиком.
"""

from __future__ import annotations

from agmind.diagnostics.doctor import doctor_report, format_doctor_report, run_preflight

__all__ = ["doctor_report", "format_doctor_report", "run_preflight"]
