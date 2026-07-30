# api/src/docscope/services/models/sdworx.py
#
# Field extraction rules for "SDWorx": a Belgian payslip PDF ("DÉCOMPTE
# SALARIAL", secrétariat social SD Worx n° 640). Two pages: page 1 carries
# the header blocks (employer left / period + personal + contract data
# right) and the main earnings table; page 2 carries the "Informatif" block
# and the "Détails jours et heures de travail" table.
#
# The source PDFs come in two flavours and both must work with the same
# rules:
#   - native text extraction: words keep their spaces;
#   - OCR of the scanned version: intra-label spaces are dropped, so
#     "Numéro employeur:" reads "Numéroemployeur:" and "01/10/2020 au
#     31/10/2020" reads "01/10/2020au31/10/2020". Inter-column spaces
#     survive, so column splitting still works.
# Every label anchor is therefore built with _lbl(), which makes the spaces
# between words optional. Values are kept as read; no maths, no OCR fixing.
#
# Two independent parts (same public interface as ucm/apside/mosica):
#   - extract_fields(): single key/value fields
#   - extract_earnings_table(): the "Code/Description/Jours/Heures/Montants"
#     table, its subtotals (montant brut, imposable, salaire net, versé),
#     the "Informatif" block and the working-time detail table.

import re

NUMBER_RE = re.compile(r"-?\d{1,3}(?:\.\d{3})*,\d{2}|-?\d+,\d{2}")
INT_RE = re.compile(r"(?<![\d,./-])\d{1,3}(?![\d,./-])")
CODE_LINE_RE = re.compile(r"^(\d{4})\s+(.*)$")


def _lbl(label: str) -> str:
    """Build a label pattern whose inter-word spaces are optional, so the
    same anchor matches the native text ("Numéro employeur") and the OCR
    of the scan ("Numéroemployeur")."""
    return r"\s*".join(re.escape(word) for word in label.split())


# Right-hand column content glued onto the employer block by the
# flattening: used as cut points, not as anchors.
_RIGHT_COLUMN_MARKERS = (
    _lbl("DÉCOMPTE SALARIAL"), _lbl("DECOMPTE SALARIAL"),
    _lbl("Période du"), _lbl("Periode du"),
    _lbl("Date de calcul"), _lbl("Extrait du compte"),
    _lbl("Données personnelles"), _lbl("Données du contrat"),
)
_CUT_RE = re.compile(r"\s*(?:" + "|".join(_RIGHT_COLUMN_MARKERS) + r")")


def _cut_right_column(line: str) -> str:
    """Drop whatever the right-hand column glued onto a left-column line."""
    return _CUT_RE.split(line, maxsplit=1)[0].strip()


def extract_fields(text: str) -> list[tuple[str, str | None]]:
    """Extract the fixed set of key/value fields from an SDWorx payslip."""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    joined = "\n".join(lines)

    fields = []
    fields += _extract_employer_block(lines, joined)
    fields += _extract_period_block(joined)
    fields += _extract_personal_block(joined)
    fields += _extract_contract_block(joined)
    fields += _extract_worker_block(lines, joined)
    fields += _extract_payment_block(joined)
    return fields


def _extract_employer_block(lines, joined) -> list[tuple[str, str | None]]:
    # Employer block = top-left corner: name, then 1-2 address lines, then
    # the "Numéro employeur" line. Its lines carry the right-hand column
    # ("DÉCOMPTE SALARIAL", "Période du ...") glued on, so each one is cut.
    fields = []

    name = _cut_right_column(lines[0]) if lines else None
    fields.append(("Nom employeur", name or None))

    address = []
    for line in lines[1:]:
        if re.match(_lbl("Numéro employeur") + r"\s*:", line) or re.match(
                _lbl("Numero employeur") + r"\s*:", line):
            break
        cut = _cut_right_column(line)
        if cut:
            address.append(cut)
    fields.append(("Adresse employeur", " ".join(address) or None))

    m = re.search(_lbl("Numéro employeur") + r"\s*:\s*(\S+)", joined)
    fields.append(("Numéro employeur", m.group(1) if m else None))

    # Dossier reference, on its own line under the header rule:
    # "<numéro employeur> / <numéro travailleur>".
    m = re.search(r"^([A-Z0-9]{4,})\s*/\s*(\d{4,})\s*$", joined, re.MULTILINE)
    fields.append(("Référence dossier", f"{m.group(1)} / {m.group(2)}" if m else None))

    return fields


def _extract_period_block(joined) -> list[tuple[str, str | None]]:
    fields = []

    # "Période du <date> au <date>" - "au" is glued to both dates on the scan.
    m = re.search(r"(\d{2}/\d{2}/\d{4})\s*au\s*(\d{2}/\d{2}/\d{4})", joined)
    fields.append(("Période", f"{m.group(1)} au {m.group(2)}" if m else None))

    m = re.search(_lbl("Date de calcul") + r"\s*:\s*(\S+?)\s*$", joined, re.MULTILINE)
    if not m:
        m = re.search(_lbl("Date de calcul") + r"\s*:\s*(\d{2}/\d{2}/\d{4})", joined)
    fields.append(("Date de calcul", m.group(1) if m else None))

    return fields


# Key/value pairs of the right-hand column: (output label, source label(s),
# accent-less OCR variants included).
_PERSONAL_ANCHORS = [
    ("NISS", ("NISS",)),
    ("État civil", ("État civil", "Etat civil")),
    ("Personnes à charge", ("Personnes à charge", "Personnes a charge")),
]
_CONTRACT_ANCHORS = [
    ("Numéro travailleur", ("Numéro travailleur", "Numero travailleur")),
    ("Statut", ("Statut",)),
    ("Fonction", ("Fonction",)),
]

# Any known label of these two blocks (plus their titles), used as the right
# bound of a value when the flattening glued the next pair onto the line.
_ALL_ANCHORS = ([a for _, anchors in _PERSONAL_ANCHORS + _CONTRACT_ANCHORS for a in anchors]
                + ["Données du contrat", "Donnees du contrat",
                   "Données personnelles", "Donnees personnelles"])
_NEXT_LABEL_RE = r"(?:" + "|".join(_lbl(a) for a in _ALL_ANCHORS) + r")\s*:"


def _labelled_value(joined: str, anchors: tuple[str, ...]) -> str | None:
    r"""Value of a "<label>: <value>" pair. The value ends at the end of its
    line ([^\n], not \s, so an empty value resolves to None instead of
    eating the next line) or earlier, at the next known label glued onto
    the same line by the flattening."""
    for anchor in anchors:
        m = re.search(_lbl(anchor) + r"\s*:[ \t]*([^\n]*)", joined)
        if not m:
            continue
        value = m.group(1)
        cut = re.search(r"\s*" + _NEXT_LABEL_RE, value)
        if cut:
            value = value[: cut.start()]
        return value.strip() or None
    return None


def _extract_personal_block(joined) -> list[tuple[str, str | None]]:
    # "Données personnelles" column.
    return [(label, _labelled_value(joined, anchors)) for label, anchors in _PERSONAL_ANCHORS]


def _extract_contract_block(joined) -> list[tuple[str, str | None]]:
    # "Données du contrat" column.
    return [(label, _labelled_value(joined, anchors)) for label, anchors in _CONTRACT_ANCHORS]


def _extract_worker_block(lines, joined) -> list[tuple[str, str | None]]:
    # Worker block: the "CONFIDENTIEL" marker, then the name, then the
    # street and the "<code postal> <ville>" lines. Nothing else sits at
    # that height, so the lines are taken as-is up to the next block
    # (the earnings table header, "Salaire mensuel de base").
    fields = []

    start = next((i for i, line in enumerate(lines)
                  if re.fullmatch(_lbl("CONFIDENTIEL"), line, re.IGNORECASE)), None)
    block = []
    if start is not None:
        for line in lines[start + 1:]:
            if re.match(_lbl("Salaire mensuel de base"), line):
                break
            block.append(_cut_right_column(line))
        block = [line for line in block if line]

    fields.append(("Nom travailleur", block[0] if block else None))
    fields.append(("Adresse travailleur", " ".join(block[1:3]) or None))

    return fields


def _extract_payment_block(joined) -> list[tuple[str, str | None]]:
    # Bottom recap line of page 1: "versé au compte IBAN: <iban> BIC: <bic>"
    # then the net amount in the "versé" box. The IBAN's groups of 4 are
    # glued together by the OCR: taken raw, not reformatted.
    fields = []

    m = re.search(_lbl("IBAN") + r"\s*:\s*((?:[A-Z]{2}\d{2})[\d ]*\d)", joined)
    fields.append(("IBAN", m.group(1).strip() if m else None))

    m = re.search(_lbl("BIC") + r"\s*:\s*([A-Z0-9]+)", joined)
    fields.append(("BIC", m.group(1) if m else None))

    # Net paid: the amount of the "versé" box, i.e. the last "€ <amount>".
    amounts = re.findall(r"€\s*(" + NUMBER_RE.pattern + ")", joined)
    fields.append(("Net versé (EUR)", amounts[-1] if amounts else None))

    # Table header line, above the earnings table.
    m = re.search(_lbl("Salaire mensuel de base") + r"\s*:\s*€?\s*("
                  + NUMBER_RE.pattern + ")", joined)
    fields.append(("Salaire mensuel de base", m.group(1) if m else None))

    m = re.search(_lbl("Catégorie salaire") + r"\s*:[ \t]*(\S*)", joined)
    fields.append(("Catégorie salaire", (m.group(1).strip() if m else None) or None))

    return fields


# --- earnings table ---------------------------------------------------------
#
# Main table (page 1): "Code | Description | Jours | Heures | Montants".
# Rows are anchored on a 4-digit code; the numbers are read right to left
# (Montant, then Heures, then Jours), because Jours/Heures are empty on the
# benefit-in-kind rows ("internet fixe ou mobile - atn": amount only).
# A code can legitimately appear twice (1626 as a benefit then as a
# deduction), so rows are keyed on "<code>" plus an occurrence suffix when
# needed, to avoid collapsing two distinct rows.
#
# Subtotal rows ("montant brut", "imposable", "salaire net") carry no code
# and go to summary_lines, together with the "versé" amount.
#
# Page 2 blocks are appended after the main table:
#   - "Informatif": two columns of <label> <amount> pairs, flattened onto
#     the same lines; pairs are read left to right.
#   - "Détails jours et heures de travail": same shape as the main table but
#     with no Montants column (the decimal is Heures) and 0xxx codes.

_TABLE_START = _lbl("Code") + r"\s+" + _lbl("Description")
_TABLE_END_MARKERS = (_lbl("versé"), _lbl("verse"), _lbl("Secrétariat social"),
                      _lbl("Secretariat social"), _lbl("Informatif"))
_SUBTOTAL_LABELS = ("montant brut", "imposable", "salaire net", "salaire brut")
# Same labels as cut points, spaces optional, for the variant where the
# flattening glues a subtotal row onto the end of the code row above it
# ("1350 salaire pour jour férié 1 7,50 97,73 montant brut 2.150,00").
_SUBTOTAL_RE = re.compile(
    r"\s+((?:" + "|".join(_lbl(lab) for lab in _SUBTOTAL_LABELS)
    + r")\s+-?[\d.]+,\d{2})\s*$", re.IGNORECASE)


def _row_numbers(rest: str, with_amount: bool = True):
    """Split the numeric tail of a row into (jours, heures, montant) and
    return the remaining text as the label. Read right to left: the last
    decimal is the amount (main table) or the hours (detail table)."""
    decimals = list(NUMBER_RE.finditer(rest))
    amount = heures = jours = None

    if with_amount:
        if decimals:
            amount = decimals[-1].group()
            decimals = decimals[:-1]
        if decimals:
            heures = decimals[-1].group()
            decimals = decimals[:-1]
    elif decimals:
        heures = decimals[-1].group()
        decimals = decimals[:-1]

    label = NUMBER_RE.sub(" ", rest)
    ints = list(INT_RE.finditer(label))
    if ints:
        jours = ints[-1].group()
        label = label[:ints[-1].start()] + " " + label[ints[-1].end():]
    label = " ".join(label.split())

    return label or None, jours, heures, amount


def _extract_main_table(lines):
    table_lines = []
    summary_lines = []
    seen = {}

    for line in lines:
        if any(re.match(mark, line) for mark in _TABLE_END_MARKERS):
            break

        m = CODE_LINE_RE.match(line)
        if not m:
            # Subtotal row: no code, label + amount on the same line.
            low = line.lower().replace(" ", "")
            if any(lab.replace(" ", "") in low for lab in _SUBTOTAL_LABELS):
                label, _, _, amount = _row_numbers(line)
                if amount:
                    summary_lines.append(("Synthèse", label or line, amount))
            continue

        code, rest = m.group(1), m.group(2)

        # A subtotal glued at the end of this row belongs to its own row.
        glued = _SUBTOTAL_RE.search(rest)
        if glued:
            rest = rest[: glued.start()]
            sub_label, _, _, sub_amount = _row_numbers(glued.group(1))
            if sub_amount:
                summary_lines.append(("Synthèse", sub_label or glued.group(1), sub_amount))

        label, jours, heures, amount = _row_numbers(rest)

        seen[code] = seen.get(code, 0) + 1
        key = code if seen[code] == 1 else f"{code} ({seen[code]})"

        if label:
            table_lines.append((key, "Libellé", label))
        if jours:
            table_lines.append((key, "Jours", jours))
        if heures:
            table_lines.append((key, "Heures", heures))
        if amount:
            table_lines.append((key, "Montant EUR", amount))

    return table_lines, summary_lines


_PAIR_RE = re.compile(r"([^\d\n][^\n]*?)\s+(-?\d{1,3}(?:\.\d{3})*,\d{2})")


def _extract_informatif(lines):
    """"Informatif" block (page 2): two columns of <label> <amount> pairs
    glued line by line. Pairs are read left to right, so the column they
    came from is not recorded. A label wrapped onto a second line (e.g.
    "Jours de vacances légales à prendre pour / l'employé 09/2021") stays
    attached to its own fragment - accepted, not guessed."""
    start = next((i for i, line in enumerate(lines)
                  if re.match(_lbl("Informatif"), line)), None)
    if start is None:
        return []

    rows = []
    for line in lines[start:]:
        if re.match(_lbl("Détails jours"), line) or re.match(_lbl("Details jours"), line):
            break
        for label, amount in _PAIR_RE.findall(line):
            label = " ".join(label.split())
            label = re.sub(r"^" + _lbl("Informatif") + r"\s*", "", label).strip()
            if label:
                rows.append(("Informatif", label, amount))
    return rows


def _extract_worktime_detail(lines):
    """"Détails jours et heures de travail" table (page 2): Code /
    Description / Jours / Heures / Équipe - no Montants column, so the
    decimal on the row is the hours. The trailing row without a code is
    the total."""
    start = next((i for i, line in enumerate(lines)
                  if re.match(_lbl("Détails jours"), line)
                  or re.match(_lbl("Details jours"), line)), None)
    if start is None:
        return []

    rows = []
    seen = {}
    for line in lines[start + 1:]:
        if re.match(_lbl("Secrétariat social"), line) or re.match(
                _lbl("Secretariat social"), line):
            break
        m = CODE_LINE_RE.match(line)
        if m:
            code, rest = m.group(1), m.group(2)
            label, jours, heures, _ = _row_numbers(rest, with_amount=False)
            seen[code] = seen.get(code, 0) + 1
            key = f"Détail {code}" if seen[code] == 1 else f"Détail {code} ({seen[code]})"
            if label:
                rows.append((key, "Libellé", label))
            if jours:
                rows.append((key, "Jours", jours))
            if heures:
                rows.append((key, "Heures", heures))
            continue
        # Total row: "<jours> <heures>" with no code and no label.
        label, jours, heures, _ = _row_numbers(line, with_amount=False)
        if not label and (jours or heures):
            if jours:
                rows.append(("Détail total", "Jours", jours))
            if heures:
                rows.append(("Détail total", "Heures", heures))
    return rows


def extract_earnings_table(text: str):
    """Extract the earnings table of an SDWorx payslip from the flattened
    text. Returns (table_lines, summary_lines):
      - table_lines: (code, "Libellé"|"Jours"|"Heures"|"Montant EUR", value)
        for the main table, then ("Informatif", label, value) pairs and the
        working-time detail rows ("Détail <code>", ...).
      - summary_lines: ("Synthèse", label, value) for the subtotals
        (montant brut, imposable, salaire net) and the "versé" amount.
    Returns ([], []) if the table isn't found.
    """
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    # The header may sit mid-line (the flattening can glue it to the
    # "Salaire mensuel de base" line above), hence search, not match; what
    # follows it on that line is part of the table.
    start = head = None
    for i, line in enumerate(lines):
        m = re.search(_TABLE_START, line)
        if m:
            start, head = i, line[m.end():].strip()
            break
    if start is None:
        return [], []

    section = lines[start + 1:]
    if head:
        # Drop the remaining column titles ("Jours Heures Montants") and
        # keep whatever real row content followed them.
        head = re.sub(r"^(?:" + r"|".join(
            _lbl(t) for t in ("Jours", "Heures", "Montants", "Équipe", "Equipe")) + r"|\s)+",
            "", head).strip()
        if head:
            section = [head] + section

    table_lines, summary_lines = _extract_main_table(section)

    # "versé" box amount (net paid), kept with the subtotals.
    joined = "\n".join(lines)
    amounts = re.findall(r"€\s*(" + NUMBER_RE.pattern + ")", joined)
    if amounts:
        summary_lines.append(("Synthèse", "versé", amounts[-1]))

    table_lines += _extract_informatif(lines)
    table_lines += _extract_worktime_detail(lines)

    return table_lines, summary_lines