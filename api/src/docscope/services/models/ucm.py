# api/src/docscope/services/models/ucm.py
#
# Field extraction rules for "UCM": a Belgian payslip PDF (bulletin de
# paie). The layout has real columns (employer block, worker block,
# earnings table) but PDF text extraction flattens everything into plain
# lines with no coordinates. Every rule here is a regex anchored on a known
# French label, because the source document itself is in French - only the
# code (names, comments) is in English.
#
# Two independent parts:
#   - extract_fields(): single key/value fields (employer, worker, dates...)
#   - extract_earnings_table(): the "Remunerations" table + its summary block

import re
import unicodedata


def extract_fields(text: str) -> list[tuple[str, str | None]]:
    """Extract the fixed set of key/value fields from a UCM payslip."""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    joined = "\n".join(lines)

    fields = []
    fields += _extract_employer_block(joined)
    fields += _extract_worker_identity_block(joined)
    fields += _extract_worker_status_block(joined)

    return fields


def _extract_employer_block(joined: str) -> list[tuple[str, str | None]]:
    fields = []

    def add(label, pattern, flags=0):
        m = re.search(pattern, joined, flags)
        fields.append((label, m.group(1).strip() if m else None))

    add("Période", r"Période\s*(.+)")
    add("Date de calcul", r"Calculé le\s+(.+)")
    add("N° du bulletin", r"N° journal des paies\s+(.+)")

    # Employer name: the line right after the "Employeur" label.
    add("Nom employeur", r"Employeur\n(.+)")

    # Address: the lines between the employer name and the "Dossier" line.
    # The first .+? must be lazy, otherwise (with DOTALL) it also
    # swallows the employer name line itself.
    m = re.search(r"Employeur\n.+?\n(.+?)\nDossier", joined, re.DOTALL)
    address = " ".join(m.group(1).split("\n")) if m else None
    fields.append(("Adresse employeur", address))

    add("Numéro de dossier", r"Dossier\s+(.+?)\s+CONFIDENTIEL")

    return fields


def _extract_worker_identity_block(joined: str) -> list[tuple[str, str | None]]:
    # Worker / company block: the layout is two columns flattened into one.
    # Known left-hand labels are used as anchors; the value on their right
    # is captured, and whatever follows on the same line is a fragment of
    # the right-hand column.
    fields = []

    def add(label, pattern, flags=0):
        m = re.search(pattern, joined, flags)
        fields.append((label, m.group(1).strip() if m else None))

    add("Numéro d'entreprise", r"N° d'entreprise\s+(\S+)")
    add("Numéro ONSS", r"N° ONSS\s+(\S+)")

    # Identity: rest of the "N° d'entreprise" line, with the civility
    # prefix (Monsieur/Madame...) stripped off.
    m_identity = re.search(r"N° d'entreprise\s+\S+\s+(.+)", joined)
    identity = m_identity.group(1).strip() if m_identity else ""
    identity = re.sub(r"^(Monsieur|Madame|Mademoiselle)\s+", "", identity)
    parts = identity.split()
    last_name = parts[0] if parts else None
    first_name = " ".join(parts[1:]) if len(parts) > 1 else None
    fields.append(("Nom", last_name))
    fields.append(("Prénom", first_name))

    # Worker address: street from the rest of the ONSS line, postal
    # code + city from the rest of the "Allocations familiales" line.
    m_street = re.search(r"N° ONSS\s+\S+\s+(.+)", joined)
    m_city = re.search(r"Allocations familiales\s+(.+)", joined)
    street = m_street.group(1).strip() if m_street else ""
    city = m_city.group(1).strip() if m_city else ""
    worker_address = " ".join(x for x in (street, city) if x) or None
    fields.append(("Adresse", worker_address))

    return fields


def _extract_worker_status_block(joined: str) -> list[tuple[str, str | None]]:
    # Worker / status / counters block: 3 columns flattened into one line.
    # Each left-hand value is bounded by the middle-column label that
    # follows it on the same line.
    fields = []

    def add(label, pattern, flags=0):
        m = re.search(pattern, joined, flags)
        fields.append((label, m.group(1).strip() if m else None))

    add("Référence Dossier", r"Référence Dossier\s+(.+?)\s+Date d'entrée")
    add("Statut", r"Statut\s+(.+?)\s+Date de sortie")
    add("Fonction", r"Fonction\s+(.+?)\s+N° de contrat d'apprentissage")
    add("Commission paritaire", r"Commission paritaire\s+(.+?)\s+Date de naissance")
    add("Catégorie", r"Catégorie\s+(.+?)\s+Sexe")
    add("Temps de travail", r"Temps de travail\s+(.+?)\s+N.?reg nat")
    add("Rémunération", r"Rémunération\s+(.+?)\s+Etat civil")
    add("Lieu de travail", r"Lieu de travail\s+(.+?)\s+Personnes à charge")
    add("IBAN", r"IBAN\s+(.+?)\s+Enfant\(s\)")
    add("BIC bénéficiaire", r"BIC bénéficiaire\s+(.+?)\s+Autre\(s\)")

    # Middle column.
    add("Date d'entrée", r"Date d'entrée\s+(.+?)\s+Solde vacances")

    # "Date de sortie" is empty on an active worker: the " ... heures" value
    # that follows on the line belongs to the right-hand counter, not to
    # this date, so it must resolve to None rather than to that fragment.
    m = re.search(r"Date de sortie\s+(.*?)\s*\d[\d.,]*\s+heures", joined)
    end_date = m.group(1).strip() if m else None
    fields.append(("Date de sortie", end_date or None))

    add("Date de naissance", r"Date de naissance\s+(.+?)\s+\d[\d.,]*\s+heures")
    add("Sexe", r"Sexe\s+(.+)")
    add("Numéro de registre national", r"N.?reg nat\s+(.+)")
    add("Etat civil", r"Etat civil\s+(.+)")

    # Dependents: split across 3 lines (children / disabled / other).
    m1 = re.search(r"Personnes à charge\s+(.+)", joined)
    m2 = re.search(r"(Enfant\(s\) handicapé\(s\)\s*:\s*.+)", joined)
    m3 = re.search(r"(Autre\(s\)\s*:\s*.+)", joined)
    parts = [m.group(1).strip() for m in (m1, m2, m3) if m]
    fields.append(("Personnes à charge", " ".join(parts) or None))

    # Right-hand column (counters): each label is matched to the first
    # "... heures" value that follows it in the text.
    add("Solde vacances", r"Solde vacances\b.*?(\d[\d.,]*\s+heures)", re.DOTALL)
    add("Solde récupération", r"Solde récupérations\b.*?(\d[\d.,]*\s+heures)", re.DOTALL)

    return fields


# --- "Remunerations - avantages et retenues" table -------------------------
#
# Pure regex over flattened text (page.extract_text()), two passes.
#
# Pass 1 (table): lines anchored on a ####.## code, cut short as soon as a
# marker (1-2 uppercase letters) followed by a known summary label appears,
# then read right to left (amount, indicator, remaining decimals =
# Jours/Heures/Unites in that order).
#
# Pass 2 (summary): key/value anchored on the fixed labels, tolerant to OCR
# variants (accents/case ignored).
#
# Known limitation: flattening sometimes glues a summary value onto a table
# line (same visual height). Pass 2 recovers it via the "spillover" value
# identified by pass 1 on that line. When this isn't enough (OCR noise like
# isolated "=" or "EE -"), the table label can stay slightly polluted -
# accepted, not guessed.

NUMBER_RE = re.compile(r"-?\d{1,3}(?:\.\d{3})*,\d{2}")
ISOLATED_LETTER_RE = re.compile(r"\b[A-G]\b")
CODE_LINE_RE = re.compile(r"^(\d{4}\.\d{2})\s+(.*)$")

SUMMARY_LABELS = [
    "Brut ONSS", "ONSS travailleur", "Brut non ONSS", "Cotis. Diverses",
    "Imposable", "Précompte", "Net à payer", "Net", "Divers",
    "Charges patronales",
]


def _strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


def _cut_at_summary_marker(rest):
    """Cut `rest` short as soon as a marker (1-2 uppercase letters) followed
    by a known summary label appears. The marker is required to avoid
    confusing a summary label ("ONSS travailleur") with the same word inside
    a table label (e.g. code 6506.00, "ONSS travailleur de base")."""
    rest_norm = _strip_accents(rest)
    cut_positions = []
    for label in SUMMARY_LABELS:
        label_norm = _strip_accents(label)
        m = re.search(r"[A-Z]{1,2}\s+" + re.escape(label_norm), rest_norm, re.IGNORECASE)
        if m:
            cut_positions.append(m.start())
    if not cut_positions:
        return rest
    return rest[: min(cut_positions)].rstrip()


def _parse_table_line(rest):
    """Parse an already-cut table line (code stripped). Returns a dict with
    label/jours/heures/unites/indicator/amount, plus `spillover`: an extra
    decimal on this line that actually belongs to a neighboring summary
    value glued on by the flattening (used by pass 2)."""
    decimals = list(NUMBER_RE.finditer(rest))
    letters = list(ISOLATED_LETTER_RE.finditer(rest))
    letter = letters[-1] if letters else None

    days = hours = units = amount = spillover = None
    indicator = letter.group() if letter else None

    if letter:
        before = [d for d in decimals if d.start() < letter.start()]
        after = [d for d in decimals if d.start() > letter.start()]

        if len(before) == 2:
            # Days+Hours before the letter: any decimal after it is a
            # spillover, not an amount.
            days, hours = before[0].group(), before[1].group()
            if after:
                spillover = after[0].group()
        else:
            positional = [before[i].group() if i < len(before) else None for i in range(3)]
            days, hours, units = positional
            if after:
                amount = after[0].group()
            if len(after) > 1:
                spillover = after[1].group()
    elif len(decimals) == 2:
        # No letter, 2 decimals: the 1st is the line's amount, the 2nd is
        # a spillover.
        amount = decimals[0].group()
        spillover = decimals[1].group()
    elif decimals:
        # No letter: the last decimal is the amount, the rest (if any)
        # goes positionally into Jours/Heures/Unites.
        amount = decimals[-1].group()
        remaining = decimals[:-1]
        positional = [remaining[i].group() if i < len(remaining) else None for i in range(3)]
        days, hours, units = positional

    label = NUMBER_RE.sub(" ", rest)
    label = ISOLATED_LETTER_RE.sub(" ", label)
    label = " ".join(label.split())

    return {
        "label": label or None,
        "days": days,
        "hours": hours,
        "units": units,
        "indicator": indicator,
        "amount": amount,
        "spillover": spillover,
    }


def _extract_table_lines(lines):
    results = []
    for line in lines:
        m = CODE_LINE_RE.match(line)
        if not m:
            continue
        code, rest = m.group(1), m.group(2)
        r = _parse_table_line(_cut_at_summary_marker(rest))

        if r["label"]:
            results.append((code, "Libellé", r["label"]))
        if r["days"]:
            results.append((code, "Jours", r["days"]))
        if r["hours"]:
            results.append((code, "Heures", r["hours"]))
        if r["units"]:
            results.append((code, "Unités", r["units"]))
        if r["indicator"]:
            results.append((code, "Ind.", r["indicator"]))
        if r["amount"]:
            results.append((code, "Montant EUR", r["amount"]))
    return results


def _find_summary_value(window):
    """Look for a summary label's value in the text that follows its
    occurrence, up to the next known label. Skips table lines that have
    nothing "extra" to offer (their decimal is claimed by their own line),
    and recovers the spillover set aside on lines that have one - this is
    what glues a summary value that landed on a table line back together."""
    lines = [line.strip() for line in window.splitlines() if line.strip()]
    detached_sign = False

    for line in lines:
        if line == "-":
            detached_sign = True
            continue

        m_code = CODE_LINE_RE.match(line)
        if m_code:
            r = _parse_table_line(_cut_at_summary_marker(m_code.group(2)))
            if r["spillover"]:
                value = r["spillover"]
                if detached_sign and not value.startswith("-"):
                    value = "-" + value
                return value
            if detached_sign:
                return "-"
            continue

        number_m = NUMBER_RE.search(line)
        if number_m:
            value = number_m.group()
            if detached_sign and not value.startswith("-"):
                value = "-" + value
            rest = line[number_m.end():].strip()
            if rest[:3].upper() == "EUR":
                value += " EUR"
            return value

    return "-" if detached_sign else None


def _extract_summary_lines(lines):
    section_text = "\n".join(lines)
    section_norm = _strip_accents(section_text)

    occurrences = []
    for label in SUMMARY_LABELS:
        label_norm = _strip_accents(label)
        if label == "Divers":
            # "Divers +" and "Divers -" share the same source word: look
            # for ALL occurrences (marked or not) to avoid missing either
            # one; the actual sign is resolved later, from the found value.
            for m in re.finditer(r"\bDivers\b", section_norm, re.IGNORECASE):
                occurrences.append((m.start(), m.end(), label))
            continue
        marked_pattern = re.compile(r"[A-Z]{1,2}\s*" + re.escape(label_norm), re.IGNORECASE)
        plain_pattern = re.compile(re.escape(label_norm), re.IGNORECASE)
        m = marked_pattern.search(section_norm) or plain_pattern.search(section_norm)
        if m:
            occurrences.append((m.start(), m.end(), label))

    occurrences.sort(key=lambda o: o[0])

    results = []
    for i, (start, end, label) in enumerate(occurrences):
        window_end = occurrences[i + 1][0] if i + 1 < len(occurrences) else len(section_text)
        value = _find_summary_value(section_text[end:window_end])
        if label == "Divers":
            label = "Divers -" if (value and value.startswith("-")) else "Divers +"
        results.append(("Synthèse", label, value))

    return results


def extract_earnings_table(text: str):
    """Extract the "Remunerations - avantages et retenues" section from the
    flattened text (pure regex, no coordinates).

    Returns (table_lines, summary_lines):
      - table_lines: (code, "Libellé"|"Jours"|"Heures"|"Unités"|"Ind."|
        "Montant EUR", value) - one entry per non-empty cell.
      - summary_lines: ("Synthèse", label, value).
    Returns ([], []) if the section isn't found.
    """
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    start = next(
        (i for i, line in enumerate(lines) if _strip_accents(line).lower().startswith("remunerations")),
        None,
    )
    if start is None:
        return [], []
    section_lines = lines[start:]
    return _extract_table_lines(section_lines), _extract_summary_lines(section_lines)
