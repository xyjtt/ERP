from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any


@dataclass
class RunReportWriter:
    project_root: Path
    report_dir: str = "logs/run_reports"
    session_id: str = field(default_factory=lambda: datetime.now().strftime("%Y%m%d_%H%M%S"))
    report_path: Path | None = None
    summary_path: Path | None = None
    counters: dict[str, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        target_dir = self.project_root / self.report_dir
        target_dir.mkdir(parents=True, exist_ok=True)
        self.report_path = target_dir / f"{self.session_id}.jsonl"
        self.summary_path = target_dir / f"{self.session_id}.summary.json"

    def append(self, payload: dict[str, Any]) -> None:
        if not self.report_path:
            raise RuntimeError("Run report path is not initialized")
        record = dict(payload)
        record["logged_at"] = datetime.now().isoformat(timespec="seconds")
        status = str(record.get("status", "unknown")).strip().lower() or "unknown"
        self.counters[status] = self.counters.get(status, 0) + 1
        with self.report_path.open("a", encoding="utf-8") as file:
            file.write(json.dumps(record, ensure_ascii=False) + "\n")

    def info(self) -> dict[str, str]:
        return {
            "session_id": self.session_id,
            "report_path": str(self.report_path) if self.report_path else "",
            "summary_path": str(self.summary_path) if self.summary_path else "",
        }

    def write_summary(self, payload: dict[str, Any]) -> None:
        if not self.summary_path:
            raise RuntimeError("Run summary path is not initialized")
        summary = dict(payload)
        summary["session_id"] = self.session_id
        summary["counters"] = self.counters
        summary["finished_at"] = datetime.now().isoformat(timespec="seconds")
        with self.summary_path.open("w", encoding="utf-8") as file:
            json.dump(summary, file, ensure_ascii=False, indent=2)
            file.write("\n")
