"""
PDF report generator for scan results.

Uses fpdf2 (pure Python) — no system-level dependencies required beyond
the pip package itself.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fpdf import FPDF

# ── Greyscale palette (R, G, B) ───────────────────────────────────────────────
# Print-safe — no ink colour required.  Contrast is driven by fill tones,
# bold weight, and underline rather than hue.
_HEADER_BG = (30,  30,  30)   # near-black  — page header bar
_DARK      = (20,  20,  20)   # near-black  — primary text
_MUTED     = (110, 110, 110)  # mid-grey    — labels / secondary text
_RULE      = (60,  60,  60)   # dark grey   — section underline rules
_STRIPE    = (242, 242, 242)  # light grey  — alternating row tint
_FILE_BG   = (210, 210, 210)  # medium grey — file-path header rows
_WHITE     = (255, 255, 255)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _safe(text: str, max_chars: int = 120) -> str:
    """Truncate and sanitise text for Latin-1 PDF core fonts."""
    if len(text) > max_chars:
        text = text[:max_chars - 3] + '...'
    return text.encode('latin-1', 'replace').decode('latin-1')


def _group_by_file(results: list) -> dict:
    by_file: dict[str, list] = {}
    for r in results:
        by_file.setdefault(r['file'], []).append(r)
    return by_file


# ── Custom FPDF subclass ──────────────────────────────────────────────────────

class _ScanReport(FPDF):
    """Adds a branded header bar and page-number footer to every page."""

    def header(self):
        self.set_fill_color(*_HEADER_BG)
        self.rect(0, 0, self.w, 11, style='F')
        self.set_y(2)
        self.set_font('Helvetica', 'B', 10)
        self.set_text_color(*_WHITE)
        self.set_x(10)
        self.cell(0, 7, 'Repository Scanner  -  Security Report', align='L')
        self.ln(13)

    def footer(self):
        self.set_y(-12)
        self.set_font('Helvetica', 'I', 7.5)
        self.set_text_color(*_MUTED)
        ts = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')
        self.cell(0, 8, f'Page {self.page_no()}/{{nb}}  -  {ts}', align='C')


# ── Section-level drawing helpers ─────────────────────────────────────────────

def _section_heading(pdf: _ScanReport, title: str) -> None:
    pdf.ln(3)
    pdf.set_font('Helvetica', 'B', 11)
    pdf.set_text_color(*_DARK)
    pdf.cell(0, 8, title, new_x='LMARGIN', new_y='NEXT')
    y = pdf.get_y()
    pdf.set_draw_color(*_RULE)
    pdf.set_line_width(0.4)
    pdf.line(pdf.l_margin, y, pdf.w - pdf.r_margin, y)
    pdf.set_line_width(0.2)
    pdf.ln(3)


def _kv_table(pdf: _ScanReport, rows: list[tuple[str, str]]) -> None:
    """Two-column key/value table with alternating row stripes."""
    key_w = 50
    val_w = pdf.epw - key_w
    for i, (key, val) in enumerate(rows):
        bg = _STRIPE if i % 2 == 0 else _WHITE
        pdf.set_fill_color(*bg)
        pdf.set_font('Helvetica', 'B', 8.5)
        pdf.set_text_color(*_MUTED)
        pdf.cell(key_w, 6.5, _safe(key, 60), fill=True)
        pdf.set_font('Helvetica', '', 8.5)
        pdf.set_text_color(*_DARK)
        pdf.cell(val_w, 6.5, _safe(val, 110), fill=True,
                 new_x='LMARGIN', new_y='NEXT')


def _word_list(pdf: _ScanReport, words: list[str]) -> None:
    """Render a list of words as a wrapped, shaded block."""
    if not words:
        pdf.set_font('Helvetica', 'I', 8.5)
        pdf.set_text_color(*_MUTED)
        pdf.cell(0, 7, '(none)', new_x='LMARGIN', new_y='NEXT')
        return
    pdf.set_font('Helvetica', '', 8.5)
    pdf.set_text_color(*_DARK)
    pdf.set_fill_color(*_STRIPE)
    pdf.multi_cell(0, 5.5, _safe(',  '.join(words), 3000), fill=True, align='L')


def _findings_table(pdf: _ScanReport, by_file: dict, is_exact: bool) -> None:
    """Render violations grouped by file.

    Contrast is achieved without colour:
      exact matches   — bold + underline, near-black
      partial matches — bold italic, mid-grey
    """
    col_line    = 14
    col_word    = 34
    col_content = pdf.epw - col_line - col_word

    if is_exact:
        word_style = 'BU'          # bold + underline
        word_color = _DARK
    else:
        word_style = 'BI'          # bold italic
        word_color = _MUTED

    for file_path, violations in by_file.items():
        # File path header row
        pdf.set_fill_color(*_FILE_BG)
        pdf.set_font('Helvetica', 'B', 8)
        pdf.set_text_color(*_DARK)
        pdf.cell(0, 6.5, '  ' + _safe(file_path, 110), fill=True,
                 new_x='LMARGIN', new_y='NEXT')

        # Column headers
        pdf.set_font('Helvetica', 'B', 7.5)
        pdf.set_text_color(*_MUTED)
        pdf.set_draw_color(*_MUTED)
        pdf.cell(col_line,    5, 'LINE',    border='B')
        pdf.cell(col_word,    5, 'WORD',    border='B')
        pdf.cell(col_content, 5, 'CONTENT', border='B',
                 new_x='LMARGIN', new_y='NEXT')
        pdf.set_draw_color(0, 0, 0)

        # Data rows
        for j, v in enumerate(violations):
            bg = _STRIPE if j % 2 == 0 else _WHITE
            pdf.set_fill_color(*bg)

            pdf.set_font('Helvetica', '', 8)
            pdf.set_text_color(*_MUTED)
            pdf.cell(col_line, 5.5, str(v['line_number']), fill=True)

            pdf.set_font('Helvetica', word_style, 8)
            pdf.set_text_color(*word_color)
            pdf.cell(col_word, 5.5, _safe(v['prohibited_word'], 28), fill=True)

            pdf.set_font('Courier', '', 7.5)
            pdf.set_text_color(*_DARK)
            pdf.cell(col_content, 5.5, _safe(v['line_content'], 90), fill=True,
                     new_x='LMARGIN', new_y='NEXT')

        pdf.ln(3)


# ── Public API ────────────────────────────────────────────────────────────────

def generate_pdf(scan_record: dict[str, Any]) -> bytes:
    """
    Build a formatted PDF report from a scan_record dict (as stored in
    scan_history).  Returns raw PDF bytes suitable for a file download.
    """
    results   = scan_record.get('results', [])
    exact_r   = [r for r in results if r.get('match_type') == 'exact']
    partial_r = [r for r in results if r.get('match_type') == 'partial']

    total     = scan_record.get('total_violations',   len(results))
    exact_c   = scan_record.get('exact_violations',   len(exact_r))
    partial_c = scan_record.get('partial_violations', len(partial_r))

    words_evaluated = sorted(scan_record.get('words_evaluated', []))
    matched_words   = sorted({r['prohibited_word'] for r in results})
    unique_files    = len({r['file'] for r in results})

    by_file_exact   = _group_by_file(exact_r)
    by_file_partial = _group_by_file(partial_r)

    # ── Build document ────────────────────────────────────────────────────────
    pdf = _ScanReport()
    pdf.set_auto_page_break(auto=True, margin=16)
    pdf.alias_nb_pages()
    pdf.add_page()
    pdf.set_margins(12, 14, 12)

    # Title
    pdf.set_font('Helvetica', 'B', 22)
    pdf.set_text_color(*_DARK)
    pdf.cell(0, 12, 'Scan Report', new_x='LMARGIN', new_y='NEXT')
    pdf.set_font('Helvetica', '', 9)
    pdf.set_text_color(*_MUTED)
    pdf.cell(0, 5, 'Generated by Repository Scanner',
             new_x='LMARGIN', new_y='NEXT')
    pdf.ln(4)

    # ── Scan details ──────────────────────────────────────────────────────────
    _section_heading(pdf, 'Scan Details')
    _kv_table(pdf, [
        ('Scan ID',       str(scan_record.get('id', '-'))),
        ('Target',        scan_record.get('repo_path', '-')),
        ('Source type',   scan_record.get('source_type', '-').capitalize()),
        ('Scanned at',    scan_record.get('timestamp', '-')),
        ('Case sensitive', 'Yes' if scan_record.get('case_sensitive') else 'No'),
        ('Max file size', f"{scan_record.get('max_file_size_mb', '-')} MB"),
    ])

    # ── Summary ───────────────────────────────────────────────────────────────
    _section_heading(pdf, 'Summary')
    _kv_table(pdf, [
        ('Total violations',   str(total)),
        ('Exact matches',      str(exact_c)),
        ('Partial matches',    str(partial_c)),
        ('Files affected',     str(unique_files)),
        ('Words evaluated',    str(len(words_evaluated) or len(matched_words))),
        ('Words with matches', str(len(matched_words))),
    ])

    # ── Prohibited words evaluated ────────────────────────────────────────────
    _section_heading(pdf, 'Prohibited Words Evaluated')
    _word_list(pdf, words_evaluated or matched_words)

    # ── Findings ──────────────────────────────────────────────────────────────
    if not results:
        _section_heading(pdf, 'Findings')
        pdf.set_font('Helvetica', 'B', 10)
        pdf.set_text_color(*_DARK)
        pdf.cell(0, 8, 'No prohibited words found - repository is clean.',
                 new_x='LMARGIN', new_y='NEXT')
        return bytes(pdf.output())

    _section_heading(pdf, f'Exact Matches  ({exact_c})')
    if exact_r:
        _findings_table(pdf, by_file_exact, is_exact=True)
    else:
        pdf.set_font('Helvetica', 'I', 8.5)
        pdf.set_text_color(*_MUTED)
        pdf.cell(0, 7, 'None.', new_x='LMARGIN', new_y='NEXT')

    _section_heading(pdf, f'Partial Matches  ({partial_c})')
    if partial_r:
        _findings_table(pdf, by_file_partial, is_exact=False)
    else:
        pdf.set_font('Helvetica', 'I', 8.5)
        pdf.set_text_color(*_MUTED)
        pdf.cell(0, 7, 'None.', new_x='LMARGIN', new_y='NEXT')

    return bytes(pdf.output())
