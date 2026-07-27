# api/src/docscope/services/models/ricoh.py
#
# Field extraction rules for "Ricoh": a French payslip PDF (bulletin de
# paie, RICOH France template, convention Métallurgie cadres). Like Apside
# and Mosica the source is a flattened scan with no text layer (see
# extraction.py's OCR fallback), so everything here works over OCR'd text.
# The scan is 2 pages: the employer / worker / contract header repeats on
# both pages and the "Rubriques" earnings table is split across them, so
# rules that read a single field just take the first (identical) match and
# the table scan gathers rubric lines from wherever they sit.
#
# OCR quirks handled here (all seen on the real scans): a rubric line may
# carry leading noise ("| UVG ...", "ses | UZW ...", "_ UZD ..."), a code
# can be misread ("UD0" -> "UDO", "A08" -> "AO8", "UZJ" -> "UZ]"), a minus
# is sometimes an OCR tilde ("~3073,62"), a value can gain/lose a trailing
# digit ("22,19" -> "22,194") or be destroyed entirely ("...0,036 TTL").
# Values are kept as read; genuine OCR damage that breaks a line's column
# structure just drops that line to neutral "Montant N" cells, never guessed.
#
# Two independent parts (same public interface as the other models):
#   - extract_fields(): single key/value fields (employer, worker, contract,
#     payment)
#   - extract_earnings_table(): the "Rubriques" table (codes + amounts), the
#     bottom TOTAUX grid (keyed "Synthèse") and the CONGES counter grid.

import re

# A monetary/rate token. French decimals with "," ; optional thousands sep
# (space or dot). Leading "-" or an OCR tilde "~" both mean a negative.
NUMBER_RE = re.compile(r"[-~]?\d+,\d{2,3}")


def _to_float(s: str) -> float:
    """French number ("1.234,56" / "-10,00" / OCR "~5,00") -> float."""
    s = s.replace("~", "-").replace(" ", "").replace(".", "").replace(",", ".")
    return float(s)


def _norm_number(s: str) -> str:
    """Normalise an OCR tilde used as a minus. Everything else kept as read."""
    return s.replace("~", "-")


# --- fixed key/value fields -------------------------------------------------


def extract_fields(text: str) -> list[tuple[str, str | None]]:
    """Extract the fixed set of key/value fields from a Ricoh payslip."""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    joined = "\n".join(lines)

    fields = []
    fields += _extract_employer_block(joined)
    fields += _extract_worker_block(joined)
    fields += _extract_contract_block(joined)
    fields += _extract_payment_block(joined)
    return fields


def _extract_employer_block(joined: str) -> list[tuple[str, str | None]]:
    fields = []

    def add(label, pattern, flags=0):
        m = re.search(pattern, joined, flags)
        fields.append((label, m.group(1).strip() if m else None))

    # Convention collective: the line right after "Convention collective de la".
    add("Convention collective", r"Convention collective de la\s*\n(.+)")

    add("Nom employeur", r"(?m)^(RICOH France.*)$")

    # Address: the lines between "RICOH France" and "Etablissement", joined.
    m = re.search(r"RICOH France[^\n]*\n(.+?)\nEtablissement", joined, re.DOTALL)
    fields.append(
        ("Adresse employeur", " ".join(m.group(1).split("\n")) if m else None)
    )

    # "Etablissement : RFR - JOUE LES TOU" - a trailing run of OCR page-code
    # junk ("... LOR RICG") is glued on some pages; the clean copy is picked
    # up first (page 1), so first-match is enough.
    add("Etablissement", r"(?m)^Etablissement\s*:\s*([^\n]+)")
    add("Siret", r"Siret\s*:\s*(\d+)")
    add("APE", r"APE\s*:\s*(\S+)")
    add("URSSAF", r"(?m)URSSAF de\s+([A-ZÉÈ][A-ZÉÈ ]+?)(?:\s+\d|$)")
    add("N° URSSAF", r"N\.?\s*URSSAF\s*:\s*(\d+)")

    return fields


# Street patterns reused by the worker block (case-insensitive).
_STREET_RE = (
    r"\d+\s+(?:bis\s+)?(?:rue|av|avenue|bd|boulevard|impasse|place|chemin|"
    r"all[ée]e|all|route|quai|cours|square)\b[^\n]*"
)


def _extract_worker_block(joined: str) -> list[tuple[str, str | None]]:
    # The worker block sits after the "M. <NAME>" civility line. On the
    # cleaner page it's on its own lines; on a noisier page it's glued to the
    # end of employer lines. Anchoring the address search *after* the name
    # keeps the employer street/city (which read higher up) out of the way.
    fields = []

    name_m = re.search(r"\bM(?:me)?\.\s+([A-ZÉÈ]{2,}(?:\s+[A-ZÉÈ]+)+)", joined)
    name = name_m.group(1).strip() if name_m else None
    fields.append(("Nom travailleur", name))

    after = joined[name_m.end():] if name_m else joined
    street_m = re.search(_STREET_RE, after, re.IGNORECASE)
    city_m = re.search(r"\b(\d{5}\s+[A-ZÉÈ][^\n]*)", after)
    street = street_m.group(0).strip() if street_m else None
    city = city_m.group(1).strip() if city_m else None
    address = " ".join(p for p in (street, city) if p) or None
    fields.append(("Adresse travailleur", address))

    return fields


def _extract_contract_block(joined: str) -> list[tuple[str, str | None]]:
    fields = []

    def add(label, pattern, flags=0):
        m = re.search(pattern, joined, flags)
        fields.append((label, m.group(1).strip() if m else None))

    add("Emploi", r"(?m)^Emploi\s*:\s*([^\n]+)")
    # Affectation may be glued to "Date d'entrée ..." on a page; bound it.
    add("Affectation", r"(?m)Affectation\s*:\s*([^\n]*?)(?:\s+Date d'entr[ée]e|$)")
    add("Date d'entrée", r"Date d'entr[ée]e\s*:\s*(\d{2}/\d{2}/\d{2,4})")
    add("Matricule", r"Matricule\s*:\s*(\d+)")
    add("Position", r"Position\s*:\s*(\S+)")
    add("Indice", r"Indice\s*:\s*(\S+)")
    add("Ancienneté", r"Anciennet[ée]\s*:\s*(.+?)\s+N°?\s*SS")
    add("N° SS", r"N°?\s*SS\s*:\s*(\d+)")
    add("Salaire de base", r"Salaire de base\s*:\s*(\S+)")
    # OCR often leaves "218]." - keep only the leading digit run.
    add("Horaire de référence", r"Horaire de r[ée]f[ée]rence\s*:\s*(\d+)")
    add("Minimum conventionnel", r"Minimum conventionnel\s*:\s*(\S+)")

    return fields


def _extract_payment_block(joined: str) -> list[tuple[str, str | None]]:
    # Read from the bottom CONGES grid's "MODE DE PAIEMENT" column. Present
    # only on the last page (the page-1 grid is empty), so first-match lands
    # on the filled one.
    fields = []

    m = re.search(r"\b(Virement|Ch[èe]que|Esp[èe]ces)\b", joined)
    fields.append(("Mode de paiement", m.group(1) if m else None))

    m = re.search(r"\bLE\s+(\d{2}/\d{2}/\d{2,4})", joined)
    fields.append(("Date de paiement", m.group(1) if m else None))

    m = re.search(r"((?:LA\s+)?BANQUE[A-ZÉÈ ]*[A-ZÉÈ])", joined)
    fields.append(("Banque", m.group(1).strip() if m else None))

    m = re.search(r"\b(FR\d{2}[0-9A-Z]{15,})\b", joined)
    fields.append(("IBAN", m.group(1) if m else None))

    return fields


# --- "Rubriques" earnings table --------------------------------------------
#
# Pure regex over flattened OCR text. Each rubric line starts with a 2-4 char
# code (letters/digits) after optional leading OCR noise, then a label, then
# the amounts. Column naming is done only where it can be *proven* by the
# payslip invariant Retenue = Base x Taux / 100 (magnitudes, to absorb OCR
# sign drops on régularisation lines): a line whose numbers read as Base
# followed by (Taux, Retenue) pairs each satisfying the invariant gets named
# Base / Taux / Retenue. Two valid pairs = an employee part then an employer
# part (layout order): "(sal)" then "(patr)". A single pair CANNOT be split
# (its part is lost in the flattening) so no suffix is added. Anything that
# fails the invariant (gains, primes, one-off amounts, OCR-damaged lines) has
# its amounts numbered neutrally ("Montant N") - never guessed.

# A code token: 2-4 uppercase letters/digits, possibly with an OCR bracket.
CODE_TOKEN_RE = re.compile(r"^[A-Z0-9][A-Z0-9\[\]]{1,3}$")
# Leading OCR junk that can precede a code: pipes/underscores/dashes/dots or
# a short all-lowercase blob ("ses", "sos"...).
_JUNK_RE = re.compile(r"^[|_—–\-.]+$|^[a-z]{1,4}$")

INVARIANT_TOLERANCE = 0.02  # euro: retenue vs base*taux/100, absorbs rounding

CUMUL_COLUMNS = [
    "Brut", "Frais", "Plafond TA", "Base SS plafonnée",
    "Net imposable", "Net à payer",
]
# Bare integers appear in the totals grid ("324" for "324,00"), so a looser
# number regex is used there than in the rubric table.
_TOTALS_NUMBER_RE = re.compile(r"-?\d+,\d{2,3}|-?\d+")

_CONGES_COLUMNS = ["Congés A", "Congés A-1", "RTT", "CET", "RCP"]
_CONGES_ROWS = [("Acq", "acquis"), ("Pris", "pris"), ("Solde", "solde")]


def _find_code(line: str):
    """Return (code, rest) if `line` is a rubric line, else (None, None).
    Skips leading OCR junk, then requires the first real token to look like
    a rubric code."""
    tokens = line.split()
    idx = 0
    while idx < len(tokens) and _JUNK_RE.match(tokens[idx]):
        idx += 1
    if idx >= len(tokens) or not CODE_TOKEN_RE.match(tokens[idx]):
        return None, None
    return tokens[idx], " ".join(tokens[idx + 1:])


def _clean_label(label: str) -> str:
    label = " ".join(label.split())
    changed = True
    while changed and label:
        changed = False
        stripped = label.strip("[]|()©è+:.,;* ")
        if stripped != label:
            label, changed = stripped, True
    return label.strip()


def _name_cotisation_line(nums: list[str]) -> list[tuple[str, str]] | None:
    """Read `nums` as Base + (Taux, Retenue) pairs, each validated by the
    invariant |Base|*Taux/100 == |Retenue|. Returns named (column, value)
    cells, or None if the numbers don't cleanly fit that shape."""
    if len(nums) < 3:
        return None
    base = abs(_to_float(nums[0]))
    if base == 0:
        return None

    pairs = []
    i = 1
    while i + 1 <= len(nums) - 1:
        taux, retenue = nums[i], nums[i + 1]
        if abs(base * _to_float(taux) / 100 - abs(_to_float(retenue))) <= INVARIANT_TOLERANCE:
            pairs.append((taux, retenue))
            i += 2
        else:
            return None
    if i != len(nums) or not pairs:
        return None

    cells = [("Base", _norm_number(nums[0]))]
    if len(pairs) == 1:
        cells.append(("Taux", _norm_number(pairs[0][0])))
        cells.append(("Retenue", _norm_number(pairs[0][1])))
    else:
        suffixes = ["(sal)", "(patr)"]
        for idx, (taux, retenue) in enumerate(pairs):
            suffix = suffixes[idx] if idx < len(suffixes) else str(idx + 1)
            cells.append((f"Taux {suffix}", _norm_number(taux)))
            cells.append((f"Retenue {suffix}", _norm_number(retenue)))
    return cells


def _parse_table_line(code: str, rest: str) -> list[tuple[str, str, str]]:
    nums = NUMBER_RE.findall(rest)
    label = _clean_label(NUMBER_RE.sub(" ", rest))

    results = []
    if label:
        results.append((code, "Libellé", label))

    named = _name_cotisation_line(nums)
    if named is not None:
        for column, value in named:
            results.append((code, column, value))
    else:
        for i, value in enumerate(nums, start=1):
            results.append((code, f"Montant {i}", _norm_number(value)))
    return results


def _extract_totals_block(joined: str) -> list[tuple[str, str | None]]:
    # "MENSUEL <values>" and "CUMUL ANNUEL <values>". Empty on the first page,
    # filled on the last; the [ \t]+ anchor only matches the filled line.
    fields = []
    for tag, fr in (("MENSUEL", "mensuel"), ("CUMUL ANNUEL", "cumul annuel")):
        m = re.search(rf"(?m)^[=\s_|]*{tag}[ \t]+(.+)$", joined)
        if not m:
            continue
        values = _TOTALS_NUMBER_RE.findall(m.group(1))
        for col, value in zip(CUMUL_COLUMNS, values):
            fields.append((f"{col} ({fr})", value))
    return fields


def _extract_conges_block(joined: str) -> list[tuple[str, str, str]]:
    # Acq / Pris / Solde rows. Each line carries the leave columns first, then
    # the MODE DE PAIEMENT text (Virement.../BANQUE.../IBAN); cut that off so
    # its dates aren't read as leave values. OCR can still garble a value
    # (e.g. "5" -> "la"), leaving a row short; kept as read, not realigned.
    results = []
    cut = re.compile(
        r"\s+(?=Virement\b|Ch[èe]que\b|Esp[èe]ces\b|LA\s+BANQUE|BANQUE\b|FR\d{2}|LE\s+\d)"
    )
    for tag, fr in _CONGES_ROWS:
        m = re.search(rf"(?m)^{tag}\b(.+)$", joined)
        if not m:
            continue
        segment = cut.split(m.group(1))[0]
        nums = re.findall(r"-?\d+(?:,\d+)?", segment)
        for col, value in zip(_CONGES_COLUMNS, nums[:5]):
            results.append((f"Congés {fr}", col, value))
    return results


def extract_earnings_table(text: str):
    """Extract the "Rubriques" earnings table from the flattened OCR text.

    Returns (table_lines, summary_lines):
      - table_lines: (code, column, value) - one entry per non-empty cell.
        Cotisation lines get named Base / Taux / Retenue columns (with
        (sal)/(patr) suffixes only when both parts are on the line); every
        other line's amounts are numbered "Montant N". The bottom TOTAUX grid
        is appended keyed "Synthèse", and the CONGES counter grid keyed on its
        counter label.
      - summary_lines: always [] (kept inline, matching the other templates).
    Returns ([], []) if no rubric line is found.
    """
    lines = [line.strip() for line in text.splitlines() if line.strip()]

    table_lines = []
    for line in lines:
        code, rest = _find_code(line)
        if code is None or not NUMBER_RE.search(rest):
            continue
        table_lines += _parse_table_line(code, rest)

    if not table_lines:
        return [], []

    joined = "\n".join(lines)
    for label, value in _extract_totals_block(joined):
        table_lines.append(("Synthèse", label, value))
    for key, column, value in _extract_conges_block(joined):
        table_lines.append((key, column, value))

    return table_lines, []