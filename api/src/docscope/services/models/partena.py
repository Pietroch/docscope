# api/src/docscope/services/models/partena.py
#
# Field extraction rules for "Partena": a Belgian payslip PDF ("Feuille de
# paie" / "Loonbrief", secrétariat social Partena n° 300). Layout:
#   page 1  header (employer left / period right, then the worker block),
#           box "Travailleur", table "Prestations et avantages",
#           table "Calcul", payment line "sur IBAN : ...";
#   page 2  daily grid "Prestations journalières", "Légende des codes",
#           "Montants pour information", "Informations générales".
#
# Three traits of this template drive every rule below:
#
#   1. Bilingual documents. Some payslips carry the Dutch version (page 1-2,
#      "Loonbrief") followed by the French one (page 3-4, "Feuille de
#      paie"). Only the French pages are kept - see _french_pages().
#   2. The text extraction spaces out the digits of the amounts
#      ("1. 4 8 0 , 7 7", "8, 0 0", "- 1 9 , 6 2") and, conversely, drops
#      the spaces of some labels ("RueJulesCockx8-10",
#      "Période:du01/04/2022au30/04/2022"). Amounts are therefore matched
#      with a space-tolerant pattern and de-spaced on capture (_amount());
#      label anchors are built with _lbl(), which makes their inner spaces
#      optional. Nothing else is touched: no maths, no OCR fixing.
#   3. The tables keep their box-drawing characters, so the columns of
#      "Prestations et avantages" and the two halves of the daily grid are
#      recoverable by splitting on "│" - no guessing needed there.
#
# Two independent parts (same public interface as ucm/apside/mosica/sdworx):
#   - extract_fields(): single key/value fields
#   - extract_earnings_table(): "Prestations et avantages" + "Calcul", plus
#     the page-2 blocks

import re

# Amounts: digits may be separated by spaces, hence the tolerant pattern.
# A comma is required, which keeps identifiers out ("960318 468-59",
# "200.00", "002.00").
AMOUNT_RE = re.compile(r"-?\s*\d[\d.\s]*,\s*\d(?:\s*\d)*")
# Rubric codes of the daily grid: "00 2 . 0 0" -> "002.00" (dot, no comma).
CODE_RE = re.compile(r"\d[\d\s]*\.\s*\d[\d\s]*\d")
BOX_CHARS = "┌┐└┘├┤┬┴┼─│"


def _lbl(label: str) -> str:
    """Label pattern whose inner spaces are optional, so the same anchor
    matches "Lieu d'occupation" and its glued form "Lieud'occupation"."""
    return r"\s*".join(re.escape(word) for word in label.split())


def _amount(raw: str | None) -> str | None:
    """De-space a captured amount ("1. 4 8 0 , 7 7" -> "1.480,77")."""
    return re.sub(r"\s+", "", raw) if raw else None


def _amounts(text: str) -> list[str]:
    return [_amount(m.group()) for m in AMOUNT_RE.finditer(text)]


def _cells(line: str) -> list[str]:
    """Split a boxed table row into its cells on the "│" separators."""
    return [cell.strip() for cell in line.strip().strip("│").split("│")]


def _clean(text: str) -> str:
    """Strip box-drawing characters and collapse whitespace."""
    return " ".join(text.translate({ord(c): " " for c in BOX_CHARS}).split())


# --- language selection -----------------------------------------------------

# Each page ends with the "*&*" barcode footer, which is what pages are cut
# on. French pages are recognised by their title; Dutch ones say
# "Loonbrief" and are dropped.
_PAGE_END_RE = re.compile(r"^\*&\*")
_FRENCH_MARKER = re.compile(_lbl("Feuille de paie"), re.IGNORECASE)


def _french_pages(text: str) -> str:
    """Keep only the French pages of a bilingual payslip. If no page
    carries the French title (single-language document extracted some other
    way), everything is kept rather than returning nothing."""
    pages, current = [], []
    for line in text.splitlines():
        current.append(line)
        if _PAGE_END_RE.match(line.strip()):
            pages.append("\n".join(current))
            current = []
    if current:
        pages.append("\n".join(current))

    french = [page for page in pages if _FRENCH_MARKER.search(page)]
    return "\n".join(french) if french else text


def extract_fields(text: str) -> list[tuple[str, str | None]]:
    """Extract the fixed set of key/value fields from a Partena payslip."""
    lines = [line.rstrip() for line in _french_pages(text).splitlines() if line.strip()]
    joined = "\n".join(lines)

    fields = []
    fields += _extract_employer_block(lines, joined)
    fields += _extract_period_block(joined)
    fields += _extract_worker_block(lines, joined)
    fields += _extract_worker_data_block(lines)
    fields += _extract_payment_block(joined)
    fields += _extract_general_info_block(lines)
    return fields


# --- header -----------------------------------------------------------------

# Right-hand column of the header, glued onto the employer lines by the
# flattening: used as cut points only.
_HEADER_CUT_RE = re.compile(r"\s*(?:" + "|".join((
    _lbl("Feuille de paie"), _lbl("Période"), _lbl("Periode"),
    _lbl("Etablie le"), _lbl("Etabli le"),
)) + r")")


def _extract_employer_block(lines, joined) -> list[tuple[str, str | None]]:
    fields = []

    # Employer name: first line, right-hand title cut off.
    name = _HEADER_CUT_RE.split(lines[0], maxsplit=1)[0].strip() if lines else None
    fields.append(("Nom employeur", name or None))

    # Address: the lines between the name and the "N° d'entreprise" line,
    # left column only.
    address = []
    for line in lines[1:]:
        if re.match(_lbl("N° d'entreprise"), line) or re.match(_lbl("N°d'entreprise"), line):
            break
        cut = _HEADER_CUT_RE.split(line, maxsplit=1)[0].strip()
        if cut:
            address.append(cut)
    fields.append(("Adresse employeur", " ".join(address) or None))

    m = re.search(_lbl("N° d'entreprise") + r"[ \t]*(\S+)", joined)
    fields.append(("N° d'entreprise", m.group(1) if m else None))

    m = re.search(_lbl("Lieu d'occupation") + r"[ \t]*([^\n]*)", joined)
    fields.append(("Lieu d'occupation", (m.group(1).strip() if m else None) or None))

    return fields


def _extract_period_block(joined) -> list[tuple[str, str | None]]:
    fields = []

    # "Période : du <date> au <date>" - the words are glued to the dates.
    m = re.search(_lbl("Période") + r"\s*:?\s*du\s*(\d{2}/\d{2}/\d{4})\s*au\s*(\d{2}/\d{2}/\d{4})",
                  joined)
    fields.append(("Période", f"{m.group(1)} au {m.group(2)}" if m else None))

    m = re.search(_lbl("Etablie le") + r"\s*(\d{2}/\d{2}/\d{4})", joined)
    fields.append(("Date d'établissement", m.group(1) if m else None))

    return fields


def _extract_worker_block(lines, joined) -> list[tuple[str, str | None]]:
    # Worker block of the header: a reference line ("<dossier>/<worker>/<
    # journal>"), the "Confidentiel <ref>" line, then civility, name and
    # the two address lines. The block ends at the first box ("Travailleur").
    fields = []

    m = re.search(r"^(\d+/\d+/\d+)\s*$", joined, re.MULTILINE)
    fields.append(("Référence document", m.group(1) if m else None))

    m = re.search(_lbl("Confidentiel") + r"[ \t]*(\S+)", joined)
    fields.append(("Référence confidentiel", m.group(1) if m else None))

    start = next((i for i, line in enumerate(lines)
                  if re.match(r"(?:Madame|Monsieur|Mademoiselle)\s*$", line.strip())), None)
    civility = lines[start].strip() if start is not None else None
    fields.append(("Civilité", civility))

    block = []
    if start is not None:
        for line in lines[start + 1:]:
            if line.lstrip().startswith(tuple(BOX_CHARS)):
                break
            block.append(line.strip())
    fields.append(("Nom travailleur", block[0] if block else None))
    fields.append(("Adresse travailleur", " ".join(block[1:3]) or None))

    return fields


# --- "Travailleur" box ------------------------------------------------------
#
# Two label/value columns flattened onto each line, and the labels are NOT
# in the same order from one payslip to the next ("Date d'entrée" sits in
# the left column on one, in the right column on another). So instead of
# anchoring each label on a fixed neighbour, every known label is located
# on the line and each value runs up to the start of the next label found -
# order-independent by construction.
#
# "Situation fiscale" can spill onto the next line as a parenthesised
# qualifier ("(cohabitant avec revenus)"); such a continuation line is
# appended to the last value read.

_WORKER_LABELS = [
    "Référence dossier", "N° journal des paies", "Statut", "Fonction",
    "Rémun. base", "Date d'entrée", "Catégorie Prof.", "Commission paritaire",
    "N° Reg. Nat.", "Situation fiscale",
]


def _extract_worker_data_block(lines) -> list[tuple[str, str | None]]:
    start = next((i for i, line in enumerate(lines)
                  if re.search(_lbl("Travailleur"), line)
                  and line.lstrip().startswith(tuple(BOX_CHARS))), None)
    values: dict[str, str] = {}
    last_right_label = None

    if start is not None:
        for line in lines[start + 1:]:
            clean = _clean(line)
            if not clean or clean == "Travailleur":
                continue
            hits = []
            for label in _WORKER_LABELS:
                m = re.search(_lbl(label), clean)
                if m:
                    hits.append((m.start(), m.end(), label))
            hits.sort()

            if not hits:
                # A line with no known label: a parenthesised qualifier of
                # the right-hand column, or the next box -> stop there.
                if clean.startswith("(") and last_right_label:
                    values[last_right_label] = (
                        values.get(last_right_label, "") + " " + clean).strip()
                    continue
                break

            # A parenthesised qualifier of the right-hand column can also
            # land at the end of a row that only has a left-hand label
            # ("Rémun. base 2.750,00 EUR (cohabitant avec revenus)"): it is
            # moved back to the right-hand value it continues.
            trailing = re.search(r"\s*(\([^()]*\))\s*$", clean)
            if trailing and len(hits) == 1 and last_right_label and not clean.startswith("("):
                values[last_right_label] = (
                    values.get(last_right_label, "") + " " + trailing.group(1)).strip()
                clean = clean[: trailing.start()]

            for i, (_, end, label) in enumerate(hits):
                stop = hits[i + 1][0] if i + 1 < len(hits) else len(clean)
                values.setdefault(label, clean[end:stop].strip())
            # Right-hand column = the last label of the row, when the row
            # carries two of them.
            if len(hits) > 1:
                last_right_label = hits[-1][2]

    return [(label, values.get(label) or None) for label in _WORKER_LABELS]


def _extract_payment_block(joined) -> list[tuple[str, str | None]]:
    # Payment line closing the "Calcul" table: "sur IBAN : <iban> <amount>".
    fields = []

    # Groups of 4 digits only, so the amount printed further right on the
    # same line is not swallowed.
    m = re.search(_lbl("IBAN") + r"\s*:\s*([A-Z]{2}\d{2}(?:\s?\d{4})*)", joined)
    fields.append(("IBAN", m.group(1).strip() if m else None))

    m = re.search(_lbl("Net à payer") + r"[ \t]*(" + AMOUNT_RE.pattern + ")", joined)
    fields.append(("Net à payer (EUR)", _amount(m.group(1)) if m else None))

    return fields


def _extract_general_info_block(lines) -> list[tuple[str, str | None]]:
    # "Informations générales" box (page 2): "<label> : <value>" per row.
    fields = []
    for label, anchor in (("Numéro ONSS", "Numéro ONSS"),
                          ("Cie Assurances Accident Travail",
                           "Cie Assurances Accident Travail")):
        value = None
        for line in lines:
            m = re.search(_lbl(anchor) + r"\s*:\s*(.*)$", _clean(line))
            if m:
                value = m.group(1).strip() or None
                break
        fields.append((label, value))
    return fields


# --- earnings tables --------------------------------------------------------
#
# Two tables, both keyed on their own label (this template has no rubric
# code on the earnings rows):
#
#   - "Prestations et avantages": a real boxed table, so the cells are split
#     on "│" -> Jours ou unités / Heures / Montants(EUR) / Montants
#     unitaires. The first cell holds the label plus, when present, the
#     "Jours ou unités" value (that column has no left border).
#   - "Calcul": flattened, one amount per row. Its two columns
#     (Montants(EUR) and Totaux(EUR)) can't be told apart from the text, but
#     the Totaux column only ever carries the known subtotal rows below, so
#     those go to summary_lines and everything else is an ordinary row.
#
# A label can legitimately appear twice (two "Jours et heures prestés" rows
# in one table, and again in the other table), so keys are prefixed with
# their table and suffixed with an occurrence number when needed.

_TOTAL_LABELS = (
    "Salaire brut total", "Cotisations ONSS", "Imposable", "Précompte",
    "Divers net", "Salaire net total", "Net à payer",
)

_PRESTATIONS_TITLE = _lbl("Prestations et avantages")
_CALCUL_TITLE = _lbl("Calcul")
# Rows that are section titles inside the tables, not data.
_SECTION_TITLES = ("Prestations", "Avantages", "Description")


class _Keyer:
    """Row keys: "<table> - <label>", numbered when a label repeats."""

    def __init__(self, table):
        self.table = table
        self.seen = {}

    def key(self, label):
        self.seen[label] = self.seen.get(label, 0) + 1
        suffix = "" if self.seen[label] == 1 else f" ({self.seen[label]})"
        return f"{self.table} - {label}{suffix}"


def _box_section(lines, title_pattern):
    """Lines of the section introduced by a boxed title, up to the next box
    title. Returns [] if the section isn't found."""
    start = None
    for i, line in enumerate(lines):
        if line.lstrip().startswith(tuple(BOX_CHARS)) and re.search(title_pattern, _clean(line)):
            start = i
            break
    if start is None:
        return []

    out = []
    for line in lines[start + 1:]:
        # A new box opens -> the section is over. This test comes first
        # because a frame line is empty once cleaned, so it would otherwise
        # be skipped as blank and the section would never end.
        if line.lstrip().startswith("┌"):
            break
        if set(line.strip()) <= set(BOX_CHARS + " "):
            continue  # frame line of the current box
        clean = _clean(line)
        if not clean or re.fullmatch(title_pattern, clean):
            continue
        out.append(line)
    return out


def _extract_prestations(lines):
    """"Prestations et avantages": boxed table, cells split on "│"."""
    rows = []
    keyer = _Keyer("Prestations")

    for line in _box_section(lines, _PRESTATIONS_TITLE):
        cells = _cells(line)
        if not cells:
            continue
        head = cells[0].strip()
        if not head or head in _SECTION_TITLES or head.startswith("Description"):
            continue

        # First cell: label + the "Jours ou unités" value (no left border).
        unit = AMOUNT_RE.search(head)
        label = _clean(head[: unit.start()] if unit else head)
        if not label:
            continue
        key = keyer.key(label)
        rows.append((key, "Libellé", label))
        if unit:
            rows.append((key, "Jours ou unités", _amount(unit.group())))

        for column, cell in zip(("Heures", "Montant EUR", "Montant unitaire"), cells[1:]):
            value = AMOUNT_RE.search(cell)
            if value:
                rows.append((key, column, _amount(value.group())))

    return rows


def _extract_calcul(lines):
    """"Calcul": flattened rows, one amount each. Known subtotals go to the
    summary; the payment line ("sur IBAN : ...") closes the table."""
    rows, summary = [], []
    keyer = _Keyer("Calcul")

    for line in _box_section(lines, _CALCUL_TITLE):
        clean = _clean(line)
        if clean.startswith("Description"):
            continue  # column header row
        if re.match(_lbl("sur IBAN"), clean):
            break  # payment line: closes the table (read by extract_fields)
        amounts = _amounts(clean)
        if not amounts:
            continue
        label = _clean(AMOUNT_RE.sub(" ", clean).replace("(", " ").replace(")", " "))
        # "(Salaire imposable) ( 2.419,02)" keeps its parentheses as a hint
        # that it is an informative row, not a column of the table.
        if clean.startswith("("):
            label = f"({label})"
        if not label:
            continue

        if label in _TOTAL_LABELS:
            summary.append(("Synthèse", label, amounts[0]))
            continue

        key = keyer.key(label)
        rows.append((key, "Libellé", label))
        rows.append((key, "Montant EUR", amounts[0]))

    return rows, summary


def _extract_legend(lines):
    """"Légende des codes": "<code> : <libellé>" pairs, two per row."""
    rows = []
    for line in _box_section(lines, _lbl("Légende des codes")):
        for cell in _cells(line):
            m = re.match(r"(" + CODE_RE.pattern + r")\s*:\s*(.+)$", cell.strip())
            if m:
                rows.append(("Légende", re.sub(r"\s+", "", m.group(1)), _clean(m.group(2))))
    return rows


def _extract_info_amounts(lines):
    """"Montants pour information": <label> <amount> pairs, two per row."""
    rows = []
    for line in _box_section(lines, _lbl("Montants pour information")):
        for cell in _cells(line):
            m = AMOUNT_RE.search(cell)
            if not m:
                continue
            label = _clean(cell[: m.start()])
            if label:
                rows.append(("Montants pour information", label, _amount(m.group())))
    return rows


def _extract_daily_grid(lines):
    """"Prestations journalières": 10 boxed columns = two identical halves
    (Date / Code / Heures / Occupat / Centre de frais). Both halves are read
    and keyed on their date; empty days ("-") produce no entry."""
    rows = []
    columns = ("Code", "Heures", "Occupation", "Centre de frais")

    for line in _box_section(lines, _lbl("Prestations journalières")):
        cells = _cells(line)
        if len(cells) < 5 or cells[0].startswith("Date"):
            continue
        for half in (cells[:5], cells[5:10]):
            if len(half) < 5:
                continue
            # The date can come out spaced out ("V e 1 5"): all spaces are
            # dropped, then one is put back between day name and number.
            date = re.sub(r"(?<=[A-Za-z])(?=\d)", " ", re.sub(r"\s+", "", half[0]))
            if not date or date == "-":
                continue
            key = f"Jour {date}"
            code = CODE_RE.search(half[1])
            if code:
                rows.append((key, "Code", re.sub(r"\s+", "", code.group())))
            hours = AMOUNT_RE.search(half[2])
            if hours:
                rows.append((key, "Heures", _amount(hours.group())))
            for column, cell in zip(columns[2:], half[3:5]):
                value = _clean(cell)
                if value and value != "-":
                    rows.append((key, column, value))
    return rows


def extract_earnings_table(text: str):
    """Extract the earnings tables of a Partena payslip. Only the French
    pages are read (see _french_pages).

    Returns (table_lines, summary_lines):
      - table_lines: (row key, column, value) - the "Prestations et
        avantages" rows ("Libellé"|"Jours ou unités"|"Heures"|"Montant EUR"|
        "Montant unitaire"), the "Calcul" rows ("Libellé"|"Montant EUR"),
        then the page-2 blocks: ("Légende", code, libellé),
        ("Montants pour information", label, amount) and the daily grid
        ("Jour <date>", "Code"|"Heures"|..., value).
      - summary_lines: ("Synthèse", label, value) for the "Totaux(EUR)"
        column of the "Calcul" table (Salaire brut total, Cotisations ONSS,
        Imposable, Précompte, Divers net, Salaire net total, Net à payer).
    Returns ([], []) if neither table is found.
    """
    lines = [line.rstrip() for line in _french_pages(text).splitlines() if line.strip()]

    prestations = _extract_prestations(lines)
    calcul, summary_lines = _extract_calcul(lines)
    if not prestations and not calcul:
        return [], []

    table_lines = prestations + calcul
    table_lines += _extract_legend(lines)
    table_lines += _extract_info_amounts(lines)
    table_lines += _extract_daily_grid(lines)

    return table_lines, summary_lines