"""heatmap.png: use cases (rows) x 8 areas (columns), each cell a 1-4 score."""
from __future__ import annotations

from pathlib import Path
from textwrap import fill

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.colors import ListedColormap  # noqa: E402
from matplotlib.patches import Patch  # noqa: E402

from .models import Scored  # noqa: E402
from .palette import SCORE_FILL, SCORE_TEXT  # noqa: E402
from .scoring import Config  # noqa: E402

INK, MUTED = "#1f2933", "#52606d"


def draw_heatmap(sc: Scored, cfg: Config, path: Path) -> Path:
    ucs = sc.register.use_cases
    areas = cfg.areas
    data = [[sc.scores[u.id][a["key"]].score for a in areas] for u in ucs]
    n = len(ucs)
    fig_h = max(4.2, 0.55 * n + 2.6)
    fig, ax = plt.subplots(figsize=(12, fig_h), dpi=200)
    cmap = ListedColormap([f"#{SCORE_FILL[i]}" for i in (1, 2, 3, 4)])
    ax.imshow(data, cmap=cmap, vmin=0.5, vmax=4.5, aspect="auto")
    for i, row in enumerate(data):                       # 2px surface gaps between cells
        for j, v in enumerate(row):
            ax.add_patch(plt.Rectangle((j - 0.5, i - 0.5), 1, 1, fill=False, ec="white", lw=2.5))
            ax.text(j, i, str(v), ha="center", va="center", fontsize=12, fontweight="bold", color=f"#{SCORE_TEXT[v]}")
    ax.set_xticks(range(len(areas)), [fill(a["name"], 14) for a in areas], fontsize=9.5, color=INK)
    ax.xaxis.tick_top()
    ax.set_yticks(range(n), [f"{u.id}  {fill(u.name, 34)}  [{sc.tiers[u.id].tier}]" for u in ucs], fontsize=9.5, color=INK)
    ax.tick_params(length=0)
    for spine in ax.spines.values():
        spine.set_visible(False)
    ax.set_title(f"{sc.register.org.name}: AI readiness by use case and area (score 1-4)", loc="left",
                 fontsize=12.5, fontweight="bold", color=INK, pad=44)
    handles = [Patch(facecolor=f"#{SCORE_FILL[i]}", edgecolor="#8a97a6", label=f"{i} {cfg.areas_doc['score_labels'][i]}")
               for i in (1, 2, 3, 4)]
    ax.legend(handles=handles, loc="upper center", bbox_to_anchor=(0.5, -0.025), ncol=4, frameon=False,
              fontsize=9.5, labelcolor=INK)
    fig.text(0.01, 0.005, "Tier in brackets is the assessed risk tier. Scores come only from the register and control "
             "answers supplied; nothing was independently tested.", fontsize=8, color=MUTED)
    fig.tight_layout(rect=(0, 0.02, 1, 1))
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, facecolor="white")
    plt.close(fig)
    return path
