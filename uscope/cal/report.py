"""PDF report for a calibration session."""

import glob
import os

from uscope.cal.registry import get as get_cal
from uscope.util import writej


def write_report(session, path=None):
    """Write report.pdf and report.json into the session directory."""
    path = path or session.path
    pdf_fn = os.path.join(path, "report.pdf")
    json_fn = os.path.join(path, "report.json")
    summary = {
        "session": path,
        "quality": session.quality,
        "calibrations": {},
    }
    for name, result in session.results.items():
        summary["calibrations"][name] = {
            "ok": result.ok,
            "message": result.message,
            "values": result.values,
            "stats": result.stats,
        }
    writej(json_fn, summary)

    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from matplotlib.backends.backend_pdf import PdfPages
    except Exception:
        return pdf_fn

    with PdfPages(pdf_fn) as pdf:
        fig, ax = plt.subplots(figsize=(8.5, 11))
        ax.axis("off")
        lines = ["pyuscope calibration report", ""]
        try:
            lines.append("Model: %s" % session.microscope.model())
            lines.append("Serial: %s" % session.microscope.serial())
        except Exception:
            pass
        lines.append("Quality: %s" % session.quality)
        lines.append("Session: %s" % path)
        lines.append("")
        for name in session.names:
            result = session.results.get(name)
            title = get_cal(name).title
            if result is None:
                lines.append("%s: (no result)" % title)
                continue
            lines.append("%s: %s  %s" %
                         (title, "PASS" if result.ok else "FAIL",
                          result.message or ""))
            for k, v in result.values.items():
                if isinstance(v, float):
                    lines.append("    %s: %0.6g" % (k, v))
                else:
                    lines.append("    %s: %s" % (k, v))
        ax.text(0.05,
                0.95,
                "\n".join(lines),
                va="top",
                family="monospace",
                fontsize=10,
                transform=ax.transAxes)
        pdf.savefig(fig)
        plt.close(fig)

        for name in session.names:
            pngs = sorted(glob.glob(os.path.join(path, name, "*.png")))
            for png in pngs:
                try:
                    im = plt.imread(png)
                except Exception:
                    continue
                fig, ax = plt.subplots(figsize=(8.5, 11))
                ax.imshow(im)
                ax.set_title("%s: %s" % (name, os.path.basename(png)))
                ax.axis("off")
                pdf.savefig(fig)
                plt.close(fig)
    return pdf_fn
