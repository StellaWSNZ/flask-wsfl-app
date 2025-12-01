import numpy as np
import matplotlib.pyplot as plt
from shapely.geometry import Polygon

def _quarter_arc(center, r, deg_start, deg_end, n=32):
    """Return (x,y) points sampling a circular arc from deg_start to deg_end."""
    th = np.deg2rad(np.linspace(deg_start, deg_end, n))
    cx, cy = center
    return [(cx + r*np.cos(t), cy + r*np.sin(t)) for t in th]

def rounded_rect_polygon(
    *,
    cx: float = 0.0,
    cy: float = 0.0,
    width: float = 8.0,
    height: float = 4.0,
    ratio: float | list[float] = 0.25,     # can be single or per-corner list
    corners_round: list[int] = [1,2,3,4],  # 1=TL, 2=TR, 3=BR, 4=BL
    n_arc: int = 32
) -> Polygon:
    """
    Build a Shapely Polygon for a rectangle centered at (cx,cy),
    with optional quarter-circle fillets on selected corners.
    If `ratio` is a single number, all rounded corners use that ratio.
    If it's a list, it must match len(corners_round).
    """
    # normalize ratios
    if isinstance(ratio, (int, float)):
        ratio_map = {c: ratio for c in corners_round}
    elif isinstance(ratio, list):
        if len(ratio) != len(corners_round):
            raise ValueError("If ratio is a list, its length must match corners_round.")
        ratio_map = dict(zip(corners_round, ratio))
    else:
        raise TypeError("ratio must be float or list[float]")

    x0, x1 = cx - width/2.0, cx + width/2.0
    y0, y1 = cy - height/2.0, cy + height/2.0

    def corner_radius(corner):
        if corner not in corners_round:
            return 0.0
        r_val = ratio_map[corner] * height
        return max(0.0, min(r_val, width/2.0, height/2.0))

    # radius for each corner
    r1 = corner_radius(1)
    r2 = corner_radius(2)
    r3 = corner_radius(3)
    r4 = corner_radius(4)

    C_TL = (x0 + r1, y1 - r1)
    C_TR = (x1 - r2, y1 - r2)
    C_BR = (x1 - r3, y0 + r3)
    C_BL = (x0 + r4, y0 + r4)

    pts: list[tuple[float, float]] = []

    # ---- bottom-left (4) ----
    if 4 in corners_round and r4 > 0:
        pts += _quarter_arc(C_BL, r4, 180, 270, n_arc)
    else:
        pts.append((x0, y0))

    pts.append((x1 - r3 if r3 > 0 else x1, y0))

    # ---- bottom-right (3) ----
    if 3 in corners_round and r3 > 0:
        pts += _quarter_arc(C_BR, r3, 270, 360, n_arc)

    pts.append((x1, y1 - r2 if r2 > 0 else y1))

    # ---- top-right (2) ----
    if 2 in corners_round and r2 > 0:
        pts += _quarter_arc(C_TR, r2, 0, 90, n_arc)
    else:
        pts.append((x1, y1))

    pts.append((x0 + r1 if r1 > 0 else x0, y1))

    # ---- top-left (1) ----
    if 1 in corners_round and r1 > 0:
        pts += _quarter_arc(C_TL, r1, 90, 180, n_arc)
    else:
        pts.append((x0, y1))

    pts.append((x0, y0 + r4 if r4 > 0 else y0))

    return Polygon(pts)