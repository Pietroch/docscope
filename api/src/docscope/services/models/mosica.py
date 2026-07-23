# api/src/docscope/services/models/mosica.py
#
# Field extraction rules for "Mosica": a French payslip PDF (bulletin de
# paye, EBP Informatique / MOSICA SAS template). Most source PDFs are
# scanned images run through OCR, so text is flattened, two-column blocks
# (employer left / worker right) get glued line by line, and some glyphs
# are misread (e.g. NAF "6201Z" read as "62017"). Values are kept as read;
# such OCR slips are corrected downstream, not guessed here.
#
# Two independent parts (same public interface as ucm/apside):
#   - extract_fields(): single key/value fields (employer, URSSAF, worker,
#     contract/status, refs)
#   - extract_earnings_table(): the "Libellé/Base/Taux/Gain/Retenue" table
#     plus the bottom cumuls - not built yet (stub).

import re


def extract_fields(text: str) -> list[tuple[str, str | None]]:
    """Extract the fixed set of key/value fields from a Mosica payslip."""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    joined = "\n".join(lines)

    fields = []
    fields += _extract_employer_block(joined)
    fields += _extract_urssaf_block(joined)
    fields += _extract_worker_block(joined)
    fields += _extract_contract_block(joined)
    fields += _extract_header_refs(joined)
    fields += _extract_footer_block(joined)
    return fields


def _extract_employer_block(joined: str) -> list[tuple[str, str | None]]:
    fields = []

    def add(label, pattern, flags=0):
        m = re.search(pattern, joined, flags)
        fields.append((label, m.group(1).strip() if m else None))

    # Employer name: first "... SAS" line (the header top-left block).
    add("Nom employeur", r"^(.+?SAS)\b", re.MULTILINE)

    # Employer address: the lines between the name and the SIRET line, left
    # column only. The worker column (civility line + "<n> rue ..." +
    # "<cp> <ville>") is glued on the right by the flattening; it's cut off
    # each line at the worker street ("<digits> rue") or postal-code token.
    m = re.search(r"SAS\b.*?\n(.+?)\nSIRET", joined, re.DOTALL)
    if m:
        left = []
        for line in m.group(1).splitlines():
            # Cut the worker column glued on the right: at its street
            # ("<n> rue ...") or at its city ("<cp> <VILLE>"). A lone postal
            # code like "CS 71975" is NOT a cut point (must be <cp> + city).
            left.append(re.split(r"\s+(?=\d+\s+rue\b|\d{5}\s+[A-ZÉÈ])", line)[0].strip())
        fields.append(("Adresse employeur", " ".join(part for part in left if part) or None))
    else:
        fields.append(("Adresse employeur", None))

    add("SIRET", r"SIRET\s*:\s*(\S+)")
    add("NAF", r"NAF\s*:\s*(\S+)")

    return fields


def _extract_urssaf_block(joined: str) -> list[tuple[str, str | None]]:
    fields = []

    # URSSAF name: the "URSSAF ..." line (left column), cut before any
    # right-column label glued on ("Date d'entrée", etc.).
    m = re.search(r"^(URSSAF .+?)(?:\s{2,}|\s+Date d|\s+Nature|$)", joined, re.MULTILINE)
    fields.append(("Nom URSSAF", m.group(1).strip() if m else None))

    # URSSAF address: the street line after it + the "<cp> ... CEDEX <n>"
    # line, left column each.
    street = re.search(r"URSSAF .+\n(.+?)(?:\s{2,}|\s+Date|$)", joined, re.MULTILINE)
    city = re.search(r"(\d{5}\s+.+?CEDEX\s+\d+)", joined)
    parts = [m.group(1).strip() for m in (street, city) if m]
    fields.append(("Adresse URSSAF", " ".join(parts) or None))

    return fields


def _extract_worker_block(joined: str) -> list[tuple[str, str | None]]:
    fields = []

    # Worker name: after the civility marker.
    m = re.search(r"(?:Monsieur|Madame|Mademoiselle)\s+(.+?)(?:\n|$)", joined)
    fields.append(("Nom travailleur", m.group(1).strip() if m else None))

    # Worker address: right-column street + "<cp> <ville>". The employer
    # street sits on the same flattened lines (left column), so we take the
    # LAST street match on the relevant lines (right column = rightmost),
    # and the first non-CEDEX city line.
    streets = re.findall(
        r"(\d+\s+(?:rue|avenue|av|bd|boulevard|impasse|place|chemin)\b.*?)"
        r"(?=\s+\d+\s+(?:rue|avenue|av|bd|boulevard|impasse|place|chemin)\b|\n|$)",
        joined, re.IGNORECASE)
    street_val = streets[-1].strip() if streets else ""
    city_val = None
    for m in re.finditer(r"\b(\d{5}\s+[A-ZÉÈ].*?)(?=\n|$)", joined):
        if "CEDEX" not in m.group(1):
            city_val = m.group(1).strip()
            break
    fields.append(("Adresse travailleur", " ".join(x for x in (street_val, city_val) if x) or None))

    return fields


def _extract_contract_block(joined: str) -> list[tuple[str, str | None]]:
    # Contract / status column (right side of the header). Several rows pack
    # two labels ("Statut catégoriel ... Position", "Service ... Coefficient"),
    # so those values are bounded by the next label. "Niveau" and "Echelon"
    # are blank on this payslip: [ \t] (not \s) keeps the capture on the same
    # line so an empty value resolves to None instead of eating the next line.
    fields = []

    def add(label, pattern, flags=0):
        m = re.search(pattern, joined, flags)
        fields.append((label, m.group(1).strip() if m else None))

    def add_bounded(label, pattern, flags=0):
        m = re.search(pattern, joined, flags)
        value = m.group(1).strip() if m else None
        fields.append((label, value or None))

    add("Date d'entrée", r"Date d'entrée\s*:\s*(\S+)")
    add("Date ancienneté", r"Date ancienneté\s*:\s*(\S+)")
    add("Nature d'emploi", r"Nature d'emploi\s*:?\s*(.+)")
    add_bounded("Statut catégoriel", r"Statut cat[ée]goriel\s*:\s*(.*?)\s*Position")
    add_bounded("Position", r"Position[ \t]*:?[ \t]*([^\n]*)")
    add_bounded("Niveau", r"Niveau[ \t]*:?[ \t]*([^\n]*)")
    add("N° S.S.", r"N[°ºo]\s*S\.?\s*S\.?\s*:\s*(\S+)")
    add_bounded("Echelon", r"Echelon[ \t]*:?[ \t]*([^\n]*)")
    add_bounded("Service", r"Service\s*:\s*(.*?)\s*Coefficient")
    add("Coefficient", r"Coefficient\s*:?\s*(\S+)")
    add("CCN", r"CCN\s*:\s*(.+)")

    return fields


def _extract_header_refs(joined: str) -> list[tuple[str, str | None]]:
    fields = []

    # Pay period: "<date> au : <date>".
    m = re.search(r"(\d{2}/\d{2}/\d{4})\s+au\s*:?\s*(\d{2}/\d{2}/\d{4})", joined)
    fields.append(("Période de paye", f"{m.group(1)} au {m.group(2)}" if m else None))

    # Bulletin number: "BULLETIN DE PAYE N° <n>" (often mangled on scans).
    m = re.search(r"BULLETIN DE PAYE\s+N[°ºo]\s*(\d+)", joined, re.IGNORECASE)
    fields.append(("Bulletin n°", m.group(1) if m else None))

    return fields


def _extract_footer_block(joined: str) -> list[tuple[str, str | None]]:
    # Bottom block: payment recap + cumuls + leave counters. Layout and
    # column labels differ between the scanned (Tranche A/B, no leave
    # rows) and digital (Tranche 1/2, leave rows present) versions, and
    # the scan's cumul grid is badly OCR-scrambled - so cumul/leave values
    # are taken positionally and numbered, not named. Missing rows just
    # produce fewer fields. No maths, values kept raw.
    fields = []

    def add(label, pattern, flags=0):
        m = re.search(pattern, joined, flags)
        fields.append((label, m.group(1).strip() if m else None))

    # The last "... , .. EUR" amount on the page is the net paid.
    nets = re.findall(r"([\d  .]+,\d{2})\s*EUR", joined)
    fields.append(("Net à payer (EUR)", nets[-1].strip() if nets else None))

    add("Mode de paiement", r"Paiement par\s*:?\s*(.+)")
    add("Date de paiement", r"Date de paiement\s*:?\s*(\S+)")
    add("Banque", r"Banque\s*:?\s*(.+)")
    add("IBAN", r"IBAN\s*:?\s*(\S+)")
    add("Coût employeur", r"Co[ûu]t\s*(?:employeur)?\s*:?\s*([\d  .]+,\d{2})")
    add("Allègement cotisations", r"All[èe]gement\s*(?:cotisations)?\s*:?\s*(-?[\d  .]+,\d{2})")

    # Leave counters (Acquis / Pris / Reste): 4 columns (N-1, N, Anc., RTT).
    # Cap at 4 so the right-hand column glued on ("Allègement", "Coût") is
    # dropped rather than captured as a 5th value.
    for tag in ("Acquis", "Pris", "Reste"):
        m = re.search(rf"^{tag}\s+(.+)$", joined, re.MULTILINE)
        if m:
            for i, value in enumerate(NUMBER_RE.findall(m.group(1))[:4], start=1):
                fields.append((f"Congés {tag} {i}", value))

    return fields


# --- earnings table ---------------------------------------------------------
#
# Generic extraction over the flattened text: this template has no rubric
# codes, so each row is anchored on its own label. A row is any non-empty
# line carrying at least one amount; its label (all text once amounts and
# OCR separators are stripped) is used as the row key, and the amounts are
# numbered in reading order ("Montant N"). Their exact column
# (Base/Taux/Gain/Retenue/part patronale) can't be told apart from
# flattened text once empty cells are dropped, so they're not named.
#
# Section titles ("Santé", "Retraite"...) carry no amount and are skipped.
# Subtotal rows ("Total Brut SS", "Net à payer"...) are ordinary rows.

NUMBER_RE = re.compile(r"-?\d{1,3}(?:[  .]\d{3})*,\d{2,3}|-?\d+,\d{2,3}")

# Known column labels of the bottom cumul grid, longest first so multi-word
# ones ("Plafond S.S.") match before their prefixes. Both version variants
# (Tranche A/B and Tranche 1/2) are listed; only those present are used.
_CUMUL_COLUMNS = [
    "Plafond S.S.", "Heures trav.", "Jours trav.", "Salaire brut",
    "Net imposable", "Charges sal.", "Charges pat.",
    "Tranche A", "Tranche B", "Tranche 1", "Tranche 2",
]


def _split_cumul_header(header: str) -> list[str]:
    """Split the cumul grid header row into its column labels, in reading
    order. Labels contain internal spaces/dots, so they're located by known
    label text rather than by whitespace splitting; the OCR variant actually
    present (Tranche A/B vs 1/2) is picked up automatically."""
    found = []
    for label in _CUMUL_COLUMNS:
        pos = header.find(label)
        if pos != -1:
            found.append((pos, label))
    found.sort()
    return [label for _, label in found]

# The table starts after the CCN line and ends at the "1 676,54 EUR" /
# Net à payer recap block below it.
_SECTION_START = "CCN"
_SECTION_END_MARKERS = ("EUR", "Paiement par", "Allegement", "Allègement cot")


def extract_earnings_table(text: str):
    """Extract the earnings table (libellé + amounts) from the flattened
    text. Returns (table_lines, summary_lines):
      - table_lines: (label, "Libellé"|"Montant N", value), one entry per
        cell. The label doubles as the row key (no rubric code on this
        template). Amounts are numbered in reading order - their column
        can't be recovered reliably from flattened text.
      - summary_lines: always [] (subtotals are kept inline as normal rows).
    Returns ([], []) if the table section isn't found.
    """
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    start = next((i for i, line in enumerate(lines) if line.startswith(_SECTION_START)), None)
    if start is None:
        return [], []
    section = lines[start + 1:]

    table_lines = []
    for line in section:
        if any(mark in line for mark in _SECTION_END_MARKERS):
            break
        amounts = NUMBER_RE.findall(line)
        if not amounts:
            continue  # section title or noise: no amount, skipped
        label = NUMBER_RE.sub(" ", line)
        for ch in "|])":
            label = label.replace(ch, " ")
        label = " ".join(label.split())
        if not label:
            continue
        table_lines.append((label, "Libellé", label))
        for i, amount in enumerate(amounts, start=1):
            table_lines.append((label, f"Montant {i}", amount))

    # Bottom cumul grid (a separate table), appended at the very end so it
    # renders after the main table. Its header row ("Plafond S.S. Heures
    # trav. ... Charges pat.") names the columns; each column value is
    # crossed with the "Mois" / "Année" rows -> "Synthèse", "<col> (mois)".
    # If the header is missing/unreadable (some scans), fall back to numbered
    # "Montant N". Column labels differ by version (Tranche A/B vs 1/2) but
    # are read from the header itself, so no hard-coded list. Values raw.
    all_text = "\n".join(lines)
    header = re.search(r"^(Plafond S\.?S\.?.*Charges pat\.?)\s*$", all_text, re.MULTILINE)
    columns = _split_cumul_header(header.group(1)) if header else None

    for tag, fr in (("Mois", "mois"), ("Année", "année")):
        m = re.search(rf"^{tag}\s+(.+)$", all_text, re.MULTILINE)
        if not m:
            continue
        values = NUMBER_RE.findall(m.group(1))
        if columns and len(columns) == len(values):
            for col, value in zip(columns, values):
                table_lines.append(("Synthèse", f"{col} ({fr})", value))
        else:
            for i, value in enumerate(values, start=1):
                table_lines.append((tag, f"Montant {i}", value))

    return table_lines, []