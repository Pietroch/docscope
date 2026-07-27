# api/src/docscope/services/models/sitti.py
#
# Field extraction rules for "Sitti": a French payslip PDF (bulletin de paie,
# S.A.S. SITTI, Sage template, CCN Bureaux d'études techniques - Syntec). Like
# Apside/Mosica/Ricoh the source is a flattened scan with no text layer (see
# extraction.py's OCR fallback), so everything works over OCR'd text.
#
# The document is one page but its header is a dense two-column grid
# (employer block on the left, an identity grid on the right) that the OCR
# glues line by line, and the period / payment box at the top-right is torn
# off and lands as detached lines *above* the "BULLETIN DE PAIE" line. So
# header fields anchor on their own label where the value is adjacent, and
# fall back to matching the value by its shape (a date, the 13+2 digit NIR,
# the SIRET pair) where the flattening pulled label and value apart. Values
# are kept as read; where a glued right-column intrudes on a left-column
# value it's cut off, but genuine OCR damage is left for downstream review.
#
# Two independent parts (same public interface as the other models):
#   - extract_fields(): single key/value fields (employer, worker, contract,
#     header refs, leave counters, abattements)
#   - extract_earnings_table(): the "Désignation" table (codes + amounts,
#     inline Total Brut / Total Cotisations, bottom cumuls grid).

import re

NUMBER_RE = re.compile(r"-?\d+,\d{2,3}")


def _to_float(s: str) -> float:
    return float(s.replace(" ", "").replace(".", "").replace(",", "."))


# --- fixed key/value fields -------------------------------------------------


def extract_fields(text: str) -> list[tuple[str, str | None]]:
    """Extract the fixed set of key/value fields from a Sitti payslip."""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    joined = "\n".join(lines)

    fields = []
    fields += _extract_employer_block(joined)
    fields += _extract_identity_grid(joined)
    fields += _extract_worker_block(joined)
    fields += _extract_header_refs(joined)
    fields += _extract_counters_block(joined)
    return fields


def _extract_employer_block(joined: str) -> list[tuple[str, str | None]]:
    fields = []

    def add(label, pattern, flags=0):
        m = re.search(pattern, joined, flags)
        fields.append((label, m.group(1).strip() if m else None))

    # Name: "S.A.S. SITTI" / OCR "SAS. SITTI".
    add("Nom employeur", r"S\.?A\.?S\.?[.\s]+([A-ZÉÈ][A-ZÉÈ]+)")
    add("SIRET", r"N[°ºo.]?\s*SIRET\s*:\s*(\d+\s+\d+)")
    add("APE/NAF", r"APE\s*/?\s*NAF\s*:\s*(\S+)")

    street = re.search(r"(?m)^(Rue\s+[A-ZÉÈ][A-Za-zÉÈéè]+(?:\s+[A-Za-zÉÈéè]+)?)", joined)
    m_city = re.search(r"\b(\d{5}\s+[A-ZÉÈ][A-Za-z]+(?:\s+[A-ZÉÈ][A-Za-z]+)?)", joined)
    parts = [g.group(1).strip() for g in (street, m_city) if g]
    fields.append(("Adresse employeur", ", ".join(parts) or None))

    # URSSAF number: the "<3 digits> <long digits>" line under "URSSAF".
    m = re.search(r"(?m)^URSSAF\b.*\n\s*(\d{3}\s+\d{6,})", joined)
    fields.append(("N° URSSAF", m.group(1).strip() if m else None))

    return fields


def _extract_identity_grid(joined: str) -> list[tuple[str, str | None]]:
    # The identity grid's value row is glued onto the employer street line and
    # reads, in order: Matricule, (Niveau), Coefficient, (Indice), Ancienneté,
    # N° de Sécurité Sociale. Niveau/Indice are blank on these payslips. The
    # row is located by the NIR (13 digits + 2-digit key) at its end.
    fields = []

    nir = re.search(r"(\d{13})\s+(\d{2})\b", joined)
    fields.append(
        ("N° Sécurité Sociale", f"{nir.group(1)} {nir.group(2)}" if nir else None)
    )

    if nir:
        row = next(
            (line for line in joined.splitlines()
             if re.search(r"\d{13}\s+\d{2}\b", line)),
            "",
        )
        mc = re.search(r"\b(\d{4})\b\s*\|?\s*(\d{3})\b", row)
        fields.append(("Matricule", mc.group(1) if mc else None))
        fields.append(("Coefficient", mc.group(2) if mc else None))
    else:
        fields.append(("Matricule", None))
        fields.append(("Coefficient", None))

    m = re.search(r"(\d+)\s*an.{0,8}?(\d+)\s*mois", joined)
    fields.append(
        ("Ancienneté", f"{m.group(1)} an(s) et {m.group(2)} mois" if m else None)
    )

    add = lambda label, pat, f=0: fields.append(
        (label, (lambda mm: mm.group(1).strip() if mm else None)(re.search(pat, joined, f)))
    )
    add("Position", r"Position\s+(\d(?:\.\d+)+)")
    add("Horaire", r"Position\s+\S+\s+(\d{1,3},\d{3,4})")

    # Catégorie / Emploi occupé: on the "<cp> <ville> <cat> <emploi>" line,
    # both read as the same value here ("CHARGE DE CLIENTELE") repeated.
    m = re.search(r"(?m)^\d{5}\s+\S+\s+\S+\s+([A-ZÉÈ].+)$", joined)
    cat_emploi = m.group(1).strip() if m else None
    if cat_emploi:
        cat_emploi = re.sub(r"\s*\|\s*$", "", cat_emploi).strip()
        words = cat_emploi.split()
        if len(words) % 2 == 0 and words[: len(words) // 2] == words[len(words) // 2:]:
            cat_emploi = " ".join(words[: len(words) // 2])
    fields.append(("Catégorie", cat_emploi))
    fields.append(("Emploi occupé", cat_emploi))

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
    street = re.search(r"\b(\d+\s+(?:Avenue|Rue|Bd|Boulevard|Av|Impasse|Place|Chemin|All[ée]e)\b[^\n|]*)",
                       after, re.IGNORECASE)
    # Worker city is glued to the "D.I.F. (heures) ..." row (right column).
    city = re.search(r"D\.?[IL]\.?F\.?.*?(\d{5}\s+[A-ZÉÈ][A-Za-zÉÈ]+)", joined)
    parts = [g.group(1).strip() for g in (street, city) if g]
    fields.append(("Adresse travailleur", " ".join(parts) or None))

    return fields


def _extract_header_refs(joined: str) -> list[tuple[str, str | None]]:
    # Period / payment box: OCR'd as a detached block. "Période du" is often
    # mangled ("Pérodedu"), the start date with it; the "au" date and the
    # payment date/mode land on their own lines. Matched loosely / by shape.
    fields = []

    period_line = next(
        (l for l in joined.splitlines() if "rode" in l.lower() or "riode" in l.lower()),
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
    # Abattement mensuel / cumulé pair.
    fields = []

    m = re.search(r"D\.?[IL]\.?F\.?\s*\(heures\)\s+([\d,]+)\s+([\d,]+)\s+([\d,]+)", joined)
    if m:
        for label, value in zip(("D.I.F. acquis", "D.I.F. reste", "D.I.F. pris"), m.groups()):
            fields.append((label, value))
    m = re.search(r"(?m)^Cong[ée]s\s+([\d,]+)\s+([\d,]+)\s*\|?\s*([\d,]+)", joined)
    if m:
        for label, value in zip(("Congés acquis", "Congés reste", "Congés pris"), m.groups()):
            fields.append((label, value))

    # Abattement: the two numbers on the line following the "Abat mensuel /
    # Abat cumulé" header. On the final payslip the cumulé can lose its comma.
    am = re.search(r"Abat\s*cumul[ée]", joined)
    abat = []
    if am:
        region = joined[am.end():am.end() + 140]
        abat = re.findall(r"(?<![/\d])\d+,\d{2,4}|(?<![/\d])\d{6,}(?![/\d])", region)
    fields.append(("Abattement mensuel", abat[0] if len(abat) > 0 else None))
    fields.append(("Abattement cumulé", abat[1] if len(abat) > 1 else None))

    return fields


# --- "Désignation" earnings table ------------------------------------------
#
# Pure regex over flattened OCR text. Each row starts with a numeric code
# (10, 19, 2100 ... 9000), possibly wrapped in OCR punctuation ("2100!",
# "7900}", "4570)"). Column naming uses the same payslip invariant as Apside
# (Retenue = Base x Taux / 100): a line reading as Base + (Taux, Retenue)
# pairs is named Base / Taux / Retenue, two pairs -> (sal) then (patr). Every
# other line's amounts are numbered "Montant N" - Gain vs Retenue and the
# Retenue(+)/Retenue(-) split can't be recovered from flattened text. The two
# inline subtotals (Total Brut, Total Cotisations) are emitted as ordinary
# rows; the bottom cumuls grid is appended keyed "Synthèse".

# Code: optional leading OCR noise, then 1-4 digits, then an OCR separator.
CODE_LINE_RE = re.compile(r"^[\s§|.!)\]}]*(\d{1,4})\s*[|\])},!.:*]*\s+(.+)$")

INVARIANT_TOLERANCE = 0.02

SUMMARY_LABELS = ["Total Brut", "Total Cotisations"]

CUMUL_COLUMNS = [
    "Salaire brut", "Net imposable", "Charges salariales", "Charges patronales",
    "Heures travaillées", "Heures sup.", "Avantages en nature", "Net à payer",
]


def _clean_label(label: str) -> str:
    label = " ".join(label.split())
    return label.strip("[]|()€:.,;*! ").strip()


def _name_cotisation_line(nums: list[str]) -> list[tuple[str, str]] | None:
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

    cells = [("Base", nums[0])]
    if len(pairs) == 1:
        cells.append(("Taux", pairs[0][0]))
        cells.append(("Retenue", pairs[0][1]))
    else:
        suffixes = ["(sal)", "(patr)"]
        for idx, (taux, retenue) in enumerate(pairs):
            suffix = suffixes[idx] if idx < len(suffixes) else str(idx + 1)
            cells.append((f"Taux {suffix}", taux))
            cells.append((f"Retenue {suffix}", retenue))
    return cells


def _parse_table_line(code: str, rest: str) -> list[tuple[str, str, str]]:
    nums = NUMBER_RE.findall(rest)
    label = _clean_label(NUMBER_RE.sub(" ", rest))

    results = []
    if label:
        results.append((code, "Désignation", label))

    named = _name_cotisation_line(nums)
    if named is not None:
        for column, value in named:
            results.append((code, column, value))
    else:
        for i, value in enumerate(nums, start=1):
            results.append((code, f"Montant {i}", value))
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
        Cotisation lines get named Base / Taux / Retenue (with (sal)/(patr)
        suffixes only when both parts are present); every other line's amounts
        are numbered "Montant N". Total Brut / Total Cotisations are ordinary
        rows keyed on their label. The bottom cumuls grid is appended keyed
        "Synthèse".
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
    for line in section:
        total = next(
            (lbl for lbl in SUMMARY_LABELS if line.lower().startswith(lbl.lower())),
            None,
        )
        if total is not None:
            values = NUMBER_RE.findall(line[len(total):])
            if total == "Total Brut":
                table_lines.append(("Total Brut", "Gain (sal)", values[0] if values else None))
            else:
                cols = ["Retenue (sal)", "Retenue (patr)"]
                for col, value in zip(cols, values):
                    table_lines.append(("Total Cotisations", col, value))
                if not values:
                    table_lines.append(("Total Cotisations", "Retenue (sal)", None))
            continue

        m = CODE_LINE_RE.match(line)
        if not m or not NUMBER_RE.search(m.group(2)):
            continue
        table_lines += _parse_table_line(m.group(1), m.group(2))

    if not table_lines:
        return [], []

    joined = "\n".join(lines)
    for label, value in _extract_cumuls_block(joined):
        table_lines.append(("Synthèse", label, value))

    return table_lines, []