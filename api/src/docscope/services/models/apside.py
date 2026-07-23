# api/src/docscope/services/models/apside.py
#
# Field extraction rules for "Apside": a French payslip PDF (bulletin de
# paie, template Apside/APSIDE TOP SAS). This template is a flattened scan
# (no embedded text layer - see extraction.py's OCR fallback), so labels and
# values keep the reading order of the visual layout but some same-line
# pairs come out reordered (SIRET / NAF swap vs. the visual left-to-right
# order), some words get glued together (e.g. "Paiementle"), and blank
# lines inside blocks get dropped. Every rule anchors on its own label
# independently, never on token order.
#
# Two independent parts (same public interface as the other models):
#   - extract_fields(): single key/value fields (employer, header, contract)
#   - extract_earnings_table(): the "Désignation" table - not rebuilt yet.

import re
import unicodedata


def extract_fields(text: str) -> list[tuple[str, str | None]]:
    """Extract the fixed set of key/value fields from an Apside payslip."""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    joined = "\n".join(lines)

    fields = []
    fields += _extract_employer_block(joined)
    fields += _extract_header_refs(joined)
    fields += _extract_contract_block(joined)
    fields += _extract_worker_identity_block(joined)
    return fields


def _extract_employer_block(joined: str) -> list[tuple[str, str | None]]:
    fields = []

    def add(label, pattern, flags=0):
        m = re.search(pattern, joined, flags)
        fields.append((label, m.group(1).strip() if m else None))

    # Employer name: first line of the document / header block.
    add("Nom employeur", r"^(APSIDE.+)$", re.MULTILINE)

    # Address: the lines after the name, up to (but not including) the
    # NAF / SIRET labels. The name line itself must be bounded to a single
    # line ([^\n]* not .+) - otherwise it's greedy under DOTALL and matches
    # the NAF/SIRET anchor furthest away instead of the closest one.
    m = re.search(r"^APSIDE[^\n]*\n(.+?)\n(?:NAF|SIRET)\b", joined, re.DOTALL)
    address = " ".join(m.group(1).split("\n")) if m else None
    fields.append(("Adresse employeur", address))

    add("SIRET", r"SIRET\s+(\d{9}\s+\d{5})")
    add("NAF", r"NAF\s+(\S+)")

    return fields


def _extract_header_refs(joined: str) -> list[tuple[str, str | None]]:
    fields = []

    def add(label, pattern, flags=0):
        m = re.search(pattern, joined, flags)
        fields.append((label, m.group(1).strip() if m else None))

    add("Période", r"P[ée]riode du\s+(.+)")
    add("Date de paiement", r"Paiement\s*le\s+(\S+)")
    add("Mode de paiement", r"Paiement\s*le\s+\S+\s+par\s+(.+)")
    add("Identifiant", r"Identifiant\s+(\S+)")
    add("Ancienneté", r"Anciennet[ée]\s+(\S+)")

    return fields


def _extract_contract_block(joined: str) -> list[tuple[str, str | None]]:
    fields = []

    def add(label, pattern, flags=0):
        m = re.search(pattern, joined, flags)
        fields.append((label, m.group(1).strip() if m else None))

    # URSSAF number: a trailing " 2" fragment sometimes follows on the same
    # line - (\S+) stops at the first space, keeping only the number.
    add("Cotisations URSSAF", r"Cotisations [àa] URSSAF\s+(\S+)")

    # Convention collective: code + label, label continues on the next
    # (label-less) line - captured as two lines and rejoined.
    m = re.search(r"Conv\.\s*coll\.\s+(.+\n.+)", joined)
    convention = " ".join(m.group(1).split("\n")) if m else None
    fields.append(("Convention collective", convention))

    def add_bounded(label, pattern, flags=0):
        m = re.search(pattern, joined, flags)
        value = m.group(1).strip() if m else None
        fields.append((label, value or None))

    # Niveau / Coefficient: two labels packed on one line ("Niveau
    # Coefficient"), both blank on this payslip. Niveau is bounded by the
    # Coefficient label; Coefficient is the last token on its line - [^\n]*
    # keeps the capture on the same line so an empty value resolves to None
    # instead of eating the next line ("Emploi STAGIAIRE").
    add_bounded("Niveau", r"Niveau\s+(.*?)\s*Coefficient")
    add_bounded("Coefficient", r"Coefficient[ \t]*([^\n]*)")

    add("Emploi", r"Emploi\s+(.+)")

    # Catégorie / Section: two labels on one line - Catégorie bounded by
    # the Section label, Section is the value after it.
    add_bounded("Catégorie", r"Cat[ée]gorie\s+(.*?)\s+Section")
    add("Section", r"Section\s+(\S+)")

    # Matricule s.s. (French NIR): 13 digits + a 2-digit control key. The
    # key is deterministic (97 - NIR mod 97), so instead of trusting the
    # flattening to keep the two fragments together, we recompute it: the
    # key is only appended when it matches, which both validates the OCR
    # read of the NIR and guarantees the two fragments belong together.
    # If the key doesn't check out, keep the 13 digits alone (not guessed).
    m = re.search(r"Matricule s\.?s\.?\s+(\d{13})\s+(\d{2})", joined)
    if m and int(m.group(2)) == 97 - (int(m.group(1)) % 97):
        matricule = f"{m.group(1)} {m.group(2)}"
    else:
        m13 = re.search(r"Matricule s\.?s\.?\s+(\d{13})", joined)
        matricule = m13.group(1) if m13 else None
    fields.append(("Matricule sécurité sociale", matricule))

    # Bank details (RIB), split into its four standard parts. The block
    # reads as "C.B 20041 C.G 01003 n° cpte 0709943J024 09": code banque
    # (5), code guichet (5), account number (11, alphanumeric), RIB key (2).
    add("Code banque", r"C\.?B\s+(\d{5})")
    add("Code guichet", r"C\.?G\s+(\d{5})")
    add("N° de compte", r"n[°ºo]?\s*cpte\s+(\S+)")
    add("Clé RIB", r"n[°ºo]?\s*cpte\s+\S+\s+(\d{2})\b")

    add("Domiciliation", r"Domiciliation\s+(.+)")

    return fields


def _extract_worker_identity_block(joined: str) -> list[tuple[str, str | None]]:
    # Worker name and street only. Both the civility line ("M COUTANT
    # PIERRE") and the street line right after it read reliably. The postal
    # code / city line does NOT: it sits at the same page height as the
    # RTT/Congés table on the left, so flattening glues them together and
    # OCR mangles the result ("eo ORÉEANS") - not extracted (not guessed).
    #
    # The "M"/"Mme" civility anchor is intentionally weak but safe here: the
    # "Matricule" line above is already consumed and no other line starts
    # with a bare "M". Revisit this anchor if it ever captures the wrong line.
    fields = []

    m = re.search(r"^M\.?\s+(.+)$", joined, re.MULTILINE)
    fields.append(("Nom travailleur", m.group(1).strip() if m else None))

    m = re.search(r"^M\.?\s+.+\n(.+)$", joined, re.MULTILINE)
    fields.append(("Adresse travailleur", m.group(1).strip() if m else None))

    return fields


# --- totals / cumuls block (bottom-right grid) ------------------------------
#
# A three-part grid at the bottom of the page, heavily mangled by OCR:
#   - hours worked (Période / Année)
#   - a 5-column cumul table (Brut fiscal / Base SS / Charges patronales /
#     Charges salariales / Net imposable) with two rows: Période and Année
#   - the Net à payer
# The column labels are destroyed by OCR ("Ch nales", "Chi salariales"), so
# nothing anchors on them - the values are read positionally instead, off
# two reliable lines:
#   - the "Heures travaillées" line carries, in order: hours worked
#     (période, année), the 5 Période cumuls, then the Net à payer.
#   - the last line of the block carries the 5 Année cumuls as its last 5
#     numbers (leading OCR junk / heures supp. varies and is ignored).
# Heures supplémentaires are not extracted: always 0,00 here and their OCR
# form is unreadable ("Homes supe'", "o00 ooo") - accepted, not guessed.

CUMUL_COLUMNS = [
    "Brut fiscal", "Base sécurité sociale", "Charges patronales",
    "Charges salariales", "Net imposable",
]


def _extract_totals_block(joined: str) -> list[tuple[str, str | None]]:
    # All fields here are bulletin-level totals: their labels are prefixed
    # "Synthèse ..." on the way out (see the return below).
    empty = (
        [("Heures travaillées (période)", None), ("Heures travaillées (année)", None)]
        + [(f"{c} (période)", None) for c in CUMUL_COLUMNS]
        + [(f"{c} (année)", None) for c in CUMUL_COLUMNS]
        + [("Net à payer", None)]
    )

    lines = joined.splitlines()
    worked = next((line for line in lines if "Heures travaill" in line), None)
    # Last line of the block: the one carrying "Euros" (the Net à payer unit).
    annee_line = next((line for line in lines if "Euros" in line), None)
    if worked is None or annee_line is None:
        return empty

    head = NUMBER_RE.findall(worked)   # ht_per, ht_an, 5x période, net
    tail = NUMBER_RE.findall(annee_line)
    if len(head) < 8 or len(tail) < 5:
        return empty

    fields = [
        ("Heures travaillées (période)", head[0]),
        ("Heures travaillées (année)", head[1]),
    ]
    for col, value in zip(CUMUL_COLUMNS, head[2:7]):
        fields.append((f"{col} (période)", value))
    # Année cumuls: the last 5 numbers of the block's final line (any leading
    # OCR junk before them is ignored).
    for col, value in zip(CUMUL_COLUMNS, tail[-5:]):
        fields.append((f"{col} (année)", value))
    fields.append(("Net à payer", head[7]))

    return fields
    # (each label prefixed "Synthèse — ..." to match the app's field display)


# --- "Désignation" earnings table -------------------------------------------
#
# Pure regex over flattened text (this template is an OCR'd scan), one pass
# over the table's code-prefixed lines plus its two inline subtotals.
#
# Column naming is done only where it can be *proven*, never guessed:
#   - Cotisation lines are recognised by the payslip invariant
#     Retenue = Base x Taux / 100. When a line's numbers read as Base
#     followed by (Taux, Retenue) pairs that each satisfy this invariant,
#     the columns are named Base / Taux / Retenue. A line carrying two
#     valid pairs has both an employee and an employer part: the left pair
#     (salariale) gets the "(sal)" suffix, the right one (patronale)
#     "(patr)" - this is the one case where the sal/patr split can be told
#     apart from flattened text (by pair order = layout order). A line with
#     a single pair can't: its retenue could be either part, so no suffix.
#   - Gain lines (indemnités, primes, gratifications) don't satisfy the
#     cotisation invariant. Their trailing amount is the "Gain (sal)"
#     column; any leading numbers stay numbered ("Montant N") because
#     Nombre vs Base can't be told apart reliably from flattened text.
#
# Section anchor: "Domiciliation" is the last reliable label before the
# table (the table's own OCR'd header row is too mangled to anchor on).

NUMBER_RE = re.compile(r"-?\d{1,3}(?:[ .]\d{3})*,\d{2,3}|\d+,\d{2,3}")
# OCR renders the column separator as a literal "|" on some rows and as
# plain whitespace on others - both accepted between the code and the rest.
# A code is 1-4 digits NOT immediately followed by another digit or "/",
# so a detail date line ("09/05/14") isn't mistaken for a code. The
# trailing (?![\d/]) anchors on the whole leading number: it forbids the
# regex from backtracking to a shorter digit run (matching "0" out of "09").
CODE_LINE_RE = re.compile(r"^(\d{1,4})(?![\d/])\s*\|?\s*(.*)$")
# A code-less detail line whose content starts like a date (dd/mm/yy).
DATE_DETAIL_RE = re.compile(r"^\d{1,2}/\d{1,2}/\d{2,4}\b")

INVARIANT_TOLERANCE = 0.015  # euro: retenue vs base*taux/100, absorbs cents rounding

SUMMARY_LABELS = ["Total Brut", "Total Cotisations"]


def _to_float(s: str) -> float:
    """French number ("1.234,56" / "10,00") -> float."""
    return float(s.replace(" ", "").replace(".", "").replace(",", "."))


def _name_cotisation_line(nums: list[str]) -> list[tuple[str, str]] | None:
    """Read `nums` as Base + (Taux, Retenue) pairs, each validated by the
    invariant Retenue = Base*Taux/100. Returns named (column, value) cells,
    or None if the numbers don't cleanly fit that shape (i.e. not a
    cotisation line - probably a gain line)."""
    if len(nums) < 3:
        return None
    base = _to_float(nums[0])
    if base == 0:
        return None

    pairs = []
    i = 1
    while i + 1 <= len(nums) - 1:
        taux, retenue = nums[i], nums[i + 1]
        if abs(base * _to_float(taux) / 100 - _to_float(retenue)) <= INVARIANT_TOLERANCE:
            pairs.append((taux, retenue))
            i += 2
        else:
            return None
    # Every number after Base must have been consumed as a clean pair.
    if i != len(nums) or not pairs:
        return None

    cells = [("Base", nums[0])]
    if len(pairs) == 1:
        # Single part only: salariale vs patronale is indistinguishable
        # from flattened text (both parts obey the same invariant) - no suffix.
        cells.append(("Taux", pairs[0][0]))
        cells.append(("Retenue", pairs[0][1]))
    else:
        # Two parts: left = salariale, right = patronale (table layout order).
        suffixes = ["(sal)", "(patr)"]
        for idx, (taux, retenue) in enumerate(pairs):
            suffix = suffixes[idx] if idx < len(suffixes) else str(idx + 1)
            cells.append((f"Taux {suffix}", taux))
            cells.append((f"Retenue {suffix}", retenue))
    return cells


def _parse_table_line(code: str, rest: str) -> list[tuple[str, str, str]]:
    nums = NUMBER_RE.findall(rest)
    # Label: everything that isn't a number or the stray "|" separator.
    label = " ".join(NUMBER_RE.sub(" ", rest).replace("|", " ").split())

    results = []
    if label:
        results.append((code, "Libellé", label))

    named = _name_cotisation_line(nums)
    if named is not None:
        for column, value in named:
            results.append((code, column, value))
    else:
        # Not a cotisation line (fails the Base*Taux/100 invariant): could be
        # a gain (indemnité, prime) or a retenue (absence). These are
        # indistinguishable from flattened text - "10,50 2,83 29,72" (absence,
        # a retenue) and "21,00 5,00 105,00" (indemnité, a gain) have the same
        # shape - so the amounts are numbered neutrally rather than named
        # Gain/Retenue (not guessed).
        for i, value in enumerate(nums, start=1):
            results.append((code, f"Montant {i}", value))

    return results


def _strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


def _extract_summary_lines(lines: list[str]) -> list[tuple[str, str, str | None]]:
    """The two inline subtotals of the earnings table (Total Brut, Total
    Cotisations), returned as (label, value) pairs. "Total Cotisations"
    carries two values (part salariale then patronale, left-to-right layout
    order) -> "Total Cotisations (sal)" / "Total Cotisations (patr)"."""
    section = "\n".join(lines)
    section_norm = _strip_accents(section)
    results = []
    for label in SUMMARY_LABELS:
        m = re.search(re.escape(_strip_accents(label)) + r"\s+(.+)", section_norm)
        if not m:
            results.append((label, "Montant", None))
            continue
        values = NUMBER_RE.findall(m.group(1))
        if len(values) <= 1:
            results.append((label, "Montant", values[0] if values else None))
        else:
            # Two values = part salariale then patronale (left-to-right layout).
            results.append((label, "Montant (sal)", values[0]))
            results.append((label, "Montant (patr)", values[1]))
    return results


def extract_earnings_table(text: str):
    """Extract the "Désignation" earnings table from the flattened text.

    Returns (table_lines, summary_lines):
      - table_lines: (code, column, value) - one entry per non-empty cell.
        Cotisation lines (validated by the Retenue = Base*Taux/100 invariant)
        get named columns (Base / Taux / Retenue, with (sal)/(patr) suffixes
        when both parts are present). Any other line's amounts are numbered
        ("Montant N") - gain vs retenue can't be told apart from flattened
        text. Code-less detail rows (absence dates) attach to the code above
        as "Détail" cells.
      - summary_lines: ("Synthèse", label, value) for Total Brut / Total
        Cotisations (the latter split into "... 1"/"... 2" = sal/patr).
    Returns ([], []) if the table isn't found.
    """
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    start = next((i for i, line in enumerate(lines) if "Domiciliation" in line), None)
    if start is None:
        return [], []
    section_lines = lines[start + 1:]

    # The table ends where the bottom totals grid begins. That grid is
    # anchored on the "Heures travaillées" line (or its "Brut Fiscal"
    # header); everything from there on (hours, cumuls, Net à payer, legal
    # footer) belongs to _extract_totals_block, not to the table - cutting
    # here stops those lines from being mistaken for rubrics/detail.
    end = next(
        (i for i, line in enumerate(section_lines)
         if "Heures travaill" in line or "Brut Fiscal" in line),
        len(section_lines),
    )
    section_lines = section_lines[:end]

    table_lines = []
    last_code = None
    for line in section_lines:
        # "Total Brut" / "Total Cotisations": ordinary table rows whose N°
        # column is empty. Keyed on their own label (like a code), with the
        # amount(s) after them. Total Cotisations carries two values
        # (salariale then patronale, left-to-right layout order).
        total_label = next(
            (lbl for lbl in SUMMARY_LABELS
             if _strip_accents(line).lower().startswith(_strip_accents(lbl).lower())),
            None,
        )
        if total_label is not None:
            # Amounts after the label: decimals ("5,22") or bare integers
            # ("234", OCR having dropped the comma - kept as-is). Two values
            # = retenue salariale then patronale (left-to-right layout).
            values = re.findall(r"-?\d{1,3}(?:[ .]\d{3})*,\d{2,3}|\d+", line[len(total_label):])
            if len(values) <= 1:
                table_lines.append((total_label, "Montant", values[0] if values else None))
            else:
                table_lines.append((total_label, "Montant (sal)", values[0]))
                table_lines.append((total_label, "Montant (patr)", values[1]))
            last_code = None
            continue

        m = CODE_LINE_RE.match(line)
        if m:
            last_code = m.group(1)
            table_lines += _parse_table_line(m.group(1), m.group(2))
            continue
        # Code-less line following a coded one. Only genuine detail rows are
        # attached: the individual absence dates ("09/05/14", "19/05/14
        # (0,5)") under a code like 650. Anything else (the totals grid
        # header, OCR noise, the legal footer) is NOT rubric detail and is
        # ignored - matching a date pattern is the gate.
        if last_code is not None and DATE_DETAIL_RE.match(line):
            table_lines.append((last_code, "Détail", line.strip()))

    # Bulletin totals (hours, cumuls, Net à payer) go at the very end, as
    # ordinary table rows so the app renders them below the table - keyed
    # on "Synthèse" with the field name as the column.
    for label, value in _extract_totals_block(text):
        table_lines.append(("Synthèse", label, value))

    return table_lines, []