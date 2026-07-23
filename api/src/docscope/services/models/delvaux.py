# api/src/docscope/services/models/delvaux.py
#
# Field extraction rules for "Delvaux": a Belgian payslip PDF (fiche de
# paie, template TREMA/Delvaux). Unlike UCM, the source PDF keeps a
# real text layer with no spaces between words (Reportlab-style tight
# character placement), so flattened text is enough for the fixed
# key/value fields - but the earnings table has no printed separator
# between rows, so a value's column can't be told apart from its text
# alone. That table is parsed from word coordinates instead (see
# extract_earnings_table), using the PDF's own cell borders as column
# boundaries.

import re

# --- fixed key/value fields -------------------------------------------------


def extract_fields(page) -> list[tuple[str, str | None]]:
    """Extract the fixed set of key/value fields from a Delvaux payslip.

    Most fields are anchored on a unique French label and read fine from
    flattened text. The employer/worker block is the exception: it's a
    two-column layout (employer left, worker right) with no label on 2 of
    its 3 lines, so splitting it from text alone means guessing where the
    right column starts. That block is read from word coordinates instead,
    the same way extract_earnings_table reads the table."""
    text = page.extract_text() or ""
    joined = "\n".join(line.strip() for line in text.splitlines() if line.strip())

    fields = []
    fields += _extract_employer_worker_block(page)
    fields += _extract_header_refs(joined)
    fields += _extract_status_block(joined)
    fields += _extract_summary_block(joined)
    return fields


def _extract_header_refs(joined: str) -> list[tuple[str, str | None]]:
    fields = []

    def add(label, pattern, flags=0):
        m = re.search(pattern, joined, flags)
        fields.append((label, m.group(1).strip() if m else None))

    add("Période", r"(\d{2}/\d{2}/\d{4}-\d{2}/\d{2}/\d{4})")
    add("N° du bulletin", r"compteindividuel\.\s*(\S+)")

    return fields


def _extract_employer_worker_block(page) -> list[tuple[str, str | None]]:
    words = page.extract_words()
    marker = next((w for w in words if w["text"].startswith("Employeur")), None)
    if marker is None:
        return [("Nom employeur", None), ("Adresse employeur", None),
                ("Nom travailleur", None), ("Adresse travailleur", None)]

    # 3 lines below the "Employeur :" line (name, street, city); split left
    # (employer) / right (worker) on x0, using the marker's own x0 as the
    # boundary - the worker column starts well to its right on every line.
    split_x = marker["x0"] + 150
    block = [w for w in words if marker["top"] - 1 <= w["top"] <= marker["top"] + 45]
    rows = _group_into_rows(block)

    employer_lines, worker_lines = [], []
    for row in rows[:3]:
        left = " ".join(w["text"] for w in row if w["x0"] < split_x and w["text"] != "Employeur:")
        right = " ".join(w["text"] for w in row if w["x0"] >= split_x)
        employer_lines.append(left.strip())
        worker_lines.append(right.strip())

    return [
        ("Nom employeur", employer_lines[0] or None),
        ("Adresse employeur", " ".join(line for line in employer_lines[1:] if line) or None),
        ("Nom travailleur", worker_lines[0] or None),
        ("Adresse travailleur", " ".join(line for line in worker_lines[1:] if line) or None),
    ]


def _extract_status_block(joined: str) -> list[tuple[str, str | None]]:
    fields = []

    def add(label, pattern, flags=0):
        m = re.search(pattern, joined, flags)
        fields.append((label, m.group(1).strip() if m else None))

    add("Numéro de registre national", r"régistrenational\s+(\S+)")
    add("Etat civil", r"Etatcivil\s+(.+)")
    add("Département", r"Département\s+(.+?)\s+Personnesàcharge")
    add("Personnes à charge", r"Personnesàcharge\s+(.+)")
    add("Fonction", r"Fonction\s+(.+?)\s+Commissionparitaire")
    add("Commission paritaire", r"Commissionparitaire\s+(.+)")
    add("Date d'entrée contrat", r"Dated'entréecontrat\s+(\S+)")
    # "Catégorie" is the last value on its own line (unlike the other
    # right-column fields above, which share a line with the next label),
    # so no DOTALL / no bounding label needed: match stops at the newline.
    add("Catégorie", r"Catégorie\s+(.+)")
    add("Salaire horaire", r"Salairehoraire\s+(\S+)")
    add("Fraction temps de travail", r"Fractiontempsdetravail\s+(.+)")

    return fields


# Fixed order of the final summary line's columns: used both to parse the
# "one row" summary and its ONSS PATR / NET A PAYER continuation.
SUMMARY_COLUMNS = [
    "BRUT", "BRUT-108", "ONSS", "HORS ONSS", "IMPOS", "PP", "HORS PP",
    "ACOMPTE", "NET",
]

NUMBER_RE = re.compile(r"-?\d{1,3}(?:\.\d{3})*,\d{2}|\b0\b")


def _extract_summary_block(joined: str) -> list[tuple[str, str | None]]:
    fields = []

    m = re.search(
        r"BRUT\s+BRUT-108\s+ONSS\s+HORSONSS\s+IMPOS\s+PP\s+HORSPP\s+ACOMPTE\s+NET\s*\n(.+)\n"
        r"ONSSPATR\s+(\S+)\s+NETAPAYER\s+(\S+)",
        joined,
    )
    if not m:
        fields += [(f"Synthèse {c}", None) for c in SUMMARY_COLUMNS]
        fields += [("ONSS patronal", None), ("Net à payer", None)]
        return fields

    values = NUMBER_RE.findall(m.group(1))
    for col, value in zip(SUMMARY_COLUMNS, values):
        fields.append((f"Synthèse {col}", value))
    fields.append(("ONSS patronal", m.group(2)))
    fields.append(("Net à payer", m.group(3)))

    return fields


# --- earnings table (rubriques) --------------------------------------------
#
# The table has vertical borders between columns but no horizontal ones
# between rows, so pdfplumber's own row-grouping merges every row's cells
# into one ("199\n1101\n2109", "JOURSDEPRESENCE\nHEURESPRESTEES\n...", ...)
# with blank cells silently dropped - useless for realigning rows. Instead:
# take the column x-boundaries from the table's own border rects, take each
# word's raw (x0, top) position, cluster by top into rows, and place each
# word in whichever column its x0 falls into.

TABLE_HEADER_LABELS = ["CODE", "RUBRIQUE", "JOURS", "NOMBRE", "BASE", "BRUT", "HORSONSS", "HORSPP"]
COLUMN_DISPLAY_NAMES = {
    "CODE": "Code", "RUBRIQUE": "Rubrique", "JOURS": "Jours", "NOMBRE": "Nombre",
    "BASE": "Base", "BRUT": "Brut", "HORSONSS": "Hors ONSS", "HORSPP": "Hors PP",
}
ROW_TOLERANCE = 3  # points: words within this many points of `top` are the same row


def extract_earnings_table(pdf) -> list[tuple[str, str, str]]:
    """Extract the "rubriques" table (one row per pay component: days
    worked, hours, base salary...). Returns (code, column, value) tuples,
    one per non-empty cell - same shape as UCM's earnings table."""
    results = []
    for page in pdf.pages:
        results += _extract_table_from_page(page)
    return results


def _extract_table_from_page(page) -> list[tuple[str, str, str]]:
    table = next(
        (t for t in page.find_tables() if _header_matches(t)),
        None,
    )
    if table is None:
        return []

    x0, top, x1, bottom = table.bbox
    boundaries = sorted(
        r["x0"] for r in page.rects
        if (r["x1"] - r["x0"]) < 1 and top - 1 <= r["top"] and r["bottom"] <= bottom + 1
    )
    boundaries = sorted(set(round(b, 1) for b in boundaries))
    if len(boundaries) < len(TABLE_HEADER_LABELS) + 1:
        return []

    # Header row height: skip it, keep only data rows below it.
    header_words = [w for w in page.extract_words() if top <= w["top"] <= top + 15]
    data_top = max((w["bottom"] for w in header_words), default=top) + 1

    words = [
        w for w in page.extract_words()
        if data_top <= w["top"] <= bottom and x0 <= w["x0"] <= x1
    ]
    rows = _group_into_rows(words)

    results = []
    for row_words in rows:
        by_column = _assign_columns(row_words, boundaries)
        code = by_column.get("CODE")
        if not code or not code.strip().isdigit():
            continue
        for header in TABLE_HEADER_LABELS[1:]:
            value = by_column.get(header)
            if value:
                results.append((code.strip(), COLUMN_DISPLAY_NAMES[header], value))
    return results


def _header_matches(table) -> bool:
    try:
        header = table.extract()[0]
    except (IndexError, TypeError):
        return False
    header_joined = "".join(c or "" for c in header).replace(" ", "").upper()
    return all(label in header_joined for label in TABLE_HEADER_LABELS)


def _group_into_rows(words: list[dict]) -> list[list[dict]]:
    rows: list[list[dict]] = []
    for word in sorted(words, key=lambda w: (w["top"], w["x0"])):
        row = next((r for r in rows if abs(r[0]["top"] - word["top"]) <= ROW_TOLERANCE), None)
        if row is None:
            rows.append([word])
        else:
            row.append(word)
    return rows


def _assign_columns(row_words: list[dict], boundaries: list[float]) -> dict[str, str]:
    by_column: dict[str, list[str]] = {label: [] for label in TABLE_HEADER_LABELS}
    for word in sorted(row_words, key=lambda w: w["x0"]):
        col_index = _column_index(word["x0"], boundaries)
        if col_index is None:
            continue
        by_column[TABLE_HEADER_LABELS[col_index]].append(word["text"])
    return {label: " ".join(parts) for label, parts in by_column.items() if parts}


def _column_index(x: float, boundaries: list[float]) -> int | None:
    for i in range(len(boundaries) - 1):
        if boundaries[i] <= x < boundaries[i + 1]:
            return i
    return None
