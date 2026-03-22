from __future__ import annotations

from html import escape
from pathlib import Path

from job_intake.storage.models import JobORM


def render_html_report(jobs: list[JobORM], output_path: Path) -> Path:
    rows = []
    for job in jobs:
        rows.append(
            "<tr>"
            f"<td>{escape(job.tier)}</td>"
            f"<td>{escape(job.company)}</td>"
            f"<td>{escape(job.title)}</td>"
            f"<td>{escape(job.filter_decision)}</td>"
            f"<td>{escape(str(round(job.fit_score, 2)))}</td>"
            f"<td>{escape(', '.join(job.matched_signals[:4]))}</td>"
            f"<td>{escape(', '.join(job.detected_blockers[:4]))}</td>"
            f"<td><a href=\"{escape(job.apply_url or job.original_url)}\">open</a></td>"
            "</tr>"
        )
    html = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Job Intake Review</title>
  <style>
    body {{ font-family: Georgia, serif; background: #f5f0e8; color: #1f2933; padding: 24px; }}
    table {{ width: 100%; border-collapse: collapse; background: #fffdf9; }}
    th, td {{ padding: 10px; border: 1px solid #d9c8aa; vertical-align: top; }}
    th {{ background: #ead8b7; text-align: left; }}
    h1 {{ margin-top: 0; }}
  </style>
</head>
<body>
  <h1>Recent Job Review</h1>
  <table>
    <thead>
      <tr><th>Tier</th><th>Company</th><th>Title</th><th>Decision</th><th>Score</th><th>Signals</th><th>Blockers</th><th>Link</th></tr>
    </thead>
    <tbody>
      {''.join(rows)}
    </tbody>
  </table>
</body>
</html>
"""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(html, encoding="utf-8")
    return output_path
