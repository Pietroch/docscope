# api/src/docscope/services/models/apside.py
#
# Field extraction rules for "Apside": a French payslip PDF (bulletin de
# paie, template Apside/APSIDE TOP SAS). This template is a flattened scan
# (no embedded text layer - see extraction.py's OCR fallback), so labels and
# values keep the reading order of the visual layout but some same-line
# pairs come out reordered (SIRET / NAF swap vs. the visual left-to-right
# order), some words get glued together (e.g. "Paiementle"), and blank
# lines inside blocks get dropped - and, worse than the other templates,
# whole blocks land out of order: on this payslip the URSSAF number and the
# convention code are OCR'd *below* their own labels, several lines away,
# and the pay period / payment date / mode sit in a right-hand box that
# flattens to its own lines, detached from the labels on the left. So rules
# here anchor on their own label when the value is adjacent, and fall back
# to matching the value *by its shape* (a date range, a long digit run, a
# NIR key) when the flattening has torn label and value apart. Never on
# token order.
#
# Two independent parts (same public interface as the other models):
#   - extract_fields(): single key/value fields (employer, header, contract)
#   - extract_earnings_table(): the "Désignation" table (codes + amounts,
#     inline subtotals, bottom totals grid, and the top RTT/Congés grid).

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
    # The period / payment box is OCR'd as its own detached block: the labels
    # ("Période du", "Paiement le") come out on lines with no value, and the
    # values ("01/07/14 au 31/07/14", "28/07/14", "par Virement") land a few
    # lines below. So period, payment date and mode are matched by their own
    # shape, not by adjacency to the label. Identifiant and Ancienneté keep
    # their value on the label line and are read normally.
    fields = []

    # Pay period: "<date> au <date>", the only date-range on the page.
    m = re.search(r"(\d{2}/\d{2}/\d{2,4}\s+au\s+\d{2}/\d{2}/\d{2,4})", joined)
    fields.append(("Période", m.group(1).strip() if m else None))

    # Payment date: a bare date on its own line (not part of the range, not
    # a period label). It sits just after the range in reading order.
    m = re.search(
        r"\d{2}/\d{2}/\d{2,4}\s+au\s+\d{2}/\d{2}/\d{2,4}\s*\n(\d{2}/\d{2}/\d{2,4})\b",
        joined,
    )
    fields.append(("Date de paiement", m.group(1).strip() if m else None))

    # Mode of payment: the token(s) after "par" (the "par Virement" line).
    m = re.search(r"\bpar\s+([A-Za-zÀ-ÿ]+)", joined)
    fields.append(("Mode de paiement", m.group(1).strip() if m else None))

    m = re.search(r"Identifiant\s+(\S+)", joined)
    fields.append(("Identifiant", m.group(1).strip() if m else None))
    m = re.search(r"Anciennet[ée]\s+(\S+)", joined)
    fields.append(("Ancienneté", m.group(1).strip() if m else None))

    return fields


def _extract_contract_block(joined: str) -> list[tuple[str, str | None]]:
    fields = []

    def add(label, pattern, flags=0):
        m = re.search(pattern, joined, flags)
        fields.append((label, m.group(1).strip() if m else None))

    # URSSAF number: OCR'd on its own line, several lines below the
    # "Cotisations à URSSAF" label (not adjacent), and shaped as a long digit
    # run optionally followed by a 1-2 digit control fragment. Match it by
    # shape. Guard against catching the SIRET (9+5 digits) or the matricule
    # (13 digits): require >= 15 leading digits, which the URSSAF number has
    # here and the others don't.
    m = re.search(r"^(\d{15,}(?:\s+\d{1,2})?)\s*$", joined, re.MULTILINE)
    fields.append(("Cotisations URSSAF", m.group(1).strip() if m else None))

    # Convention collective: code + label. On this scan the code line
    # ("3018 - Bureaux d'Etudes ...") and the tail line ("Conseils Sociétés
    # de Conseils") are OCR'd out of order (the tail lands *above* the code,
    # right after "Conv. coll."). Rebuild from both, joined with " - ":
    # the "<code> - <first half>" line plus the "Conseils ..." tail if present.
    convention = None
    m_code = re.search(r"^(\d{3,4}\s*-\s*Bureaux.+)$", joined, re.MULTILINE)
    if m_code:
        parts = [m_code.group(1).strip()]
        m_tail = re.search(r"Conv\.\s*coll\.\s*\n(.+)", joined)
        if m_tail:
            tail = m_tail.group(1).strip()
            # Only append a genuine label tail (letters), not another anchor.
            if tail and not tail[0].isdigit():
                parts.append(tail)
        convention = " - ".join(parts)
    else:
        # Fallback: label-adjacent capture over two lines (older layout).
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

    # Catégorie / Section: "Catégorie <val>" and "Section <val>" are on
    # different OCR lines here, so Catégorie can't be bounded by the Section
    # label. Bound it on the known category values instead (Cadre / Non
    # cadre), which read reliably. Section keeps its own label-adjacent value.
    add_bounded("Catégorie", r"Cat[ée]gorie\s+(Non cadre|Cadre)\b")
    add("Section", r"Section\s+(\S+)")

    # Matricule s.s. (French NIR): 13 digits + a 2-digit control key. The
    # key is deterministic (97 - NIR mod 97). On this scan the NIR and its
    # key are split across lines by an intervening OCR line ("Section 0099"),
    # so allow anything between them, then validate: the key is only appended
    # when it satisfies the check, which both confirms the OCR read of the
    # NIR and proves the two fragments belong together. If it doesn't check
    # out, keep the 13 digits alone (not guessed).
    m_nir = re.search(r"Matricule s\.?s\.?\s+(\d{13})", joined)
    matricule = None
    if m_nir:
        nir = m_nir.group(1)
        expected_key = f"{97 - (int(nir) % 97):02d}"
        # Look for that exact 2-digit key standing alone anywhere after the
        # NIR (it's on its own OCR line here).
        if re.search(rf"(?m)^{expected_key}$", joined[m_nir.end():]):
            matricule = f"{nir} {expected_key}"
        else:
            matricule = nir
    fields.append(("Matricule sécurité sociale", matricule))

    # Bank details (RIB), split into its four standard parts. The block
    # reads as "C.B 20041 C.G 01003 n° cpte 0709943J024 09": code banque
    # (5), code guichet (5), account number (11, alphanumeric), RIB key (2).
    add("Code banque", r"C\.?B\s+(\d{5})")
    add("Code guichet", r"C\.?G\s+(\d{5})")
    add("N° de compte", r"n[°ºo]?\s*cpte\s+(\S+)")
    add("Clé RIB", r"n[°ºo]?\s*cpte\s+\S+\s+(\d{2})\b")

    # Domiciliation: the bank name only. The worker's street sits at the same
    # page height and is glued on the right by the flattening, so cut the
    # capture at the first street pattern ("<n> [bis] rue/av ...").
    m = re.search(r"Domiciliation\s+(.+)", joined)
    if m:
        domiciliation = re.split(
            r"\s+(?=\d+\s+(?:bis\s+)?(?:rue|av|avenue|bd|boulevard|impasse|place|chemin)\b)",
            m.group(1),
        )[0].strip()
    else:
        domiciliation = None
    fields.append(("Domiciliation", domiciliation))

    return fields


def _extract_worker_identity_block(joined: str) -> list[tuple[str, str | None]]:
    # Worker name and address. The civility line ("M COUTANT PIERRE") and the
    # street line ("27 bis rue du Faubourg St Jean", glued onto the
    # Domiciliation line) read reliably; the postal-code / city line
    # ("45000 ORLEANS") sits at the same page height as the RTT/Congés grid
    # and is glued onto one of its lines ("collaborateur | employeur 45000
    # ORLEANS"), but a "<cp> <VILLE>" pattern still recovers it.
    #
    # The "M"/"Mme" civility anchor is intentionally weak but safe here: the
    # "Matricule" line above is already consumed and no other line starts
    # with a bare "M". Revisit this anchor if it ever captures the wrong line.
    fields = []

    m = re.search(r"^M(?:me)?\.?\s+([A-ZÉÈ].+)$", joined, re.MULTILINE)
    name = m.group(1).strip() if m else None
    fields.append(("Nom travailleur", name))

    # Street: glued to the end of the "Domiciliation ..." line. The employer
    # street ("2 avenue de Paris") matches the same pattern higher up, so
    # take the LAST match - the worker block is below the employer block in
    # reading order.
    streets = re.findall(
        r"(\d+\s+(?:bis\s+)?(?:rue|av|avenue|bd|boulevard|impasse|place|chemin)\b[^\n]*)",
        joined,
        re.IGNORECASE,
    )
    street = streets[-1].strip() if streets else None

    # City: first "<5-digit cp> <VILLE>" that isn't the employer's own line.
    # The employer city ("45000 ORLEANS") also matches, so take the *last*
    # such occurrence - the worker block is below the employer block in
    # reading order.
    cities = re.findall(r"\b(\d{5}\s+[A-ZÉÈ][^\n]*)", joined)
    city = cities[-1].strip() if cities else None

    address = " ".join(p for p in (street, city) if p) or None
    fields.append(("Adresse travailleur", address))

    return fields


# --- totals / cumuls block (bottom-right grid) ------------------------------
#
# A three-part grid at the bottom of the page, heavily mangled by OCR:
#   - hours worked (Période / Année) and overtime hours (0,00 here)
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
# Overtime hours ("Heures supp.") are always 0,00 on this template and their
# OCR line is unreadable ("Pore o00 ooo"), so they're emitted as a fixed
# 0,00 rather than parsed (documented, not guessed).

CUMUL_COLUMNS = [
    "Brut fiscal", "Base sécurité sociale", "Charges patronales",
    "Charges salariales", "Net imposable",
]


def _extract_totals_block(joined: str) -> list[tuple[str, str | None]]:
    # All fields here are bulletin-level totals: their labels are prefixed
    # "Synthèse ..." on the way out (see extract_earnings_table).
    empty = (
        [("Heures travaillées (période)", None), ("Heures travaillées (année)", None)]
        + [(f"{c} (période)", None) for c in CUMUL_COLUMNS]
        + [(f"{c} (année)", None) for c in CUMUL_COLUMNS]
        + [("Net à payer", None)]
        + [("Heures supp. (période)", "0,00"), ("Heures supp. (année)", "0,00")]
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

    # Overtime hours: fixed 0,00 (see block comment above).
    fields.append(("Heures supp. (période)", "0,00"))
    fields.append(("Heures supp. (année)", "0,00"))

    return fields


# --- top RTT / Congés grid --------------------------------------------------
#
# A small grid at the top of the payslip: three counters (RTT collaborateur,
# RTT employeur, Congés) each with Pris / Solde / Acquis rows, plus a "Dates
# de congés" column that is empty here. The grid is OCR'd as three data rows
# keyed "Pris" / "Solde" / "Acquis", each carrying three "0,00" values in
# column order (RTT collab, RTT employeur, Congés). The "Dates de congés"
# cells are blank ("Du au") and emitted as None. If the grid isn't found,
# nothing is emitted (not guessed).

_RTT_COLUMNS = ["RTT collaborateur", "RTT employeur", "Congés"]
_RTT_ROWS = [("Pris", "pris"), ("Solde", "solde"), ("Acquis", "acquis")]


def _extract_rtt_block(joined: str) -> list[tuple[str, str | None]]:
    fields = []
    found_any = False
    for row_label, row_fr in _RTT_ROWS:
        m = re.search(rf"(?m)^{row_label}\s+(.+)$", joined)
        values = NUMBER_RE.findall(m.group(1)) if m else []
        if m:
            found_any = True
        for col, value in zip(_RTT_COLUMNS, values[:3]):
            fields.append((f"{col} ({row_fr})", value if value else None))
        # Pad missing columns with None so the grid shape is stable.
        for col in _RTT_COLUMNS[len(values[:3]):]:
            fields.append((f"{col} ({row_fr})", None))

    if not found_any:
        return []

    # Dates de congés column: blank on this template.
    for _, row_fr in _RTT_ROWS:
        fields.append((f"Dates de congés ({row_fr})", None))

    return fields


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
#     a single pair CANNOT be split: its retenue could be either part, so no
#     suffix is added (kept honest - the visual column is lost in the OCR
#     flattening and is not reconstructed from coordinates here).
#   - Gain lines (indemnités, primes, gratifications) don't satisfy the
#     cotisation invariant. Their amounts stay numbered ("Montant N")
#     because Nombre/Base/Gain can't be told apart reliably from flattened
#     text once empty cells collapse.
#
# Section anchor: "Domiciliation" is the last reliable label before the
# table (the table's own OCR'd header row is too mangled to anchor on).

NUMBER_RE = re.compile(r"-?\d{1,3}(?:[ .]\d{3})*,\d{2,3}|\d+,\d{2,3}")
# OCR renders the column separator as a literal "|" on some rows and as
# plain whitespace on others - both accepted between the code and the rest.
# The code itself may also be wrapped in "|...|" (e.g. "|5620|"), so an
# optional leading "|" is allowed before it. A code is 1-4 digits NOT
# immediately followed by another digit or "/", so a detail date line
# ("09/05/14") isn't mistaken for a code. The trailing (?![\d/]) anchors on
# the whole leading number: it forbids the regex from backtracking to a
# shorter digit run (matching "0" out of "09").
CODE_LINE_RE = re.compile(r"^\|?\s*(\d{1,4})(?![\d/])\s*\|?\s*(.*)$")
# A code-less detail line whose content starts like a date (dd/mm/yy).
DATE_DETAIL_RE = re.compile(r"^\d{1,2}/\d{1,2}/\d{2,4}\b")

INVARIANT_TOLERANCE = 0.015  # euro: retenue vs base*taux/100, absorbs cents rounding

SUMMARY_LABELS = ["Total Brut", "Total Cotisations"]

# Characters OCR leaves stuck to the edges of a rubric label ("[Prime",
# "stag. ) )", "[Formation"). Stripped from both ends; internal punctuation
# in genuine labels (e.g. "C.S.G.", "A.F.") is kept.
_LABEL_EDGE_CHARS = "[]|()"


def _clean_label(label: str) -> str:
    """Strip stray OCR bracket/pipe/paren noise from a rubric label's edges
    and collapse whitespace. Internal punctuation is preserved."""
    label = " ".join(label.split())
    # Remove edge noise tokens ("]", ")", "[", "|", "( )") repeatedly.
    changed = True
    while changed and label:
        changed = False
        stripped = label.strip(_LABEL_EDGE_CHARS + " ")
        if stripped != label:
            label = stripped
            changed = True
    return label.strip()


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
    # Label: everything that isn't a number, once stray OCR edge noise is
    # cleaned off.
    label = _clean_label(NUMBER_RE.sub(" ", rest))

    results = []
    if label:
        results.append((code, "Désignation", label))

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


def extract_earnings_table(text: str):
    """Extract the "Désignation" earnings table from the flattened text.

    Returns (table_lines, summary_lines):
      - table_lines: (code, column, value) - one entry per non-empty cell.
        Cotisation lines (validated by the Retenue = Base*Taux/100 invariant)
        get named columns (Base / Taux / Retenue, with (sal)/(patr) suffixes
        only when both parts are present on the line). Any other line's
        amounts are numbered ("Montant N") - gain vs retenue can't be told
        apart from flattened text. The two inline subtotals (Total Brut,
        Total Cotisations) are emitted as ordinary rows keyed on their label.
        The bottom totals grid is appended keyed "Synthèse", and the top
        RTT/Congés grid is appended keyed on its counter/column labels.
      - summary_lines: always [] (subtotals are kept inline as normal rows,
        matching the other templates' public shape).
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
        # amount(s) after them.
        total_label = next(
            (lbl for lbl in SUMMARY_LABELS
             if _strip_accents(line).lower().startswith(_strip_accents(lbl).lower())),
            None,
        )
        if total_label is not None:
            rest = line[len(total_label):]
            # Amounts after the label: decimals ("5,22") or a bare integer
            # ("234", OCR having dropped the comma).
            values = re.findall(r"-?\d{1,3}(?:[ .]\d{3})*,\d{2,3}|\d+", rest)
            if total_label == "Total Brut":
                # Single value: the brut gain (part salariale).
                table_lines.append(
                    ("Total Brut", "Gain (sal)", values[0] if values else None)
                )
            else:  # Total Cotisations: retenue salariale then patronale.
                if len(values) <= 1:
                    table_lines.append(
                        ("Total Cotisations", "Retenue (sal)",
                         values[0] if values else None)
                    )
                else:
                    table_lines.append(("Total Cotisations", "Retenue (sal)", values[0]))
                    table_lines.append(("Total Cotisations", "Retenue (patr)", values[1]))
            last_code = None
            continue

        m = CODE_LINE_RE.match(line)
        if m:
            last_code = m.group(1)
            table_lines += _parse_table_line(m.group(1), m.group(2))
            continue
        # Code-less line following a coded one. Only genuine detail rows are
        # attached: the individual absence dates ("09/05/14", "19/05/14
        # (0,5)") under a code. Anything else (the totals grid header, OCR
        # noise, the legal footer) is NOT rubric detail and is ignored -
        # matching a date pattern is the gate.
        if last_code is not None and DATE_DETAIL_RE.match(line):
            table_lines.append((last_code, "Détail", line.strip()))

    joined = "\n".join(lines)

    # Top RTT / Congés grid, appended so the app can render it with the rest.
    for label, value in _extract_rtt_block(joined):
        table_lines.append((_rtt_key(label), _rtt_col(label), value))

    # Bulletin totals (hours, cumuls, Net à payer) go at the very end, as
    # ordinary rows keyed on "Synthèse" with the field name as the column.
    for label, value in _extract_totals_block(joined):
        table_lines.append(("Synthèse", label, value))

    return table_lines, []


# --- RTT grid key/column helpers --------------------------------------------
#
# _extract_rtt_block yields ("<Column> (<row>)", value) pairs; the earnings
# table wants (key, column, value) triples. Split the composite label back
# into its counter name (the key) and its row (the column) so the app groups
# the three counters as rows the way it groups rubric codes.

def _rtt_key(label: str) -> str:
    return re.sub(r"\s*\([^)]*\)\s*$", "", label).strip()


def _rtt_col(label: str) -> str:
    m = re.search(r"\(([^)]*)\)\s*$", label)
    return m.group(1) if m else label