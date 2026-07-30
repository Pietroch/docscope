# api/src/docscope/services/payslip_mosica.py
#
# Adapter: turns a document's flat (label, value) fields into the structured
# "payslip" object the mosica HTML template consumes. Source bulletin is
# produced by EBP Informatique.
#
# Design notes (same contract as payslip_apside / ricoh / sitti)
# -------------------------------------------------------------
# * Single pass over the flat fields, nothing raises, every deviation is
#   appended to `anomalies` and surfaced by the front.
# * Lines have NO code here: the "<x> — <attr>" key is the rubrique's own
#   label ("Salaire de base — Base"). Nothing sortable, so arrival order wins,
#   as on ricoh. One exception is pinned by LINE_ORDER_HINTS below.
# * Six value columns: Base / Taux / Gain / Retenue on the employee side, then
#   Taux / Montant under "Part patronale".
# * This adapter does one arithmetic cross-check (the employee-side total). It
#   is the only one that holds on the reference bulletin; see CHECK_RETENUE_TOTAL.

import re
from collections import OrderedDict

# --- simple (label -> path) mapping ---------------------------------------
SIMPLE_MAP = {
    "Nom employeur":       ("employer", "name"),
    "SIRET":               ("employer", "siret"),
    "NAF":                 ("employer", "naf"),
    "Nom URSSAF":          ("urssaf", "name"),
    "Nom travailleur":     ("employee", "name"),
    "Bulletin n°":         ("meta", "docNumber"),
    "Mode de paiement":    ("payment", "mode"),
    "Date de paiement":    ("payment", "date"),
    "Banque":              ("payment", "banque"),
    "IBAN":                ("payment", "iban"),
    "Coût employeur":      ("recap", "employerCost"),
    "Allègement cotisations": ("recap", "allegement"),
    # Absent from 2018-vintage payslips - present from 2020 onward.
    "Net à payer avant impôt sur le revenu": ("recap", "netAvantImpot"),
    "Dont évolution de la rémunération liée à la suppression des "
    "cotisations salariales chômage et maladie": ("recap", "evolutionRemuneration"),
    "Date d'entrée":       ("contract", "dateEntree"),
    "Date ancienneté":     ("contract", "dateAnciennete"),
    "Nature d'emploi":     ("contract", "natureEmploi"),
    "Statut catégoriel":   ("contract", "statutCategoriel"),
    "Position":            ("contract", "position"),
    "Niveau":              ("contract", "niveau"),
    "N° S.S.":             ("contract", "numSS"),
    "Echelon":             ("contract", "echelon"),
    "Service":             ("contract", "service"),
    "Coefficient":         ("contract", "coefficient"),
    "CCN":                 ("contract", "ccn"),
}

ADDRESS_MAP = {
    "Adresse employeur":  ("employer", "addressLines"),
    "Adresse URSSAF":     ("urssaf", "addressLines"),
    "Adresse travailleur": ("employee", "addressLines"),
}

# Rubrique-line attribute tokens -> key in a line dict.
LINE_ATTR = {
    "Libellé":     "label",
    "Base":        "base",
    "Taux":        "taux",
    "Gain":        "gain",
    "Retenue":     "retenue",
    "Taux (patr)": "tauxPatr",
    "Part patr":   "partPatr",
}

# "<rubrique label> — <attr>". The code slot holds the label itself, which can
# contain hyphens, commas and apostrophes but never an em dash, so the split is
# unambiguous. Checked after the "(scope)" patterns below all the same.
LINE_RE = re.compile(r"^(?P<code>.+?)\s+—\s+(?P<attr>.+)$")

MONTANT_RE = re.compile(r"^Montant\s+(?P<idx>\d+)$")

# Where "Montant N" values land, keyed by how many the line carries.
# The only user on the reference bulletin is "Famille - Sécurité Sociale", and
# the arithmetic pins the columns: 2 334,00 x 3,450% = 80,52, i.e.
# Base / Taux (patronal) / Montant (patronal). Note this differs from the SITTI
# adapter's table — "Montant N" is positional, so it is per-template.
# Every use still raises an anomaly.
MONTANT_FALLBACK = {
    3: ("base", "tauxPatr", "partPatr"),
}

# Rubriques the bulletin sets in bold as a genuine total line.
TOTAL_LABELS = {"Total des cotisations et contributions"}

# The flat field order puts the cotisation total 4th, right after "Total Brut
# SS", whereas the bulletin prints it below the last cotisation. No field
# carries that information, so the position is pinned by label: {label to
# move: (candidate anchors to sit in front of, tried in order)}. The anchor
# differs by scan era: 2018 payslips have "Net imposable" as its own
# rubrique line right after the cotisation detail; from 2020 onward "Net
# imposable" only exists in the Synthèse cumul band (see CUMUL_FIELD) and
# "Tickets restaurant" is the line that immediately follows the cotisations
# instead. Left unrepositioned, the total sits before its own cotisation
# lines, which also throws off the running-sum cross-check below.
LINE_ORDER_HINTS = {
    "Total des cotisations et contributions": ("Net imposable", "Tickets restaurant"),
}

# Cross-check the employee-side total against the sum of the cotisation
# lines above it - i.e. the lines between "Total Brut SS" (gross pay) and
# the cotisations total itself, not from the very top: a gross-side
# deduction line (e.g. "Absence pour congés payés", 2020-era) also carries
# a "retenue" but isn't a cotisation, and sits before "Total Brut SS".
# Only the employee side is checked: on the reference bulletin the retenues sum
# to 569,46 exactly, while the employer column sums to 977,16 against a
# declared 1 172,82. That 195,66 gap is EBP's own (contributions folded into
# the total without being itemised), not an extraction fault, so checking it
# would only produce noise.
GROSS_TOTAL_LABEL = "Total Brut SS"
CHECK_RETENUE_TOTAL = True
CHECK_TOLERANCE = 0.05

# "Congés — N-1 (acquis)" -> 3- or 4-row (acquis/pris/reste) by column grid.
# Columns vary by scan era: 2018 scans have N-1/N/RTT, 2020 onward add Anc.
# and rename RTT to "RTT (h)" - both RTT variants land on the same "rtt" key
# (the template decides which header to print, see build_payslip below).
LEAVE_RE = re.compile(r"^Congés\s+—\s+(?P<column>.+?)\s+\((?P<row>acquis|pris|reste)\)$")
LEAVE_COLUMN = {"N-1": "n1", "N": "n", "Anc.": "anc", "RTT": "rtt", "RTT (h)": "rtt"}
LEAVE_ROW_ORDER = ("acquis", "pris", "reste")
LEAVE_ROW_LABEL = {"acquis": "Acquis", "pris": "Pris", "reste": "Reste"}

# "Impôt sur le revenu — <attr>" -> its own small 1-row block, absent from
# 2018-vintage payslips (prélèvement à la source started in 2019). Checked
# before the generic LINE_RE below: "Taux personnalisé" and bare "Montant"
# aren't ordinary rubrique attrs (LINE_ATTR), so this block would otherwise
# raise "Attribut de ligne inconnu".
IMPOT_RE = re.compile(r"^Impôt sur le revenu\s+—\s+(?P<attr>Libellé|Base|Taux personnalisé|Montant)$")
IMPOT_ATTR = {"Libellé": "libelle", "Base": "base", "Taux personnalisé": "taux", "Montant": "montant"}

# "Synthèse — Salaire brut (mois)" -> bottom cumulative band. Same
# "Synthèse — <field> (<scope>)" convention as the other adapters
# (apside/ricoh/sitti).
CUMUL_RE = re.compile(r"^Synthèse\s+—\s+(?P<field>.+?)\s+\((?P<scope>mois|année)\)$")
CUMUL_FIELD = {
    "Plafond S.S.":   "plafondSS",
    "Heures trav.":   "heuresTrav",
    "Jours trav.":    "joursTrav",
    "Salaire brut":   "salaireBrut",
    # "Tranche A/B" (2018 scans) and "Tranche 1/2" (2020 onward) are the same
    # two cumul columns under a renamed label - both land on the same key so
    # the template doesn't need to know which era's wording it's reading.
    "Tranche A":      "trancheA",
    "Tranche 1":      "trancheA",
    "Tranche B":      "trancheB",
    "Tranche 2":      "trancheB",
    "Net imposable":  "netImposable",
    "Charges sal.":   "chargesSal",
    "Charges pat.":   "chargesPat",
}
CUMUL_SCOPE = {"mois": "mois", "année": "annee"}

# "Net à payer (EUR)" — the currency travels in the label, not the value.
NET_RE = re.compile(r"^Net à payer\s+\((?P<currency>[A-Z]{3})\)$")

PERIOD_SPLIT_RE = re.compile(r"\s+au\s+")


def _num(value: str):
    """"2 334,00" -> 2334.0. Returns None when not a number."""
    if not value:
        return None
    cleaned = value.replace("\u202f", "").replace("\u00a0", "").replace(" ", "")
    cleaned = cleaned.replace(",", ".")
    try:
        return float(cleaned)
    except ValueError:
        return None


def _blank_line():
    return {"label": "", "base": "", "taux": "", "gain": "", "retenue": "",
            "tauxPatr": "", "partPatr": "", "isTotal": False, "_montants": {},
            "_details": []}


def _blank_leave_row(row_label):
    return {"label": LEAVE_ROW_LABEL[row_label], "n1": "", "n": "", "anc": "", "rtt": ""}


def _blank_cumul_row():
    return {key: "" for key in CUMUL_FIELD.values()}


def build_payslip(fields: list[dict]) -> dict:
    """fields: [{"label": str, "value": str|None}, ...] in display order.
    Returns the structured payslip plus an "anomalies" list."""
    anomalies: list[str] = []

    # scaffold with empty defaults so the template never hits `undefined`
    payslip = {
        "meta": {"docTitle": "BULLETIN DE PAYE", "docNumber": ""},
        "employer": {"name": "", "addressLines": [], "siret": "", "naf": ""},
        "urssaf": {"name": "", "addressLines": []},
        "employee": {"civility": "", "name": "", "addressLines": []},
        "period": {"from": "", "to": ""},
        "payment": {"mode": "", "date": "", "banque": "", "iban": ""},
        "contract": {"dateEntree": "", "dateAnciennete": "", "natureEmploi": "",
                     "statutCategoriel": "", "position": "", "niveau": "",
                     "numSS": "", "echelon": "", "service": "",
                     "coefficient": "", "ccn": ""},
        "lines": [],
        # hasAnc / rttLabel: the leave grid's shape varies by scan era (see
        # LEAVE_RE above) - the template reads these to decide whether to
        # print an "Anc." column and which RTT header to use.
        "leave": {"rows": [], "hasAnc": False, "rttLabel": "RTT"},
        "recap": {"allegement": "", "employerCost": "",
                  "netAvantImpot": "", "evolutionRemuneration": ""},
        "netToPay": {"amount": "", "currency": ""},
        # Absent from 2018-vintage payslips - see IMPOT_RE.
        "impot": {"libelle": "", "base": "", "taux": "", "montant": ""},
        "cumuls": {"mois": _blank_cumul_row(), "annee": _blank_cumul_row()},
        "footerNotes": [
            "Pour faire valoir vos droits, conservez ce bulletin sans limitation de durée.",
            "Pour plus d'informations sur le bulletin clarifié, voir la rubrique "
            "dédiée sur www.service-public.fr",
        ],
    }

    lines_by_label = OrderedDict()   # rubrique label -> line dict, arrival order
    leave_rows = {}                  # row label (Acquis/Pris/Reste) -> row dict

    def norm(v):
        return "" if v is None else str(v)

    for f in fields:
        label = (f.get("label") or "").strip()
        value = norm(f.get("value")).strip()

        # --- addresses (kept raw, one line, like the sibling adapters) ----
        if label in ADDRESS_MAP:
            section, key = ADDRESS_MAP[label]
            payslip[section][key] = [value] if value else []
            continue

        # --- période "01/02/2018 au 28/02/2018" --------------------------
        if label == "Période de paye":
            parts = PERIOD_SPLIT_RE.split(value)
            payslip["period"]["from"] = parts[0].strip() if parts else ""
            payslip["period"]["to"] = parts[1].strip() if len(parts) > 1 else ""
            if len(parts) != 2:
                anomalies.append(f"« Période de paye » mal formée : {value!r} (attendu « X au Y »).")
            continue

        # --- "Net à payer (EUR)" ----------------------------------------
        m = NET_RE.match(label)
        if m:
            payslip["netToPay"]["amount"] = value
            payslip["netToPay"]["currency"] = m.group("currency")
            continue

        # --- simple direct mappings -------------------------------------
        if label in SIMPLE_MAP:
            section, key = SIMPLE_MAP[label]
            payslip[section][key] = value
            continue

        # --- congés grid ------------------------------------------------
        m = LEAVE_RE.match(label)
        if m:
            column = m.group("column").strip()
            if column not in LEAVE_COLUMN:
                anomalies.append(f"Colonne de congés inconnue : {label!r}.")
                continue
            row = leave_rows.setdefault(m.group("row"), _blank_leave_row(m.group("row")))
            row[LEAVE_COLUMN[column]] = value
            if column == "Anc.":
                payslip["leave"]["hasAnc"] = True
            elif column == "RTT (h)":
                payslip["leave"]["rttLabel"] = "RTT (h)"
            continue

        # --- bottom cumulative band -------------------------------------
        m = CUMUL_RE.match(label)
        if m:
            field = m.group("field").strip()
            scope = CUMUL_SCOPE[m.group("scope")]
            if field in CUMUL_FIELD:
                payslip["cumuls"][scope][CUMUL_FIELD[field]] = value
            else:
                anomalies.append(f"Champ de cumul inconnu : {label!r}.")
            continue

        # --- "Impôt sur le revenu" block, checked before the generic ----
        # rubrique-line rule below (its attrs aren't in LINE_ATTR).
        m = IMPOT_RE.match(label)
        if m:
            payslip["impot"][IMPOT_ATTR[m.group("attr")]] = value
            continue

        # --- rubrique lines "<label> — <attr>" --------------------------
        m = LINE_RE.match(label)
        if m:
            code = m.group("code").strip()
            attr = m.group("attr").strip()
            montant = MONTANT_RE.match(attr)
            if attr not in LINE_ATTR and attr != "Détail" and not montant:
                anomalies.append(f"Attribut de ligne inconnu : {label!r}.")
                continue
            if code not in lines_by_label:
                line = _blank_line()
                line["label"] = code       # the key IS the rubrique label
                line["isTotal"] = code in TOTAL_LABELS
                lines_by_label[code] = line
            # "Détail": code-less detail under the rubrique (e.g. the leave
            # dates on "Absence pour congés payés"). Several may occur for
            # one rubrique, so accumulate instead of overwriting - folded
            # into the label at the end, same as the apside adapter.
            if attr == "Détail":
                if value:
                    lines_by_label[code]["_details"].append(value)
            elif montant:
                lines_by_label[code]["_montants"][int(montant.group("idx"))] = value
            else:
                lines_by_label[code][LINE_ATTR[attr]] = value
            continue

        # --- anything else ----------------------------------------------
        anomalies.append(f"Intitulé non reconnu : {label!r}.")

    # ----- congés grid, fixed Acquis / Pris / Reste row order ------------
    if leave_rows:
        payslip["leave"]["rows"] = [
            leave_rows.get(row_label, _blank_leave_row(row_label))
            for row_label in LEAVE_ROW_ORDER
        ]

    # ----- fold detail lines (absence dates...) into the label -----------
    # Same rendering as apside: extra lines under the rubrique, same cell.
    for raw in lines_by_label.values():
        details = raw.pop("_details")
        if details:
            raw["label"] = "\n".join([raw["label"]] + details)

    # ----- place the extractor's unnamed "Montant N" values --------------
    for code, raw in lines_by_label.items():
        montants = raw.pop("_montants")
        if not montants:
            continue
        ordered = [montants[idx] for idx in sorted(montants)]
        targets = MONTANT_FALLBACK.get(len(ordered))
        if targets is None:
            anomalies.append(
                f"« {code} » : {len(ordered)} valeur(s) « Montant N » sans colonne "
                f"identifiée et aucun placement connu pour ce nombre "
                f"({' / '.join(ordered)}) — valeurs non affichées."
            )
            continue
        for key, val in zip(targets, ordered):
            raw[key] = val
        anomalies.append(
            f"« {code} » : {len(ordered)} valeur(s) « Montant N » sans colonne "
            f"identifiée, placée(s) par défaut en {' / '.join(targets)} "
            f"({' / '.join(ordered)}) — à confirmer."
        )

    # ----- an unqualified "Taux" that is really the employer rate ---------
    # A line carrying a rate and an employer amount but no employee retenue,
    # where base x rate lands on the employer amount, has had its rate written
    # to the wrong column by the extractor (observed on "Accidents du travail -
    # Maladies professionnelles": 2 334,00 x 1,000% = 23,34).
    for raw in lines_by_label.values():
        if not (raw["taux"] and raw["partPatr"]) or raw["tauxPatr"] or raw["retenue"]:
            continue
        base, taux, patr = _num(raw["base"]), _num(raw["taux"]), _num(raw["partPatr"])
        if None in (base, taux, patr):
            continue
        if abs(base * taux / 100 - patr) <= CHECK_TOLERANCE:
            raw["tauxPatr"], raw["taux"] = raw["taux"], ""
            anomalies.append(
                f"« {raw['label']} » : le taux {raw['tauxPatr']} était rangé en part "
                f"salariale ; base x taux = part patronale, déplacé côté patronal."
            )

    # ----- ordering: arrival order, minus the pinned exceptions ----------
    ordered_labels = list(lines_by_label)
    for moved, anchors in LINE_ORDER_HINTS.items():
        if moved not in ordered_labels:
            continue
        anchor = next((a for a in anchors if a in ordered_labels), None)
        if anchor is None:
            anomalies.append(
                f"« {moved} » ne peut pas être repositionnée : aucune des ancres "
                f"connues ({' / '.join(anchors)}) n'est présente."
            )
            continue
        ordered_labels.remove(moved)
        ordered_labels.insert(ordered_labels.index(anchor), moved)

    payslip["lines"] = [lines_by_label[label] for label in ordered_labels]

    # ----- cross-check the employee-side total ---------------------------
    if CHECK_RETENUE_TOTAL:
        running = 0.0
        counting = False
        for line in payslip["lines"]:
            if not counting:
                if line["label"] == GROSS_TOTAL_LABEL:
                    counting = True
                continue
            if line["isTotal"] and line["retenue"]:
                declared = _num(line["retenue"])
                if declared is not None and abs(declared - running) > CHECK_TOLERANCE:
                    anomalies.append(
                        f"« {line['label']} » : retenues cumulées des lignes "
                        f"au-dessus = {running:.2f}, total annoncé = {declared:.2f} "
                        f"(écart {declared - running:+.2f})."
                    )
                break
            running += _num(line["retenue"]) or 0.0

    # ----- flag expected-but-empty core fields --------------------------
    if not payslip["employer"]["name"]:
        anomalies.append("Nom employeur manquant.")
    if not payslip["employee"]["name"]:
        anomalies.append("Nom travailleur manquant.")
    if not payslip["netToPay"]["amount"]:
        anomalies.append("Net à payer manquant.")
    if not payslip["period"]["from"] or not payslip["period"]["to"]:
        anomalies.append("Période de paye manquante.")

    payslip["anomalies"] = anomalies
    return payslip