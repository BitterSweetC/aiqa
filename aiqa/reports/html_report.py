"""AIQA Interactive HTML Test Dashboard Generator."""

from __future__ import annotations

import base64
import html
import json
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from aiqa.models.test_case import TestRunReport
from aiqa.security.redaction import redact_data, redact_text


class HtmlReporter:
    """Generates modern, standalone, interactive HTML test dashboards."""

    def __init__(self, output_dir: Path | str = Path("./reports")) -> None:
        self.output_dir = Path(output_dir)

    def save(
        self,
        report: TestRunReport,
        filename: str | Path | None = None,
    ) -> Path:
        """Render and save HTML report to disk.

        Args:
            report: The TestRunReport instance.
            filename: Optional output filename or full path.

        Returns:
            The Path where the report was saved.
        """
        self.output_dir.mkdir(parents=True, exist_ok=True)
        if filename:
            target_path = Path(filename)
            if not target_path.is_absolute() and not target_path.parent.name:
                target_path = self.output_dir / target_path
        else:
            ts_str = report.started_at.strftime("%Y%m%d_%H%M%S")
            target_path = self.output_dir / f"dashboard_{ts_str}.html"

        target_path.parent.mkdir(parents=True, exist_ok=True)
        html_content = self.generate(report)
        target_path.write_text(html_content, encoding="utf-8")
        return target_path

    def generate(self, report: TestRunReport) -> str:
        """Generate standalone HTML document string from TestRunReport."""
        summary = report.summary
        pass_rate_pct = f"{summary.pass_rate * 100:.1f}%"
        started_str = report.started_at.strftime("%Y-%m-%d %H:%M:%S UTC")

        # Encode screenshots as base64 if they exist on disk
        results_data = []
        for r in report.results:
            if hasattr(r, "model_dump"):
                try:
                    r_dict = r.model_dump(mode="json")
                except TypeError:
                    r_dict = r.model_dump()
            else:
                r_dict = dict(r)
            r_dict = redact_data(r_dict)

            # If screenshot exists, embed small data uri for standalone portability
            s_path = getattr(r, "screenshot_path", None)
            if s_path and Path(s_path).exists():
                try:
                    raw_bytes = Path(s_path).read_bytes()
                    encoded = base64.b64encode(raw_bytes).decode("utf-8")
                    r_dict["screenshot_base64"] = f"data:image/png;base64,{encoded}"
                except OSError:
                    r_dict["screenshot_base64"] = None
            else:
                r_dict["screenshot_base64"] = None
            results_data.append(r_dict)

        report_json_str = _json_for_inline_script(results_data)
        escaped_base_url = html.escape(redact_text(report.base_url), quote=True)
        if urlsplit(report.base_url).scheme.lower() in {"http", "https"}:
            target_markup = (
                f'<a href="{escaped_base_url}" target="_blank" rel="noopener noreferrer">'
                f"{escaped_base_url}</a>"
            )
        else:
            target_markup = escaped_base_url

        # Build HTML content
        return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>AIQA Test Report — {html.escape(report.suite_name)}</title>
  <style>
    :root {{
      --bg: #0f172a;
      --card-bg: #1e293b;
      --card-border: #334155;
      --text-main: #f8fafc;
      --text-muted: #94a3b8;
      --accent: #38bdf8;
      --pass: #22c55e;
      --fail: #ef4444;
      --error: #ec4899;
      --skip: #eab308;
      --badge-bg: #0f172a;
    }}
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
      background-color: var(--bg);
      color: var(--text-main);
      padding: 24px;
      line-height: 1.5;
    }}
    .container {{ max-width: 1200px; margin: 0 auto; }}
    header {{
      display: flex;
      justify-content: space-between;
      align-items: center;
      padding-bottom: 20px;
      border-bottom: 1px solid var(--card-border);
      margin-bottom: 24px;
    }}
    .brand {{ display: flex; align-items: center; gap: 12px; }}
    .brand-icon {{
      width: 40px; height: 40px; background: linear-gradient(135deg, #0284c7, #38bdf8);
      border-radius: 8px; display: flex; align-items: center; justify-content: center;
      font-weight: 900; font-size: 20px; color: white;
    }}
    .brand h1 {{ font-size: 24px; font-weight: 700; color: var(--text-main); }}
    .brand p {{ font-size: 13px; color: var(--text-muted); }}
    .meta-info {{ text-align: right; font-size: 13px; color: var(--text-muted); }}
    .meta-info a {{ color: var(--accent); text-decoration: none; }}

    /* KPI Cards */
    .kpi-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
      gap: 16px;
      margin-bottom: 24px;
    }}
    .kpi-card {{
      background: var(--card-bg);
      border: 1px solid var(--card-border);
      border-radius: 10px;
      padding: 16px 20px;
    }}
    .kpi-label {{ font-size: 12px; font-weight: 600; text-transform: uppercase; color: var(--text-muted); letter-spacing: 0.5px; }}
    .kpi-value {{ font-size: 28px; font-weight: 700; margin-top: 4px; }}
    .kpi-value.pass {{ color: var(--pass); }}
    .kpi-value.fail {{ color: var(--fail); }}
    .kpi-value.error {{ color: var(--error); }}
    .kpi-value.skip {{ color: var(--skip); }}
    .kpi-value.accent {{ color: var(--accent); }}

    /* Progress bar */
    .progress-bar-container {{
      width: 100%; height: 10px; background: #334155; border-radius: 999px;
      overflow: hidden; display: flex; margin-bottom: 24px;
    }}
    .progress-segment.pass {{ background: var(--pass); }}
    .progress-segment.fail {{ background: var(--fail); }}
    .progress-segment.error {{ background: var(--error); }}
    .progress-segment.skip {{ background: var(--skip); }}

    /* Filters */
    .controls {{
      display: flex; justify-content: space-between; align-items: center;
      margin-bottom: 16px; gap: 12px; flex-wrap: wrap;
    }}
    .filter-tabs {{ display: flex; gap: 8px; }}
    .filter-btn {{
      background: var(--card-bg); border: 1px solid var(--card-border);
      color: var(--text-main); padding: 8px 16px; border-radius: 6px;
      font-size: 13px; font-weight: 500; cursor: pointer; transition: all 0.2s;
    }}
    .filter-btn.active {{
      background: #0284c7; border-color: #38bdf8; color: white;
    }}
    .search-box {{
      background: var(--card-bg); border: 1px solid var(--card-border);
      color: var(--text-main); padding: 8px 14px; border-radius: 6px;
      font-size: 13px; width: 250px; outline: none;
    }}
    .search-box:focus {{ border-color: var(--accent); }}

    /* Test List */
    .test-card {{
      background: var(--card-bg);
      border: 1px solid var(--card-border);
      border-radius: 8px;
      margin-bottom: 12px;
      overflow: hidden;
    }}
    .test-header {{
      padding: 14px 18px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      cursor: pointer;
      user-select: none;
      transition: background 0.15s;
    }}
    .test-header:hover {{ background: rgba(255, 255, 255, 0.02); }}
    .test-left {{ display: flex; align-items: center; gap: 12px; }}
    .badge {{
      display: inline-block; padding: 3px 8px; border-radius: 4px;
      font-size: 11px; font-weight: 700; text-transform: uppercase;
    }}
    .badge.pass {{ background: rgba(34, 197, 94, 0.2); color: var(--pass); border: 1px solid rgba(34, 197, 94, 0.4); }}
    .badge.fail {{ background: rgba(239, 68, 68, 0.2); color: var(--fail); border: 1px solid rgba(239, 68, 68, 0.4); }}
    .badge.error {{ background: rgba(236, 72, 153, 0.2); color: var(--error); border: 1px solid rgba(236, 72, 153, 0.4); }}
    .badge.skip {{ background: rgba(234, 179, 8, 0.2); color: var(--skip); border: 1px solid rgba(234, 179, 8, 0.4); }}
    .test-id {{ font-family: monospace; font-size: 13px; color: var(--accent); }}
    .test-name {{ font-weight: 600; font-size: 15px; }}
    .test-right {{ display: flex; align-items: center; gap: 16px; font-size: 13px; color: var(--text-muted); }}

    /* Test Body */
    .test-body {{
      padding: 18px;
      border-top: 1px solid var(--card-border);
      display: none;
      background: #182234;
    }}
    .test-body.expanded {{ display: block; }}

    /* Diagnosis Panel */
    .diagnosis-panel {{
      background: rgba(239, 68, 68, 0.08);
      border: 1px solid rgba(239, 68, 68, 0.3);
      border-radius: 6px;
      padding: 14px 16px;
      margin-bottom: 16px;
    }}
    .diag-title {{
      font-size: 14px; font-weight: 700; color: #f87171;
      display: flex; align-items: center; gap: 8px; margin-bottom: 8px;
    }}
    .diag-box {{ margin-bottom: 8px; font-size: 13px; }}
    .diag-label {{ font-weight: 600; color: #fca5a5; }}
    .diag-evidence {{ margin-left: 18px; font-size: 12px; color: var(--text-muted); margin-top: 4px; }}
    .diag-remediation {{
      background: rgba(0, 0, 0, 0.2); padding: 8px 12px; border-radius: 4px;
      font-size: 12px; color: #cbd5e1; border-left: 3px solid var(--accent); margin-top: 8px;
    }}

    /* Table styles */
    table {{ width: 100%; border-collapse: collapse; margin-top: 8px; font-size: 13px; }}
    th, td {{ padding: 8px 12px; text-align: left; border-bottom: 1px solid var(--card-border); }}
    th {{ background: #131c2e; color: var(--text-muted); font-size: 11px; text-transform: uppercase; }}
    td.status-pass {{ color: var(--pass); font-weight: 600; }}
    td.status-fail {{ color: var(--fail); font-weight: 600; }}

    .section-title {{ font-size: 13px; font-weight: 700; color: var(--text-muted); text-transform: uppercase; margin: 16px 0 6px 0; }}
    .code-block {{
      background: #0f172a; padding: 10px 14px; border-radius: 6px; font-family: monospace;
      font-size: 12px; color: #cbd5e1; overflow-x: auto; white-space: pre-wrap; margin-top: 4px;
    }}
    .screenshot-thumb {{
      max-width: 100%; max-height: 400px; border-radius: 6px; border: 1px solid var(--card-border);
      margin-top: 8px; cursor: pointer; transition: transform 0.2s;
    }}
    .screenshot-thumb:hover {{ transform: scale(1.01); }}
    footer {{
      text-align: center; font-size: 12px; color: var(--text-muted); margin-top: 36px; padding-top: 20px;
      border-top: 1px solid var(--card-border);
    }}
  </style>
</head>
<body>
  <div class="container">
    <header>
      <div class="brand">
        <div class="brand-icon">A</div>
        <div>
          <h1>AIQA Test Execution Dashboard</h1>
          <p>Autonomous AI Web Testing &amp; Failure Root-Cause Analysis</p>
        </div>
      </div>
      <div class="meta-info">
        <div><strong>Suite:</strong> {html.escape(report.suite_name)}</div>
        <div><strong>Target:</strong> {target_markup}</div>
        <div><strong>Date:</strong> {started_str}</div>
        <div><strong>Duration:</strong> {summary.duration_seconds:.2f}s</div>
      </div>
    </header>

    <!-- KPI Grid -->
    <div class="kpi-grid">
      <div class="kpi-card">
        <div class="kpi-label">Pass Rate</div>
        <div class="kpi-value {'pass' if summary.pass_rate >= 1.0 else ('fail' if summary.failed > 0 else 'accent')}">{pass_rate_pct}</div>
      </div>
      <div class="kpi-card">
        <div class="kpi-label">Total Tests</div>
        <div class="kpi-value">{summary.total}</div>
      </div>
      <div class="kpi-card">
        <div class="kpi-label">Passed</div>
        <div class="kpi-value pass">{summary.passed}</div>
      </div>
      <div class="kpi-card">
        <div class="kpi-label">Failed</div>
        <div class="kpi-value fail">{summary.failed}</div>
      </div>
      <div class="kpi-card">
        <div class="kpi-label">Errors</div>
        <div class="kpi-value error">{summary.errors}</div>
      </div>
      <div class="kpi-card">
        <div class="kpi-label">Skipped</div>
        <div class="kpi-value skip">{summary.skipped}</div>
      </div>
      <div class="kpi-card">
        <div class="kpi-label">Inconclusive</div>
        <div class="kpi-value skip">{getattr(summary, 'inconclusive', 0)}</div>
      </div>
    </div>

    <!-- Progress Meter -->
    <div class="progress-bar-container">
      <div class="progress-segment pass" style="width: {(summary.passed / summary.total * 100) if summary.total else 0}%"></div>
      <div class="progress-segment fail" style="width: {(summary.failed / summary.total * 100) if summary.total else 0}%"></div>
      <div class="progress-segment error" style="width: {(summary.errors / summary.total * 100) if summary.total else 0}%"></div>
      <div class="progress-segment skip" style="width: {(summary.skipped / summary.total * 100) if summary.total else 0}%"></div>
    </div>

    <!-- Controls -->
    <div class="controls">
      <div class="filter-tabs">
        <button class="filter-btn active" onclick="setFilter('all')">All ({summary.total})</button>
        <button class="filter-btn" onclick="setFilter('pass')">Passed ({summary.passed})</button>
        <button class="filter-btn" onclick="setFilter('fail')">Failed ({summary.failed})</button>
        <button class="filter-btn" onclick="setFilter('error')">Errors ({summary.errors})</button>
        <button class="filter-btn" onclick="setFilter('skip')">Skipped ({summary.skipped})</button>
      </div>
      <input type="text" class="search-box" id="searchBox" placeholder="Filter tests by name or ID..." oninput="applyFilters()">
    </div>

    <!-- Test Cards List -->
    <div id="testList"></div>

    <footer>
      Generated automatically by <strong>AIQA</strong> (Autonomous QA Testing System) • Run ID: {html.escape(report.run_id)}
    </footer>
  </div>

  <script>
    const tests = {report_json_str};
    let currentFilter = 'all';

    function setFilter(filter) {{
      currentFilter = filter;
      document.querySelectorAll('.filter-btn').forEach(btn => {{
        btn.classList.toggle('active', btn.innerText.toLowerCase().startsWith(filter));
      }});
      applyFilters();
    }}

    function toggleExpand(index) {{
      const body = document.getElementById(`test-body-${{index}}`);
      if (body) {{
        body.classList.toggle('expanded');
      }}
    }}

    function applyFilters() {{
      const query = document.getElementById('searchBox').value.toLowerCase();
      const container = document.getElementById('testList');
      container.innerHTML = '';

      tests.forEach((t, idx) => {{
        const status = (t.status || 'unknown').toLowerCase();
        const matchesFilter = (currentFilter === 'all') || (status === currentFilter);
        const textMatch = !query || 
          (t.test_id && t.test_id.toLowerCase().includes(query)) ||
          (t.name && t.name.toLowerCase().includes(query)) ||
          (t.goal && t.goal.toLowerCase().includes(query));

        if (matchesFilter && textMatch) {{
          container.appendChild(createTestCard(t, idx));
        }}
      }});
    }}

    function createTestCard(t, idx) {{
      const card = document.createElement('div');
      card.className = 'test-card';

      const status = (t.status || 'unknown').toLowerCase();
      const duration = t.duration_seconds !== undefined ? t.duration_seconds.toFixed(2) + 's' : '—';
      const diag = t.diagnosis;

      let diagnosisHtml = '';
      if (diag) {{
        let evidenceList = '';
        if (diag.evidence && diag.evidence.length) {{
          evidenceList = `<ul class="diag-evidence">${{diag.evidence.map(e => `<li>${{escapeHtml(e)}}</li>`).join('')}}</ul>`;
        }}
        diagnosisHtml = `
          <div class="diagnosis-panel">
            <div class="diag-title">
              <span>⚠️ Failure Root-Cause Diagnosis</span>
              <span class="badge fail">${{escapeHtml(diag.severity || 'medium')}}</span>
            </div>
            <div class="diag-box"><span class="diag-label">Summary:</span> ${{escapeHtml(diag.summary)}}</div>
            <div class="diag-box"><span class="diag-label">Likely Cause:</span> <strong>${{escapeHtml(diag.likely_cause)}}</strong></div>
            ${{evidenceList}}
            <div class="diag-remediation"><strong>💡 Developer Remediation:</strong> ${{escapeHtml(diag.remediation)}}</div>
          </div>
        `;
      }}

      // Verifications table
      let verificationsHtml = '';
      if (t.verification_results && t.verification_results.length) {{
        const rows = t.verification_results.map(v => `
          <tr>
            <td class="${{v.passed ? 'status-pass' : 'status-fail'}}">${{v.passed ? '✓ PASS' : '✗ FAIL'}}</td>
            <td><code>${{escapeHtml(v.expectation.type)}}</code></td>
            <td>${{escapeHtml(v.expectation.description)}}</td>
            <td>${{escapeHtml(v.actual_value || '—')}}</td>
            <td>${{escapeHtml(v.message || '')}}</td>
          </tr>
        `).join('');
        verificationsHtml = `
          <div class="section-title">Verification Results</div>
          <table>
            <thead>
              <tr><th>Status</th><th>Type</th><th>Expectation</th><th>Observed Value</th><th>Details</th></tr>
            </thead>
            <tbody>${{rows}}</tbody>
          </table>
        `;
      }}

      // Action Steps trace
      let stepsHtml = '';
      if (t.jev_steps && t.jev_steps.length) {{
        const stepsFormatted = t.jev_steps.map((s, i) => `Step ${{i+1}} [${{s.action}}]: ${{s.details || s.description || JSON.stringify(s)}}`).join('\\n');
        stepsHtml = `
          <div class="section-title">Action Execution Trace</div>
          <div class="code-block">${{escapeHtml(stepsFormatted)}}</div>
        `;
      }}

      // Console errors & network errors
      let telemetryHtml = '';
      const netErrs = t.network_errors || [];
      const conLogs = t.console_logs || [];
      if (netErrs.length || conLogs.length) {{
        let items = [];
        netErrs.forEach(n => items.push(`[Network ${{n.status || 'Failed'}}] ${{n.method || 'GET'}} ${{n.url}}`));
        conLogs.forEach(c => items.push(`[Console ${{c.type}}] ${{c.text}}`));
        telemetryHtml = `
          <div class="section-title">Browser Telemetry (Network & Console Errors)</div>
          <div class="code-block">${{escapeHtml(items.join('\\n'))}}</div>
        `;
      }}

      // Screenshot preview
      let screenshotHtml = '';
      if (t.screenshot_base64) {{
        screenshotHtml = `
          <div class="section-title">Failure Screenshot</div>
          <a href="${{t.screenshot_base64}}" target="_blank">
            <img src="${{t.screenshot_base64}}" class="screenshot-thumb" alt="Test Screenshot">
          </a>
        `;
      }}

      card.innerHTML = `
        <div class="test-header" onclick="toggleExpand(${{idx}})">
          <div class="test-left">
            <span class="badge ${{status}}">${{status}}</span>
            ${{t.inconclusive ? '<span class="badge skip">inconclusive</span>' : ''}}
            <span class="test-id">${{escapeHtml(t.test_id)}}</span>
            <span class="test-name">${{escapeHtml(t.name || t.test_id)}}</span>
          </div>
          <div class="test-right">
            ${{diag ? '<span title="Diagnosis Available">🔍 Diag</span>' : ''}}
            <span>${{duration}}</span>
            <span>▼</span>
          </div>
        </div>
        <div class="test-body" id="test-body-${{idx}}">
          ${{diagnosisHtml}}
          ${{verificationsHtml}}
          ${{stepsHtml}}
          ${{telemetryHtml}}
          ${{screenshotHtml}}
        </div>
      `;

      return card;
    }}

    function escapeHtml(str) {{
      if (!str) return '';
      return String(str)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#039;');
    }}

    // Initial render
    applyFilters();
  </script>
</body>
</html>
"""


def _json_for_inline_script(value: Any) -> str:
    """Serialize data for a script block without permitting tag breakout."""
    return (
        json.dumps(value, default=str, ensure_ascii=False)
        .replace("&", "\\u0026")
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )
