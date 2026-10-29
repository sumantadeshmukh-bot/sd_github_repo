"""Register in, five outputs out."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .board_docx import write_board_summary
from .heatmap import draw_heatmap
from .loader import load_register
from .models import Scored
from .report_docx import write_report
from .scoring import Config, score_register
from .xlsx_out import write_workbook


@dataclass
class PackResult:
    scored: Scored
    files: dict[str, Path]


def build_pack(input_path: Path, out_dir: Path, org_path: Path | None = None, config: Config | None = None) -> PackResult:
    cfg = config or Config()
    sc = score_register(load_register(input_path, org_path), cfg)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    files = {
        "workbook": write_workbook(sc, cfg, out_dir / "evidence_pack.xlsx"),
        "heatmap": draw_heatmap(sc, cfg, out_dir / "heatmap.png"),
    }
    files["report"] = write_report(sc, cfg, files["heatmap"], out_dir / "evidence_pack.docx")
    files["board_summary"] = write_board_summary(sc, cfg, out_dir / "board_summary.docx")
    return PackResult(sc, files)
