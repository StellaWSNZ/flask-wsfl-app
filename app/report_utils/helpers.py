def format_title(text):
    return text.title().replace("_", " ")
from pathlib import Path
import re
import matplotlib.font_manager as fm
import matplotlib.pyplot as plt

def load_ppmori_fonts(font_dir: str | Path) -> None:
    """
    Registers PP Mori Regular, SemiBold, and Bold fonts with Matplotlib.
    """
    font_dir = Path(font_dir)
    regular_font   = font_dir / "PPMori-Regular.otf"
    semibold_font  = font_dir / "PPMori-SemiBold.otf"
    bold_font      = font_dir / "PPMori-Bold.otf"

    found = []
    for f in (regular_font, semibold_font, bold_font):
        if f.exists():
            fm.fontManager.addfont(str(f))
            found.append(f.name)
    if not found:
        print(f"⚠️ No PP Mori fonts found in {font_dir}")
        return

    plt.rcParams.update({
        "font.family": "PP Mori",
        "font.weight": "regular",
        "font.size": 12,
    })

    print(f"✅ Loaded PP Mori fonts ({', '.join(found)}) from {font_dir}")
def choose_text_color(hex_color):
    # Convert hex to R, G, B (0-255)
    r_hex = int(hex_color[1:3], 16)
    g_hex = int(hex_color[3:5], 16)
    b_hex = int(hex_color[5:7], 16)

    # Normalize to 0-1
    r_norm = r_hex / 255.0
    g_norm = g_hex / 255.0
    b_norm = b_hex / 255.0

    # Convert to linear RGB
    def to_linear(c):
        if c <= 0.04045:
            return c / 12.92
        else:
            return ((c + 0.055) / 1.055) ** 2.4

    r_linear = to_linear(r_norm)
    g_linear = to_linear(g_norm)
    b_linear = to_linear(b_norm)

    # Calculate Luminance
    luminance = (0.2126 * r_linear) + (0.7152 * g_linear) + (0.0722 * b_linear)

    # Choose text color
    if luminance > 0.179:
        return "#000000"  # Black text
    else:
        return "#ffffff"  # White text
    
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D

def draw_debug_grid(ax, *, nx: int = 10, ny: int = 10, color: str = "#CCCCCC", lw: float = 0.5, zorder: int = 0, dark: bool = True,):
    """
    Draws a faint grid in axes-relative coordinates (0–1).
    Useful for layout debugging in PDF visualizations.
    """
    for i in range(nx + 1):
        if(i % 5 == 0 & dark):
            col =  "#878787"
        else:
            col = color

        x = i / nx
        ax.add_line(Line2D([x, x], [0, 1], color=col, lw=lw, ls="--", transform=ax.transAxes, zorder=zorder))

    for j in range(ny + 1):
        y = j / ny
        if(j % 5 == 0 & dark):
            col =  "#383737"
        else:
            col = color
        ax.add_line(Line2D([0, 1], [y, y], color=col, lw=lw, ls="--", transform=ax.transAxes, zorder=zorder))

    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)


def get_display_name(Funder):
    if(Funder == "Christchurch City Council"):
        FunderDisplay = "Christchurch City"
    else:
        FunderDisplay = Funder
    return (FunderDisplay)

def parse_funders(s: str) -> list[str]:
    # split by comma, strip whitespace, drop empties/dupes while preserving order
    seen = set()
    out: list[str] = []
    for part in (p.strip() for p in s.split(",") if p.strip()):
        if part not in seen:
            seen.add(part)
            out.append(part)
    return out

def slugify(name: str) -> str:
    # safe filename: keep letters/numbers/+-._, replace spaces with _
    s = name.strip()
    s = re.sub(r"\s+", "_", s)
    s = re.sub(r"[^A-Za-z0-9_\-\.]+", "", s)
    return s[:80] if s else "report"