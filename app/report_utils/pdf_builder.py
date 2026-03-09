# app/report_utils/pdf_builder.py
from pathlib import Path
from typing import Literal, Tuple, Optional

import matplotlib.pyplot as plt
from matplotlib.backends.backend_pdf import PdfPages


# ISO paper sizes in inches (portrait)
ISO_IN = {
    "A5": (5.83, 8.27),
    "A4": (8.27, 11.69),
    "A3": (11.69, 16.54),
}


def _page_dims(
    page_size: Literal["A5", "A4", "A3"] = "A4",
    orientation: Literal["portrait", "landscape"] = "portrait",
    custom_size_in: Optional[Tuple[float, float]] = None,
) -> Tuple[float, float]:
    """Return (width_in, height_in) for a given page size and orientation."""
    if custom_size_in:
        w, h = custom_size_in
    else:
        w, h = ISO_IN.get(page_size.upper(), ISO_IN["A4"])
    if orientation == "landscape":
        w, h = h, w
    return w, h


# -----------------------------
# Footer logic
# -----------------------------
def _add_full_width_footer(
    fig: plt.Figure,
    footer_png: str,
    width_in: float,
    height_in: float,
    *,
    bottom_margin_frac: float = 0.0,
    max_footer_height_frac: float = 0.25,
) -> None:
    """
    Add a full-width footer image at the bottom of the figure without distortion.
    If the image is narrower than the page, it moves downward so it can occupy
    the full width of the page.
    """
    from matplotlib import image as mpimg

    img = mpimg.imread(footer_png)
    img_h, img_w = img.shape[:2]
    img_aspect = img_h / img_w  # height / width

    required_footer_height_frac = (img_aspect * width_in) / height_in

    footer_h = min(required_footer_height_frac, max_footer_height_frac)
    y0 = bottom_margin_frac

    if required_footer_height_frac > max_footer_height_frac:
        extra = required_footer_height_frac - max_footer_height_frac
        y0 = max(0.0, bottom_margin_frac - extra)
        footer_h = max_footer_height_frac

    footer_h = min(footer_h, 1.0 - y0)

    ax_img = fig.add_axes([0.0, y0, 1.0, footer_h])
    ax_img.imshow(img, aspect="auto", extent=[0, 1, 0, 1])
    ax_img.axis("off")


# -----------------------------
# Modular PDF lifecycle
# -----------------------------
def open_pdf(
    filename: str,
    page_size: Literal["A5", "A4", "A3"] = "A4",
    orientation: Literal["portrait", "landscape"] = "portrait",
    dpi: int = 300,
    custom_size_in: Optional[Tuple[float, float]] = None,
):
    """Open a PdfPages object and return (pdf, width_in, height_in, dpi)."""
    out = Path(filename)
    out.parent.mkdir(parents=True, exist_ok=True)
    width_in, height_in = _page_dims(page_size, orientation, custom_size_in)
    pdf = PdfPages(out)
    return pdf, width_in, height_in, dpi


def new_page(width_in: float, height_in: float, dpi: int = 300):
    """
    Create a blank page (fig, ax) with axes off and a true 0–1 coordinate system
    that covers the full page.
    """
    fig, ax = plt.subplots(figsize=(width_in, height_in), dpi=dpi)

    # Make the axes full-bleed
    ax.set_position([0, 0, 1, 1])
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis("off")

    # Also ensure figure has no implicit padding layout
    fig.subplots_adjust(left=0, right=1, bottom=0, top=1)

    print("✅ Created new page")
    return fig, ax


def save_page(
    pdf: PdfPages,
    fig: plt.Figure,
    footer_png: Optional[str] = None,
    *,
    width_in: Optional[float] = None,
    height_in: Optional[float] = None,
    footer_bottom_margin_frac: float = 0.0,
    footer_max_height_frac: float = 0.25,
    full_bleed: bool = True,
):
    """
    Save a figure to the PDF, optionally adding a footer before saving.

    IMPORTANT:
      - For full-bleed pages, do NOT use bbox_inches="tight" because it crops.
      - If you want content-cropped export, set full_bleed=False.
    """
    if footer_png and Path(footer_png).exists():
        _add_full_width_footer(
            fig,
            footer_png,
            width_in or 8.27,
            height_in or 11.69,
            bottom_margin_frac=footer_bottom_margin_frac,
            max_footer_height_frac=footer_max_height_frac,
        )

    if full_bleed:
        pdf.savefig(fig, bbox_inches=None, pad_inches=0)
    else:
        pdf.savefig(fig, bbox_inches="tight", pad_inches=0)

    plt.close(fig)

    if footer_png and Path(footer_png).exists():
        print(f"✅ Saved page with footer {footer_png}")
    else:
        print("✅ Saved page without footer png")


def close_pdf(pdf):
    """Close PdfPages safely across matplotlib versions."""
    if pdf is None:
        return
    try:
        pdf.close()
    except Exception as e:
        print(f"⚠️ close_pdf: could not close PDF cleanly: {e}")