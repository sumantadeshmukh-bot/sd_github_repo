"""One sequential blue ramp (light = weak evidence, dark = strong) used by the heatmap, workbook and reports.

Text on each step is chosen for contrast (dark ink on the two light steps, white on the two dark ones).
Every score is also printed as a number, so colour is never the only signal.
"""
SCORE_FILL = {1: "DCE8F5", 2: "9FC3E6", 3: "2E75B6", 4: "174A80"}
SCORE_TEXT = {1: "1A1A1A", 2: "1A1A1A", 3: "FFFFFF", 4: "FFFFFF"}
SEVERITY_FILL = {"High": "C0392B", "Medium": "E6A23C", "Low": "D5DBE3"}
SEVERITY_TEXT = {"High": "FFFFFF", "Medium": "1A1A1A", "Low": "1A1A1A"}


def level(score: float) -> int:
    """Nearest 1-4 level for an average score."""
    return min(4, max(1, int(score + 0.5)))
