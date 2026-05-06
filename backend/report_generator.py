"""
report_generator.py
────────────────────
Auto-Generated Academic Research Reports for DWSIM simulations.

Pipeline:
  1. Aggregate parametric study data into a Pandas DataFrame
  2. Generate matplotlib plots (PNG) with engineering formatting
  3. Compile a structured PDF report (ReportLab) containing:
       - Title page (flowsheet name, date, property package)
       - Abstract (LLM-drafted)
       - Simulation Setup (flowsheet metadata)
       - Methodology (LLM-drafted)
       - Results & Discussion (LLM-drafted + data table + plots)
       - Appendix (raw data table)

Entry point:
    from report_generator import generate_report
    result = generate_report(report_spec)
"""

from __future__ import annotations

import io
import json
import os
import traceback
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

# ── numerical / plotting ──────────────────────────────────────────────────────
import pandas as pd
import matplotlib
matplotlib.use("Agg")           # non-interactive backend (no display needed)
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
from matplotlib import rcParams

# Engineering-style defaults
rcParams.update({
    "font.family":     "serif",
    "font.size":       10,
    "axes.titlesize":  11,
    "axes.labelsize":  10,
    "legend.fontsize": 9,
    "figure.dpi":      150,
    "axes.grid":       True,
    "grid.alpha":      0.4,
    "lines.linewidth": 1.8,
    "lines.markersize": 5,
})

# ── PDF generation ────────────────────────────────────────────────────────────
from reportlab.lib               import colors
from reportlab.lib.pagesizes     import A4
from reportlab.lib.styles        import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units         import cm
from reportlab.lib.enums         import TA_CENTER, TA_LEFT, TA_JUSTIFY
from reportlab.platypus          import (
    SimpleDocTemplate, Paragraph, Spacer, Image,
    Table as RLTable, TableStyle, PageBreak, HRFlowable,
)
from reportlab.platypus.flowables import KeepTogether

PAGE_W, PAGE_H = A4


# ─────────────────────────────────────────────────────────────────────────────
# Style sheet
# ─────────────────────────────────────────────────────────────────────────────

def _build_styles():
    base = getSampleStyleSheet()
    styles = {}

    styles["Title"] = ParagraphStyle(
        "Title", parent=base["Title"],
        fontSize=20, spaceAfter=6, textColor=colors.HexColor("#1a3a5c"),
        alignment=TA_CENTER, fontName="Times-Bold",
    )
    styles["Subtitle"] = ParagraphStyle(
        "Subtitle", parent=base["Normal"],
        fontSize=12, spaceAfter=4, textColor=colors.HexColor("#2c5f8a"),
        alignment=TA_CENTER, fontName="Times-Italic",
    )
    styles["SectionHeading"] = ParagraphStyle(
        "SectionHeading", parent=base["Heading1"],
        fontSize=13, spaceBefore=14, spaceAfter=6,
        textColor=colors.HexColor("#1a3a5c"), fontName="Times-Bold",
        borderPad=(0, 0, 3, 0),
    )
    styles["BodyText"] = ParagraphStyle(
        "BodyText", parent=base["Normal"],
        fontSize=10, spaceAfter=6, leading=15, alignment=TA_JUSTIFY,
        fontName="Times-Roman",
    )
    styles["Caption"] = ParagraphStyle(
        "Caption", parent=base["Normal"],
        fontSize=9, spaceAfter=8, textColor=colors.grey,
        alignment=TA_CENTER, fontName="Times-Italic",
    )
    styles["Meta"] = ParagraphStyle(
        "Meta", parent=base["Normal"],
        fontSize=9, spaceAfter=3, textColor=colors.HexColor("#555555"),
        alignment=TA_CENTER, fontName="Helvetica",
    )
    styles["TableHeader"] = ParagraphStyle(
        "TableHeader", parent=base["Normal"],
        fontSize=9, fontName="Helvetica-Bold",
        textColor=colors.whitesmoke, alignment=TA_CENTER,
    )
    styles["TableCell"] = ParagraphStyle(
        "TableCell", parent=base["Normal"],
        fontSize=9, fontName="Helvetica", alignment=TA_CENTER,
    )
    styles["Code"] = ParagraphStyle(
        "Code", parent=base["Code"],
        fontSize=8, fontName="Courier", leading=11,
    )
    return styles


# ─────────────────────────────────────────────────────────────────────────────
# Step 1: Data aggregation into DataFrame
# ─────────────────────────────────────────────────────────────────────────────

def build_dataframe(study_data: Dict[str, Any]) -> Tuple[pd.DataFrame, str, str]:
    """
    Convert parametric study result dict to a cleaned DataFrame.
    Returns (df, x_label, y_label).
    """
    table = study_data.get("table") or []
    if not table:
        # Reconstruct from results list
        results = study_data.get("results", [])
        table = [{"input": r["input"], "observed": r["observed"]} for r in results]

    df = pd.DataFrame(table)
    if df.empty:
        raise ValueError("No data in parametric study results")

    # Drop rows with None observed values
    df = df.dropna()

    cols = list(df.columns)
    x_col = cols[0]
    y_col = cols[1]

    # Numeric coercion
    df[x_col] = pd.to_numeric(df[x_col], errors="coerce")
    df[y_col] = pd.to_numeric(df[y_col], errors="coerce")
    df = df.dropna().sort_values(x_col).reset_index(drop=True)

    return df, x_col, y_col


# ─────────────────────────────────────────────────────────────────────────────
# Step 2: Plot generation
# ─────────────────────────────────────────────────────────────────────────────

def generate_plots(
    df: pd.DataFrame,
    x_col: str,
    y_col: str,
    output_dir: str,
    title: str = "",
) -> List[str]:
    """
    Generate engineering-quality plots for the parametric study.
    Returns list of saved PNG file paths.
    """
    os.makedirs(output_dir, exist_ok=True)
    saved_paths = []

    # ── Plot 1: Line plot (main trend) ────────────────────────────────────────
    fig, ax = plt.subplots(figsize=(7, 4.2))
    ax.plot(df[x_col], df[y_col],
            marker="o", color="#1a6faf", linewidth=2, markersize=6,
            markerfacecolor="white", markeredgewidth=1.5, label="Simulation data")

    # Trend line (polynomial fit if enough points)
    if len(df) >= 4:
        import numpy as np
        deg = min(3, len(df) - 1)
        coeffs = np.polyfit(df[x_col], df[y_col], deg)
        x_fit = np.linspace(df[x_col].min(), df[x_col].max(), 200)
        y_fit = np.polyval(coeffs, x_fit)
        ax.plot(x_fit, y_fit, "--", color="#e05c2a", linewidth=1.2,
                alpha=0.7, label=f"Polynomial fit (deg={deg})")
        ax.legend()

    _clean_axis_labels(ax, x_col, y_col)
    if title:
        ax.set_title(title, pad=10)
    fig.tight_layout()
    p1 = os.path.join(output_dir, "plot_trend.png")
    fig.savefig(p1, dpi=150, bbox_inches="tight")
    plt.close(fig)
    saved_paths.append(p1)

    # ── Plot 2: Bar chart (discrete view) ─────────────────────────────────────
    if len(df) <= 20:
        fig2, ax2 = plt.subplots(figsize=(7, 4.2))
        bars = ax2.bar(df[x_col].astype(str), df[y_col],
                       color="#1a6faf", edgecolor="navy", alpha=0.78, width=0.6)
        # Value labels on bars
        for bar, val in zip(bars, df[y_col]):
            ax2.text(bar.get_x() + bar.get_width() / 2,
                     bar.get_height() + 0.01 * (df[y_col].max() - df[y_col].min() + 1e-9),
                     f"{val:.3g}", ha="center", va="bottom", fontsize=8)
        _clean_axis_labels(ax2, x_col, y_col)
        ax2.set_xticklabels(df[x_col].astype(str).tolist(),
                            rotation=45, ha="right", fontsize=8)
        ax2.set_title(f"Bar Chart — {_short_label(y_col)} vs {_short_label(x_col)}", pad=10)
        fig2.tight_layout()
        p2 = os.path.join(output_dir, "plot_bar.png")
        fig2.savefig(p2, dpi=150, bbox_inches="tight")
        plt.close(fig2)
        saved_paths.append(p2)

    # ── Plot 3: Sensitivity (normalised % change) ─────────────────────────────
    if len(df) >= 3:
        import numpy as np
        x_norm = (df[x_col] - df[x_col].min()) / (df[x_col].max() - df[x_col].min() + 1e-12)
        y_base = df[y_col].iloc[0]
        y_pct  = 100.0 * (df[y_col] - y_base) / (abs(y_base) + 1e-12)

        fig3, ax3 = plt.subplots(figsize=(7, 4.2))
        ax3.plot(df[x_col], y_pct, marker="s", color="#2ca02c",
                 linewidth=1.8, markersize=5)
        ax3.axhline(0, color="grey", linewidth=0.8, linestyle="--")
        ax3.set_xlabel(_short_label(x_col))
        ax3.set_ylabel(f"% Change in {_short_label(y_col)} (relative to first point)")
        ax3.set_title("Sensitivity Analysis", pad=10)
        ax3.yaxis.set_major_formatter(mticker.PercentFormatter())
        fig3.tight_layout()
        p3 = os.path.join(output_dir, "plot_sensitivity.png")
        fig3.savefig(p3, dpi=150, bbox_inches="tight")
        plt.close(fig3)
        saved_paths.append(p3)

    return saved_paths


def _clean_axis_labels(ax, x_col: str, y_col: str):
    ax.set_xlabel(_short_label(x_col))
    ax.set_ylabel(_short_label(y_col))


def _short_label(col_name: str) -> str:
    """Shorten column name for axis labels."""
    # e.g. "Water in.temperature [kg/h]" -> "Water in temperature [kg/h]"
    return col_name.replace(".", " ").strip()


# ─────────────────────────────────────────────────────────────────────────────
# Step 3: PDF compilation
# ─────────────────────────────────────────────────────────────────────────────

def compile_pdf(
    output_path: str,
    title: str,
    report_text: Dict[str, str],     # {abstract, introduction, methodology, results, discussion, conclusion}
    df: pd.DataFrame,
    x_col: str,
    y_col: str,
    plot_paths: List[str],
    flowsheet_meta: Dict[str, Any],
) -> str:
    """
    Compile all content into a formatted A4 PDF.
    Returns the output path.
    """
    styles = _build_styles()
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)

    doc = SimpleDocTemplate(
        output_path,
        pagesize=A4,
        leftMargin=2.5*cm, rightMargin=2.5*cm,
        topMargin=2.5*cm,  bottomMargin=2.5*cm,
        title=title,
        author="DWSIM Agentic AI",
        subject="Process Simulation Research Report",
    )

    story: List[Any] = []

    # ── Title page ─────────────────────────────────────────────────────────────
    story.append(Spacer(1, 1.5*cm))
    story.append(Paragraph(title, styles["Title"]))
    story.append(Spacer(1, 0.4*cm))
    story.append(HRFlowable(width="100%", thickness=2,
                             color=colors.HexColor("#1a3a5c")))
    story.append(Spacer(1, 0.3*cm))

    # Subtitle / metadata
    fs_name = flowsheet_meta.get("flowsheet", "DWSIM Simulation")
    pp      = flowsheet_meta.get("property_package", "")
    date_str = datetime.now().strftime("%d %B %Y")
    story.append(Paragraph(f"Simulation: {fs_name}", styles["Subtitle"]))
    if pp:
        story.append(Paragraph(f"Thermodynamic Model: {pp}", styles["Meta"]))
    story.append(Paragraph(f"Generated: {date_str}", styles["Meta"]))
    story.append(Paragraph("Auto-generated by DWSIM Agentic AI", styles["Meta"]))
    story.append(Spacer(1, 1.0*cm))
    story.append(PageBreak())

    # ── Abstract ───────────────────────────────────────────────────────────────
    _section(story, "Abstract", styles)
    story.append(Paragraph(
        report_text.get("abstract", "No abstract provided."), styles["BodyText"]))
    story.append(Spacer(1, 0.5*cm))

    # ── Introduction ──────────────────────────────────────────────────────────
    if report_text.get("introduction"):
        _section(story, "1. Introduction", styles)
        story.append(Paragraph(report_text["introduction"], styles["BodyText"]))
        story.append(Spacer(1, 0.4*cm))
        sec_offset = 1
    else:
        sec_offset = 0

    # ── Simulation Setup ───────────────────────────────────────────────────────
    _section(story, f"{1 + sec_offset}. Simulation Setup", styles)
    meta_items = [
        ("Flowsheet",          flowsheet_meta.get("flowsheet", "—")),
        ("Property Package",   flowsheet_meta.get("property_package", "—")),
        ("Streams",            ", ".join(flowsheet_meta.get("streams", [])) or "—"),
        ("Unit Operations",    ", ".join(flowsheet_meta.get("unit_ops", [])) or "—"),
        ("Varied Parameter",   _short_label(x_col)),
        ("Observed Property",  _short_label(y_col)),
        ("Data Points",        str(len(df))),
        ("Software",           "DWSIM Process Simulator"),
    ]
    story.append(_build_meta_table(meta_items, styles))
    story.append(Spacer(1, 0.4*cm))

    # ── Methodology ───────────────────────────────────────────────────────────
    _section(story, f"{2 + sec_offset}. Methodology", styles)
    story.append(Paragraph(
        report_text.get("methodology", "No methodology provided."), styles["BodyText"]))
    story.append(Spacer(1, 0.4*cm))

    # ── Results ───────────────────────────────────────────────────────────────
    _section(story, f"{3 + sec_offset}. Results", styles)
    story.append(Paragraph(
        report_text.get("results", "No results provided."), styles["BodyText"]))
    story.append(Spacer(1, 0.5*cm))

    # Insert plots
    for i, plot_path in enumerate(plot_paths):
        if not os.path.exists(plot_path):
            continue
        plot_titles = [
            f"Figure 1. Parametric Trend — {_short_label(y_col)} vs. {_short_label(x_col)}.",
            "Figure 2. Bar Chart — discrete response values at each parameter setting.",
            "Figure 3. Sensitivity Analysis — percentage change relative to baseline.",
        ]
        caption = plot_titles[i] if i < len(plot_titles) else f"Figure {i+1}."
        img_width = 13 * cm
        try:
            story.append(KeepTogether([
                Image(plot_path, width=img_width, height=img_width * 0.6),
                Paragraph(caption, styles["Caption"]),
                Spacer(1, 0.4*cm),
            ]))
        except Exception:
            pass

    # ── Discussion ────────────────────────────────────────────────────────────
    if report_text.get("discussion"):
        _section(story, f"{4 + sec_offset}. Discussion", styles)
        story.append(Paragraph(report_text["discussion"], styles["BodyText"]))
        story.append(Spacer(1, 0.4*cm))
        stat_sec = 5 + sec_offset
        conc_sec = 6 + sec_offset
    else:
        stat_sec = 4 + sec_offset
        conc_sec = 5 + sec_offset

    # Statistical summary
    _section(story, f"{stat_sec}. Statistical Summary", styles)
    summary_items = [
        (f"{_short_label(x_col)} — Min", f"{df[x_col].min():.4g}"),
        (f"{_short_label(x_col)} — Max", f"{df[x_col].max():.4g}"),
        (f"{_short_label(y_col)} — Min", f"{df[y_col].min():.4g}"),
        (f"{_short_label(y_col)} — Max", f"{df[y_col].max():.4g}"),
        (f"{_short_label(y_col)} — Mean", f"{df[y_col].mean():.4g}"),
        (f"{_short_label(y_col)} — Std Dev", f"{df[y_col].std():.4g}"),
        (f"{_short_label(y_col)} — Range", f"{df[y_col].max() - df[y_col].min():.4g}"),
    ]
    # x value at max y
    idx_max = df[y_col].idxmax()
    idx_min = df[y_col].idxmin()
    summary_items.append(
        (f"Optimum {_short_label(x_col)} (max {_short_label(y_col)})",
         f"{df[x_col].iloc[idx_max]:.4g}")
    )
    summary_items.append(
        (f"Optimum {_short_label(x_col)} (min {_short_label(y_col)})",
         f"{df[x_col].iloc[idx_min]:.4g}")
    )
    story.append(_build_meta_table(summary_items, styles))
    story.append(Spacer(1, 0.5*cm))

    # ── Conclusion ────────────────────────────────────────────────────────────
    if report_text.get("conclusion"):
        _section(story, f"{conc_sec}. Conclusion", styles)
        story.append(Paragraph(report_text["conclusion"], styles["BodyText"]))
        story.append(Spacer(1, 0.4*cm))

    # ── Appendix: Raw Data Table ───────────────────────────────────────────────
    story.append(PageBreak())
    _section(story, "Appendix A — Raw Simulation Data", styles)
    story.append(_build_data_table(df, x_col, y_col, styles))
    story.append(Spacer(1, 0.3*cm))
    story.append(Paragraph(
        f"Table A1. Complete parametric study data ({len(df)} simulation runs).",
        styles["Caption"]))

    doc.build(story, onFirstPage=_header_footer, onLaterPages=_header_footer)
    return output_path


def _section(story, heading: str, styles):
    story.append(HRFlowable(width="100%", thickness=0.5, color=colors.HexColor("#cccccc")))
    story.append(Paragraph(heading, styles["SectionHeading"]))


def _header_footer(canvas, doc):
    """Draw page number and footer line."""
    canvas.saveState()
    canvas.setFont("Helvetica", 8)
    canvas.setFillColor(colors.HexColor("#888888"))
    canvas.drawString(2.5*cm, 1.5*cm, "DWSIM Agentic AI — Research Report")
    canvas.drawRightString(PAGE_W - 2.5*cm, 1.5*cm, f"Page {doc.page}")
    canvas.restoreState()


def _build_meta_table(items: List[Tuple[str, str]], styles) -> RLTable:
    """Two-column key-value table."""
    data = [[Paragraph(k, styles["TableHeader"]),
             Paragraph(v, styles["TableCell"])]
            for k, v in items]
    t = RLTable(data, colWidths=[7*cm, 9*cm])
    t.setStyle(TableStyle([
        ("BACKGROUND",  (0, 0), (0, -1), colors.HexColor("#1a3a5c")),
        ("BACKGROUND",  (1, 0), (1, -1), colors.HexColor("#f5f8fb")),
        ("ROWBACKGROUNDS", (1, 0), (1, -1),
         [colors.HexColor("#eaf0f7"), colors.HexColor("#f5f8fb")]),
        ("GRID",        (0, 0), (-1, -1), 0.4, colors.HexColor("#cccccc")),
        ("VALIGN",      (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING",  (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING",(0,0), (-1, -1), 5),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING",(0, 0), (-1, -1), 8),
    ]))
    return t


def _build_data_table(df: pd.DataFrame, x_col: str, y_col: str,
                      styles) -> RLTable:
    """Full data table with alternating row colours."""
    x_label = _short_label(x_col)
    y_label = _short_label(y_col)
    header = [Paragraph("#", styles["TableHeader"]),
              Paragraph(x_label, styles["TableHeader"]),
              Paragraph(y_label, styles["TableHeader"])]
    data = [header]
    for i, row in df.iterrows():
        data.append([
            Paragraph(str(i + 1), styles["TableCell"]),
            Paragraph(f"{row[x_col]:.5g}", styles["TableCell"]),
            Paragraph(f"{row[y_col]:.5g}", styles["TableCell"]),
        ])
    col_w = [1.5*cm, 7.5*cm, 7.5*cm]
    t = RLTable(data, colWidths=col_w, repeatRows=1)
    row_colors = [colors.HexColor("#eaf0f7"), colors.HexColor("#f5f8fb")]
    t.setStyle(TableStyle([
        ("BACKGROUND",   (0, 0), (-1, 0), colors.HexColor("#1a3a5c")),
        ("ROWBACKGROUNDS",(0, 1), (-1, -1), row_colors),
        ("GRID",         (0, 0), (-1, -1), 0.4, colors.HexColor("#cccccc")),
        ("VALIGN",       (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING",   (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING",(0, 0), (-1, -1), 4),
        ("LEFTPADDING",  (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
    ]))
    return t


# ─────────────────────────────────────────────────────────────────────────────
# Main entry point
# ─────────────────────────────────────────────────────────────────────────────

def generate_report(report_spec: Dict[str, Any]) -> Dict[str, Any]:
    """
    Generate a complete research report PDF from a parametric study.

    report_spec keys:
      title (str)               — Report title
      study_data (dict)         — Output from bridge.parametric_study()
      report_text (dict)        — {abstract, introduction, methodology,
                                   results, discussion, conclusion}
                                  (LLM-drafted, passed in by the agent)
      flowsheet_meta (dict)     — {flowsheet, property_package, streams, unit_ops}
      output_dir (str, opt)     — Where to save files (default: ~/Documents/reports/)
      output_pdf (str, opt)     — Override full PDF path
    """
    errors = []
    warnings = []

    title      = report_spec.get("title", "DWSIM Simulation Research Report")
    study_data = report_spec.get("study_data", {})
    report_text= report_spec.get("report_text", {})
    fs_meta    = report_spec.get("flowsheet_meta", {})
    out_dir    = report_spec.get("output_dir",
                                 os.path.join(os.path.expanduser("~/Documents"),
                                              "dwsim_reports"))
    out_pdf    = report_spec.get("output_pdf", "")

    # ── Step 1: Build DataFrame ────────────────────────────────────────────────
    try:
        df, x_col, y_col = build_dataframe(study_data)
    except Exception as e:
        return {"success": False, "error": f"Data aggregation failed: {e}"}

    if df.empty:
        return {"success": False, "error": "DataFrame is empty after cleaning"}

    # ── Step 2: Generate plots ─────────────────────────────────────────────────
    plot_dir = os.path.join(out_dir, "plots")
    plot_paths = []
    try:
        plot_paths = generate_plots(df, x_col, y_col, plot_dir, title)
    except Exception as e:
        warnings.append(f"Plot generation partial failure: {e}")
        traceback.print_exc()

    # ── Step 3 + 4: Compile PDF ────────────────────────────────────────────────
    if not out_pdf:
        safe_title = "".join(c if c.isalnum() or c in "-_ " else "_" for c in title)
        safe_title = safe_title.replace(" ", "_")[:60]
        out_pdf = os.path.join(out_dir, f"{safe_title}.pdf")

    try:
        compile_pdf(
            output_path=out_pdf,
            title=title,
            report_text=report_text,
            df=df,
            x_col=x_col,
            y_col=y_col,
            plot_paths=plot_paths,
            flowsheet_meta=fs_meta,
        )
    except Exception as e:
        return {"success": False,
                "error": f"PDF compilation failed: {e}",
                "traceback": traceback.format_exc(),
                "plot_paths": plot_paths,
                "warnings": warnings}

    # ── Summary stats ──────────────────────────────────────────────────────────
    stats = {
        "x_min":  round(float(df[x_col].min()), 5),
        "x_max":  round(float(df[x_col].max()), 5),
        "y_min":  round(float(df[y_col].min()), 5),
        "y_max":  round(float(df[y_col].max()), 5),
        "y_mean": round(float(df[y_col].mean()), 5),
        "y_std":  round(float(df[y_col].std()), 5),
        "n_points": len(df),
        "x_at_y_max": round(float(df[x_col].iloc[df[y_col].idxmax()]), 5),
        "x_at_y_min": round(float(df[x_col].iloc[df[y_col].idxmin()]), 5),
    }

    sections_present = [s for s in ("abstract","introduction","methodology",
                                     "results","discussion","conclusion")
                        if report_text.get(s, "").strip()]

    return {
        "success":          True,
        "pdf_path":         out_pdf,
        "plot_paths":       plot_paths,
        "data_points":      len(df),
        "x_column":         x_col,
        "y_column":         y_col,
        "statistics":       stats,
        "sections_present": sections_present,
        "warnings":         warnings or None,
    }
