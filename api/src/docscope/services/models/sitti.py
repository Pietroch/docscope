# api/src/docscope/services/models/sitti.py
#
# Field extraction rules for "Sitti": a French payslip PDF (bulletin de paie,
# S.A.S. SITTI, Sage template, CCN Bureaux d'études techniques - Syntec). Like
# Apside/Mosica/Ricoh the source is a flattened scan with no text layer (see
# extraction.py's OCR fallback), so everything works over OCR'd text.
#
# The document is one page but its header is a dense two-column grid (employer
# block on the left, an identity grid on the right) that the OCR glues line by
# line, and the period / payment box at the top-right is torn off and lands as
# detached lines *above* the "BULLETIN DE PAIE" line. So header fields anchor
# on their own label where the value is adjacent, and fall back to matching the
# value by its shape (a date, the 13+2 digit NIR, the SIRET pair) where the
# flattening pulled label and value apart.
#
# Two invariants of the Sage template carry most of the work in the table:
#
#   1. Every cell is decimal, with a *fixed* precision per kind of column:
#      amounts have 2 decimals, rates 3, header counters 4. So the decimal
#      count types a token (rate vs amount) even after flattening, and a cell
#      whose comma the scan ate can be repaired by re-inserting it at the
#      known position ("141" -> "1,41", "7106616" -> "710,6616").
#   2. Arithmetic: Retenue = Base x Taux / 100 for cotisation rows, and
#      montant = Nombre x Base for quantity rows (absences, tickets resto).
#
# Combined, these let us name the real columns of the grid - Nombre / Base /
# Taux (sal) / Gain (sal) / Retenue (sal) / Taux (patr) / Retenue + (patr) /
# Retenue - (patr) - instead of falling back to positional "Montant N".
# "Montant N" is kept as the last resort for shapes no rule recognises, so an
# unknown row degrades to something reviewable rather than mislabelled.
#
# Two independent parts (same public interface as the other models):
#   - extract_fields(): single key/value fields (employer, worker, contract,
#     header refs, leave counters, abattements, URSSAF address)
#   - extract_earnings_table(): the "Désignation" table (codes + amounts,
#     inline Total Brut / Total Cotisations, bottom cumuls grid).

import re

NUMBER_RE = re.compile(r"-?\d+,\d{2,3}")

# A cell inside the numeric zone of a table row: either a proper decimal, or a
# bare digit run whose comma the scan dropped (delimited by whitespace or an
# OCR'd column rule, so digits embedded in a label like "(+10 sala.)" are safe).
CELL_RE = re.compile(r"-?\d+,\d{2,3}|(?<![\w,.])-?\d{3,8}(?![\w,.])")

STREET_WORD = (
    r"(?:Rue|Avenue|Av|Bd|Boulevard|Impasse|Place|Chemin|All[ée]e|Route|Quai"
    r"|Cours|Square|Villa|Sentier)"
)


def _to_float(s: str) -> float:
    return float(s.replace(" ", "").replace(".", "").replace(",", "."))


def _repair_decimals(token: str, decimals: int = 2) -> str:
    """Re-insert a comma the scan dropped, at the column's fixed precision."""
    if "," in token:
        return token
    sign, digits = ("-", token[1:]) if token.startswith("-") else ("", token)
    if len(digits) <= decimals:
        return token
    return f"{sign}{digits[:-decimals]},{digits[-decimals:]}"


# --- fixed key/value fields -------------------------------------------------


def extract_fields(text: str) -> list[tuple[str, str | None]]:
    """Extract the fixed set of key/value fields from a Sitti payslip."""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    joined = "\n".join(lines)
    ctx = _build_context(lines)

    fields = []
    fields += _extract_employer_block(joined, ctx)
    fields += _extract_identity_grid(joined, ctx)
    fields += _extract_worker_block(joined)
    fields += _extract_header_refs(joined)
    fields += _extract_counters_block(joined)
    fields += _extract_urssaf_block(lines)
    return fields


def _build_context(lines: list[str]) -> dict:
    """Locate the employer block's three address lines once, up front.

    The employer address is the left-hand column of the header grid: a street
    line (glued onto the identity row), an optional complement line, then the
    postcode/city line (glued onto Catégorie / Emploi occupé). Finding them
    together also gives us a clean anchor for Catégorie / Emploi occupé, which
    is simply what follows the city on its line.
    """
    ctx = {"street": None, "complement": None, "cp_city": None, "cat_emploi": None}

    for i, line in enumerate(lines[:14]):
        m = re.match(rf"(?:\d+\s+)?{STREET_WORD}\b[^\d|]*", line)
        if not m or len(m.group(0).split()) < 2:
            continue
        ctx["street"] = m.group(0).strip()
        # Complement line: next line up to the first OCR'd column rule, unless
        # it is already the postcode line or a right-column grid header.
        if i + 1 < len(lines):
            nxt = lines[i + 1].split("|")[0].strip()
            if (nxt and not re.match(r"\d{5}\b", nxt)
                    and not re.match(r"(?:Cat[ée]gorie|Qualification|Acquis|Emploi)", nxt)
                    and re.search(r"[A-Za-zÉÈéè]{3}", nxt)):
                ctx["complement"] = nxt
        break

    # Postcode + city (at most two words, so the glued Catégorie cell stays
    # out), then Catégorie / Emploi occupé = the rest of that same line.
    m = re.search(
        r"(?m)^(\d{5}\s+[A-ZÉÈ][A-Za-zÉÈ'\-]+(?:\s+[A-ZÉÈ][A-Za-zÉÈ'\-]+)?)(.*)$",
        "\n".join(lines),
    )
    if m:
        ctx["cp_city"] = " ".join(m.group(1).split())
        ctx["cat_emploi"] = _dedupe_repeated(_clean_label(m.group(2)))
    return ctx


def _dedupe_repeated(value: str | None) -> str | None:
    """Catégorie and Emploi occupé hold the same text; the OCR glues them."""
    if not value:
        return None
    words = value.split()
    if len(words) % 2 == 0 and words[: len(words) // 2] == words[len(words) // 2:]:
        return " ".join(words[: len(words) // 2])
    return value


def _extract_employer_block(joined: str, ctx: dict) -> list[tuple[str, str | None]]:
    fields = []

    def add(label, pattern, flags=0):
        m = re.search(pattern, joined, flags)
        fields.append((label, m.group(1).strip() if m else None))

    # Name: "S.A.S. SITTI". The scan routinely loses the dots ("SAS. SITTI"),
    # so the legal form is normalised back to its canonical spelling.
    m = re.search(r"(?m)^\s*S\.?A\.?S\.?[.\s]+([A-ZÉÈ][A-ZÉÈ]+)", joined)
    fields.append(("Nom employeur", f"S.A.S. {m.group(1)}" if m else None))

    add("SIRET", r"N[°ºo.]?\s*SIRET\s*:\s*(\d+\s+\d+)")
    add("APE/NAF", r"APE\s*/?\s*NAF\s*:\s*(\S+)")

    # "<street>, <complement> <cp> <city>" - comma after the street only, which
    # is what the single-line rendering of the block looks like.
    tail = " ".join(p for p in (ctx["complement"], ctx["cp_city"]) if p)
    parts = [p for p in (ctx["street"], tail) if p]
    fields.append(("Adresse employeur", ", ".join(parts) or None))

    # URSSAF number: the "<3 digits> <long digits>" line under "URSSAF".
    m = re.search(r"(?m)^URSSAF\b.*\n\s*(\d{3}\s+\d{6,})", joined)
    fields.append(("N° URSSAF", m.group(1).strip() if m else None))

    return fields


def _extract_identity_grid(joined: str, ctx: dict) -> list[tuple[str, str | None]]:
    # The identity grid's value row is glued onto the employer street line and
    # reads, in order: Matricule, (Niveau), Coefficient, (Indice), Ancienneté,
    # N° de Sécurité Sociale. Niveau/Indice are blank on these payslips. The
    # row is located by the NIR at its end (13 digits + 2-digit key, first
    # digit 1 or 2, which keeps the SIRET pair from matching).
    fields = []

    row = next(
        (line for line in joined.splitlines() if re.search(r"\b[12]\d{12}\s+\d{2}\b", line)),
        "",
    )
    nir = re.search(r"\b([12]\d{12})\s+(\d{2})\b", row)
    fields.append(
        ("N° Sécurité Sociale", f"{nir.group(1)} {nir.group(2)}" if nir else None)
    )

    # Matricule then Coefficient are the first two numbers of the row, once the
    # glued street text (which may itself start with a house number) is cut off.
    head = row[: nir.start()] if nir else ""
    if ctx["street"] and ctx["street"] in head:
        head = head[head.index(ctx["street"]) + len(ctx["street"]):]
    else:
        head = re.sub(r"^\s*\d{1,4}\s+(?=[A-Za-zÉÈ])", "", head)
        head = re.sub(r"^[^\d]*", "", head, count=1) if re.search(r"[A-Za-z]{3}", head) else head
    nums = re.findall(r"\b\d{1,6}\b", head)
    fields.append(("Matricule", nums[0] if len(nums) > 0 else None))
    fields.append(("Coefficient", nums[1] if len(nums) > 1 else None))

    m = re.search(r"(\d+)\s*an.{0,8}?(\d+)\s*mois", joined)
    fields.append(
        ("Ancienneté", f"{m.group(1)} an(s) et {m.group(2)} mois" if m else None)
    )

    def add(label, pat, f=0):
        mm = re.search(pat, joined, f)
        fields.append((label, mm.group(1).strip() if mm else None))
    add("Position", r"Position\s+(\d(?:\.\d+)+)")
    add("Horaire", r"Position\s+\S+\s+(\d{1,3},\d{3,4})")

    fields.append(("Catégorie", ctx["cat_emploi"]))
    fields.append(("Emploi occupé", ctx["cat_emploi"]))

    # CCN: label + first line, then the "Sociétés de conseil" tail that lands
    # on the APE/NAF line.
    m = re.search(r"CCN\s+([^\n|]+)", joined)
    ccn = m.group(1).strip().rstrip(",") if m else None
    if ccn:
        tail = re.search(r"(Soci[ée]t[ée]s de conseil)", joined)
        if tail:
            ccn = f"{ccn}, {tail.group(1)}"
    fields.append(("CCN", ccn))

    return fields


def _extract_worker_block(joined: str) -> list[tuple[str, str | None]]:
    fields = []

    m = re.search(r"\bM\s+([A-ZÉÈ]{2,}\s+[A-ZÉÈ]+)", joined)
    fields.append(("Nom travailleur", m.group(1).strip() if m else None))

    # Anchor the address search after the worker name so the URSSAF street
    # ("1 RUE FLEMING", higher up) isn't picked up as the worker's.
    after = joined[m.end():] if m else joined
    street = re.search(rf"\b(\d+\s+{STREET_WORD}\b[^\n|]*)", after, re.IGNORECASE)
    # Worker city is glued to the "D.I.F. (heures) ..." row (right column).
    city = re.search(r"D\.?[IL1]\.?F\.?.*?(\d{5}\s+[A-ZÉÈ][A-Za-zÉÈ]+)", joined)
    parts = [g.group(1).strip() for g in (street, city) if g]
    fields.append(("Adresse travailleur", " ".join(parts) or None))

    return fields


def _extract_urssaf_block(lines: list[str]) -> list[tuple[str, str | None]]:
    """Address of the collecting URSSAF, in the left column under its label."""
    start = next((i for i, line in enumerate(lines) if re.match(r"^URSSAF\b", line)), None)
    if start is None:
        return [("URSSAF", None)]

    street = city = None
    for line in lines[start + 1: start + 6]:
        # The worker's name block is glued to the right of these lines.
        left = re.split(r"\||\s+M\s+[A-ZÉÈ]{2,}", line)[0].strip()
        if street is None and re.match(rf"\d+\s+(?:{STREET_WORD}|RUE|AV|BD)\b", left, re.IGNORECASE):
            street = left
        elif city is None and re.match(r"^\d{5}\s+[A-ZÉÈ]", left):
            city = left
    value = " ".join(p for p in (street, city) if p)
    return [("URSSAF", value or None)]


def _extract_header_refs(joined: str) -> list[tuple[str, str | None]]:
    # Period / payment box: OCR'd as a detached block. "Période du" is often
    # mangled ("Pérodedu"), the start date with it; the "au" date and the
    # payment date/mode land on their own lines. Matched loosely / by shape.
    fields = []

    period_line = next(
        (line for line in joined.splitlines() if "rode" in line.lower() or "riode" in line.lower()),
        "",
    )
    dates = re.findall(r"\d{1,2}[/tT]\d{2}[/tT]\d{1,4}", period_line)
    au = re.search(r"\bau\s*:?\s*(\d{2}/\d{2}/\d{2,4})", joined)
    s_start = dates[0] if dates else None
    s_end = au.group(1) if au else (dates[1] if len(dates) > 1 else None)
    if s_start or s_end:
        fields.append(("Période", f"{s_start or '?'} au {s_end or '?'}"))
    else:
        fields.append(("Période", None))

    m = re.search(r"Paiement le\s*:\s*(\d{2}/\d{2}/\d{2,4})", joined)
    fields.append(("Date de paiement", m.group(1) if m else None))
    m = re.search(r"\bpar\s*:?\s*(Virement|Ch[èe]que|Esp[èe]ces)", joined)
    fields.append(("Mode de paiement", m.group(1) if m else None))

    # Commentaire / solde de tout compte (present on the final payslip only).
    m = re.search(r"Commentaire\s*:\s*(.+?)\s+Abat", joined)
    fields.append(("Commentaire", m.group(1).strip() if m else None))
    m = re.search(r"Sorti le\s*:?\s*(\d{2}/\d{2}/\d{2,4})", joined)
    fields.append(("Date de sortie", m.group(1) if m else None))

    return fields


def _extract_counters_block(joined: str) -> list[tuple[str, str | None]]:
    # D.I.F. and Congés counter rows (Acquis / Reste à prendre / Pris) plus the
    # Abat mensuel / Abat cumulé pair. Keys are always emitted, so a scan that
    # loses a row yields None rather than a shorter field list.
    fields = []

    dif = re.search(
        r"D\.?[IL1]\.?F\.?\s*\(?heures\)?\s+([\d,]+)\s+([\d,]+)\s*\|?\s*([\d,]+)", joined
    )
    for i, label in enumerate(("D.I.F. acquis", "D.I.F. reste", "D.I.F. pris")):
        fields.append((label, dif.group(i + 1) if dif else None))

    cp = re.search(r"(?m)^Cong[ée]s\s+([\d,]+)\s+([\d,]+)\s*\|?\s*([\d,]+)", joined)
    for i, label in enumerate(("Congés acquis", "Congés reste", "Congés pris")):
        fields.append((label, cp.group(i + 1) if cp else None))

    # Abattement: the two numbers following the "Abat mensuel / Abat cumulé"
    # header. Both are printed with 4 decimals, so a value whose comma the scan
    # ate ("7106616") is repaired back to "710,6616".
    am = re.search(r"Abat\s*cumul[ée]", joined)
    abat = []
    if am:
        region = joined[am.end():am.end() + 140]
        abat = [
            _repair_decimals(tok, 4)
            for tok in re.findall(
                r"(?<![/\d])-?\d+,\d{2,4}|(?<![/\d,])\d{5,8}(?![/\d,])", region
            )
        ]
    fields.append(("Abat mensuel", abat[0] if len(abat) > 0 else None))
    fields.append(("Abat cumulé", abat[1] if len(abat) > 1 else None))

    return fields


# --- "Désignation" earnings table ------------------------------------------
#
# Pure regex over flattened OCR text. Each row starts with a numeric code
# (10, 19, 2100 ... 9000), possibly wrapped in OCR punctuation ("2100!",
# "7900}", "4570)"). The row is then split at its first decimal number: what
# precedes is the Désignation label (so digits inside a label survive), what
# follows is the numeric zone. Cells in the zone are typed by their decimal
# count - 3 decimals = a rate, 2 = an amount - and named by matching the
# template's arithmetic (Retenue = Base x Taux / 100, montant = Nombre x Base).
# The two inline subtotals (Total Brut, Total Cotisations) are emitted as
# ordinary rows; the bottom cumuls grid is appended keyed "Synthèse".

# Code: optional leading OCR noise, then 1-4 digits, then an OCR separator.
CODE_LINE_RE = re.compile(r"^[\s§|.!)\]}]*(\d{1,4})\s*[|\])},!.:*]*\s+(.+)$")

RATE_TOLERANCE = 0.02      # Base x Taux / 100 vs printed Retenue
PRODUCT_TOLERANCE = 0.05   # Nombre x Base vs printed montant

SUMMARY_LABELS = ["Total Brut", "Total Cotisations"]

# The eight numeric columns of the grid, in reading order.
COL_NOMBRE = "Nombre"
COL_BASE = "Base"
COL_TAUX_SAL = "Taux (sal)"
COL_GAIN_SAL = "Gain (sal)"
COL_RET_SAL = "Retenue (sal)"
COL_TAUX_PATR = "Taux (patr)"
COL_RET_PLUS = "Retenue + (patr)"
COL_RET_MOINS = "Retenue - (patr)"

# Sections of the table, delimited by the two inline subtotals. A single
# amount means "Gain" above Total Brut and a retenue below it, so the section
# is part of the naming context.
SEC_GAINS, SEC_COTIS, SEC_AUTRES = "gains", "cotisations", "autres"

# A cotisation row showing only one Taux/Retenue pair gives no positional clue
# once flattened: 2400 (accident du travail, employer-only) and 7000 (CSG,
# employee-only) both read as "Base Taux Retenue". On this template the only
# employee-only cotisations are the CSG/CRDS family, so the code decides.
SALARIAL_ONLY_CODES = range(7000, 7200)

CUMUL_COLUMNS = [
    "Salaire brut", "Net imposable", "Charges salariales", "Charges patronales",
    "Heures travaillées", "Heures sup.", "Avantages en nature", "Net à payer",
]


def _clean_label(label: str) -> str:
    """Trim OCR noise without eating meaningful punctuation.

    A trailing full stop is dropped ("URSSAF Maladie vieil." -> "... vieil"),
    but a closing parenthesis is kept when it is balanced, so labels such as
    "Formation profes. (+10 sala.)" survive intact.
    """
    label = " ".join(label.split())
    label = label.lstrip("[]|(){}€:.,;*!°§ ")
    while label and label[-1] in "|:;*!,€ .]}":
        label = label[:-1]
    while label.endswith(")") and label.count("(") < label.count(")"):
        label = label[:-1]
    return label.strip()


def _split_label_and_cells(rest: str) -> tuple[str, list[dict]]:
    """Cut the row at its first decimal number, then type each cell."""
    m = NUMBER_RE.search(rest)
    if not m:
        return _clean_label(rest), []

    label = _clean_label(rest[: m.start()])
    cells = []
    for token in CELL_RE.findall(rest[m.start():]):
        text = _repair_decimals(token, 2)
        decimals = len(text.split(",")[1]) if "," in text else 0
        cells.append({"text": text, "value": _to_float(text), "is_rate": decimals == 3})
    return label, cells


def _name_by_rate(code: str, cells: list[dict], section: str) -> list[str] | None:
    """Name a row of the form Base + (Taux, Retenue) pairs, +/- one orphan."""
    if len(cells) < 3 or cells[0]["is_rate"]:
        return None
    base = abs(cells[0]["value"])
    if base == 0:
        return None

    pairs, i = 0, 1
    while i + 1 < len(cells) and cells[i]["is_rate"] and not cells[i + 1]["is_rate"]:
        expected = base * cells[i]["value"] / 100
        if abs(expected - abs(cells[i + 1]["value"])) > RATE_TOLERANCE:
            break
        pairs += 1
        i += 2

    orphans = len(cells) - i
    if not pairs or orphans > 1 or (orphans == 1 and cells[i]["is_rate"]):
        return None

    salarial_amount = COL_GAIN_SAL if section == SEC_GAINS else COL_RET_SAL
    if pairs == 1:
        if section == SEC_GAINS or int(code) in SALARIAL_ONLY_CODES:
            columns = [COL_BASE, COL_TAUX_SAL, salarial_amount]
        else:
            columns = [COL_BASE, COL_TAUX_PATR, COL_RET_MOINS]
    else:
        columns = [COL_BASE, COL_TAUX_SAL, salarial_amount, COL_TAUX_PATR, COL_RET_MOINS]
        # A third pair would be off-template; number the extras.
        for n in range(2, pairs):
            columns += [f"Taux {n + 1}", f"Retenue {n + 1}"]
    if orphans == 1:
        # Employer side charged at a nil rate: only the Retenue (-) cell prints.
        columns.append(COL_RET_MOINS)
    return columns


def _name_columns(code: str, cells: list[dict], section: str) -> list[str] | None:
    """Map a row's cells onto the grid's columns, or None if unrecognised."""
    rates = [c["is_rate"] for c in cells]
    values = [c["value"] for c in cells]

    # Quantity rows: montant = Nombre x Base (absences, tickets restaurant).
    if (len(cells) == 3 and not any(rates) and values[0] and values[1]
            and abs(values[0] * values[1] - values[2]) <= PRODUCT_TOLERANCE):
        return [COL_NOMBRE, COL_BASE, COL_RET_SAL]

    by_rate = _name_by_rate(code, cells, section)
    if by_rate:
        return by_rate

    # Two bare amounts, no rate: employee/employer split of a flat contribution
    # (mutuelle), or an allègement - which prints as a credit in Retenue (+).
    if len(cells) == 2 and not any(rates) and section != SEC_GAINS:
        if min(values) < 0:
            return [COL_GAIN_SAL, COL_RET_PLUS]
        return [COL_RET_SAL, COL_RET_MOINS]

    # A lone amount in the earnings section is a gain.
    if len(cells) == 1 and not rates[0] and section == SEC_GAINS:
        return [COL_GAIN_SAL]

    return None


def _parse_table_line(code: str, rest: str, section: str) -> list[tuple[str, str, str]]:
    label, cells = _split_label_and_cells(rest)

    results = []
    if label:
        results.append((code, "Désignation", label))

    columns = _name_columns(code, cells, section)
    if columns is None:
        columns = [f"Montant {i}" for i in range(1, len(cells) + 1)]
    for column, cell in zip(columns, cells):
        results.append((code, column, cell["text"]))
    return results


def _extract_cumuls_block(joined: str) -> list[tuple[str, str]]:
    # "Période <values>" then "Année <values>", positional over the bottom
    # cumuls grid. Période omits the last (Net à payer) column; Année carries
    # it. Any value can be missing on a scan -> shorter row, mapped as far as
    # it goes.
    results = []
    for tag, fr in (("Période", "période"), ("Année", "année")):
        m = re.search(rf"(?m)^{tag}\s+([\d ,.|)}}\]]+)$", joined)
        if not m:
            continue
        values = NUMBER_RE.findall(m.group(1))
        for col, value in zip(CUMUL_COLUMNS, values):
            results.append((f"{col} ({fr})", value))
    return results


def extract_earnings_table(text: str):
    """Extract the "Désignation" earnings table from the flattened OCR text.

    Returns (table_lines, summary_lines):
      - table_lines: (code, column, value) - one entry per non-empty cell.
        Cells are named after the grid's real columns (Nombre, Base, Taux and
        Gain/Retenue with (sal)/(patr) and +/- qualifiers) whenever the row's
        arithmetic or the section identifies them; rows matching no rule fall
        back to numbered "Montant N". Total Brut / Total Cotisations are
        ordinary rows keyed on their label. The bottom cumuls grid is appended
        keyed "Synthèse".
      - summary_lines: always [] (subtotals kept inline, matching the family).
    Returns ([], []) if the table isn't found.
    """
    lines = [line.strip() for line in text.splitlines() if line.strip()]

    # Table region: from the "Désignation" header row down to the "Cumuls"
    # bottom grid. Bounding it keeps the header grid's stray numbers and the
    # cumuls lines out of the rubric scan.
    start = next(
        (i for i, line in enumerate(lines) if "signati" in line.lower()),
        None,
    )
    if start is None:
        return [], []
    end = next(
        (i for i, line in enumerate(lines) if re.match(r"^Cum[ou]?[ls]s?\b", line)),
        len(lines),
    )
    section = lines[start + 1:end]

    table_lines = []
    current = SEC_GAINS
    for line in section:
        total = next(
            (lbl for lbl in SUMMARY_LABELS if line.lower().startswith(lbl.lower())),
            None,
        )
        if total is not None:
            values = NUMBER_RE.findall(line[len(total):])
            if total == "Total Brut":
                table_lines.append(("Total Brut", COL_GAIN_SAL, values[0] if values else None))
                current = SEC_COTIS
            else:
                for col, value in zip((COL_RET_SAL, COL_RET_MOINS), values):
                    table_lines.append(("Total Cotisations", col, value))
                if not values:
                    table_lines.append(("Total Cotisations", COL_RET_SAL, None))
                current = SEC_AUTRES
            continue

        m = CODE_LINE_RE.match(line)
        if not m or not NUMBER_RE.search(m.group(2)):
            continue
        table_lines += _parse_table_line(m.group(1), m.group(2), current)

    if not table_lines:
        return [], []

    joined = "\n".join(lines)
    for label, value in _extract_cumuls_block(joined):
        table_lines.append(("Synthèse", label, value))

    return table_lines, []