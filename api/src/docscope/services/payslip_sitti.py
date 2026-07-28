# api/src/docscope/services/payslip_sitti.py
#
# Adapter: turns a document's flat (label, value) fields into the structured
# "payslip" object the sitti HTML template consumes.
#
# Design notes (same contract as payslip_apside / payslip_ricoh)
# --------------------------------------------------------------
# * Single pass over the flat fields, nothing raises, every deviation is
#   appended to `anomalies` and surfaced by the front.
# * Line ordering follows the apside rule, not the ricoh one: SITTI codes are
#   numeric, so lines are sorted by ascending numeric code and each code-less
#   "Total ..." row is re-anchored right after whichever numbered line
#   preceded it in the original stream. (Ricoh keeps arrival order because its
#   codes are alphanumeric and unsortable.) The reference extraction already
#   arrives sorted, so this is insurance against arrival-order drift rather
#   than a fix for it.
# * Two employer columns here: "Retenue (+)" and "Retenue (-)". The allègement
#   line (6350) is the only observed user of the (+) column, with a negative
#   value.
# * "Montant N" is the extractor's positional fallback when it cannot name a
#   column. It carries no column information, so it is placed by a documented
#   heuristic and ALWAYS raises an anomaly. See MONTANT_FALLBACK.

import re
from collections import OrderedDict

# --- simple (label -> path) mapping ---------------------------------------
SIMPLE_MAP = {
    "Nom employeur":        ("employer", "name"),
    "SIRET":                ("employer", "siret"),
    "APE/NAF":              ("employer", "apeNaf"),
    "N° URSSAF":            ("employer", "urssafNumber"),
    "Nom travailleur":      ("employee", "name"),
    "Date de paiement":     ("period", "paymentDate"),
    "Mode de paiement":     ("period", "paymentMode"),
    "Matricule":            ("contract", "matricule"),
    "Niveau":               ("contract", "niveau"),
    "Coefficient":          ("contract", "coefficient"),
    "Indice":               ("contract", "indice"),
    "Ancienneté":           ("contract", "anciennete"),
    "N° Sécurité Sociale":  ("contract", "numSS"),
    "Catégorie":            ("contract", "categorie"),
    "Emploi occupé":        ("contract", "emploi"),
    "Département":          ("contract", "departement"),
    "Position":             ("contract", "qualification"),
    "Horaire":              ("contract", "horaire"),
    "CCN":                  ("contract", "ccn"),
    "Commentaire":          ("comment", "text"),
    "Date de sortie":       ("comment", "exitDate"),
    "Abat mensuel":         ("abatement", "mensuel"),
    "Abat cumulé":          ("abatement", "cumule"),
}

# Cotisation-line attribute tokens -> key in a line dict.
LINE_ATTR = {
    "Désignation":       "label",
    "Nombre":            "nombre",
    "Base":              "base",
    "Taux (sal)":        "tauxSal",
    "Gain (sal)":        "gain",
    "Retenue (sal)":     "retenueSal",
    "Taux (patr)":       "tauxPatr",
    "Retenue + (patr)":  "retenuePlusPatr",
    "Retenue - (patr)":  "retenueMoinsPatr",
    # Observed variant (line 1319 "Prime vacances"): the extractor drops the
    # "(sal)" qualifier when the line has no employer side at all. Careful —
    # despite its name, the unqualified "Retenue" lands in the GAIN column, not
    # the Retenue one. Two independent checks on the reference bulletin:
    # the scan puts 45,61 under "Gain" alongside 1481,00 / 68,30 / 456,08, and
    # Total Brut only balances that way:
    #   1481,00 + 68,30 + 45,61 + 456,08 - 1343,66 = 707,33.
    "Taux":    "tauxSal",
    "Retenue": "gain",
}

# "Montant 1", "Montant 2"... — positional fallback, see MONTANT_FALLBACK.
MONTANT_RE = re.compile(r"^Montant\s+(?P<idx>\d+)$")

# Where "Montant N" values land, keyed by how many the line carries.
# HEURISTIC, fitted to the two lines that use it on the reference bulletin
# (1800 "Indem compensatrice congés" -> 1 value in Gain; 8000 "Tickets
# restaurants" -> 3 values in Nombre / Base / Retenue). It reproduces that
# bulletin exactly and is wrong the moment a line uses other columns, which is
# why every use raises an anomaly. Fix the extractor rather than this table.
MONTANT_FALLBACK = {
    1: ("gain",),
    3: ("nombre", "base", "retenueSal"),
}

# "<code> — <attr>" e.g. "2100 — Retenue - (patr)", "Total Brut — Gain (sal)".
LINE_RE = re.compile(r"^(?P<code>.+?)\s+—\s+(?P<attr>.+)$")

# Codes that are code-less total rows rather than numbered lines.
TOTAL_CODES = {"Total Brut", "Total Cotisations"}

# "Synthèse — <field> (<scope>)" -> bottom "Cumuls" band.
SYNTH_RE = re.compile(
    r"^Synthèse\s+—\s+(?P<field>.+?)\s+\((?P<scope>période|année)\)$"
)
SYNTH_FIELD = {
    "Salaire brut":        "brut",
    "Net imposable":       "netImposable",
    "Charges salariales":  "chargesSal",
    "Charges patronales":  "chargesPatr",
    "Heures travaillées":  "heuresTravaillees",
    "Heures sup.":         "heuresSup",
    "Avantages en nature": "avantagesNature",
    "Net à payer":         "netAPayer",
}
SYNTH_SCOPE = {"période": "periode", "année": "annee"}

# "D.I.F. acquis" / "Congés reste" ... -> 2-row x 3-column counter grid.
COUNTER_RE = re.compile(r"^(?P<row>D\.I\.F\.|Congés)\s+(?P<col>acquis|reste|pris)$")
COUNTER_ROW_LABEL = {"D.I.F.": "D.I.F. (heures)", "Congés": "Congés"}
COUNTER_ROW_ORDER = ("D.I.F.", "Congés")

# The bulletin prints three du/au slots under the counter grid.
LEAVE_DATE_SLOTS = 3

PERIOD_SPLIT_RE = re.compile(r"\s+au\s+")


def _blank_line():
    return {"num": "", "label": "", "nombre": "", "base": "", "tauxSal": "",
            "gain": "", "retenueSal": "", "tauxPatr": "", "retenuePlusPatr": "",
            "retenueMoinsPatr": "", "_montants": {}}


def _blank_counter_row(row_key):
    return {"label": COUNTER_ROW_LABEL[row_key], "acquis": "", "reste": "", "pris": ""}


def _blank_totals_row():
    return {"brut": "", "netImposable": "", "chargesSal": "", "chargesPatr": "",
            "heuresTravaillees": "", "heuresSup": "", "avantagesNature": "",
            "netAPayer": ""}


def build_payslip(fields: list[dict]) -> dict:
    """fields: [{"label": str, "value": str|None}, ...] in display order.
    Returns the structured payslip plus an "anomalies" list."""
    anomalies: list[str] = []

    # scaffold with empty defaults so the template never hits `undefined`
    payslip = {
        "docTitle": "BULLETIN DE PAIE",
        "employer": {"name": "", "addressLines": [], "siret": "", "apeNaf": "",
                     "urssafNumber": "", "urssafAddressLines": []},
        "employee": {"civility": "", "name": "", "addressLines": []},
        "period": {"from": "", "to": "", "paymentDate": "", "paymentMode": ""},
        "contract": {"matricule": "", "niveau": "", "coefficient": "",
                     "indice": "", "anciennete": "", "numSS": "",
                     "categorie": "", "emploi": "", "departement": "",
                     "qualification": "", "horaire": "", "ccn": ""},
        "counters": {"rows": [],
                     "leaveDates": [{"du": "", "au": ""} for _ in range(LEAVE_DATE_SLOTS)]},
        "comment": {"text": "", "exitDate": ""},
        "abatement": {"mensuel": "", "cumule": ""},
        "lines": [],
        "totals": {"periode": _blank_totals_row(), "annee": _blank_totals_row()},
        "netToPay": {"amount": "", "currency": ""},
        "footerNote": ("Pour vous aider à faire valoir vos droits, conservez "
                       "ce bulletin de paie sans limitation de durée."),
    }

    # holding areas that need assembly after the pass
    lines_by_code = OrderedDict()   # code -> line dict, in first-seen order
    line_order = []                 # sequence of codes as encountered
    counter_rows = {}               # row key (D.I.F./Congés) -> row dict

    def norm(v):
        return "" if v is None else str(v)

    for f in fields:
        label = (f.get("label") or "").strip()
        value = norm(f.get("value")).strip()

        # --- addresses (assembled, kept raw for now) ---------------------
        if label == "Adresse employeur":
            payslip["employer"]["addressLines"] = [value] if value else []
            continue
        if label == "Adresse travailleur":
            payslip["employee"]["addressLines"] = [value] if value else []
            continue
        # bare "URSSAF" carries the collecting office's address; the number
        # arrives separately as "N° URSSAF".
        if label == "URSSAF":
            payslip["employer"]["urssafAddressLines"] = [value] if value else []
            continue

        # --- période "01/04/17 au 04/04/17" -----------------------------
        if label == "Période":
            parts = PERIOD_SPLIT_RE.split(value)
            payslip["period"]["from"] = parts[0].strip() if parts else ""
            payslip["period"]["to"] = parts[1].strip() if len(parts) > 1 else ""
            if len(parts) != 2:
                anomalies.append(f"« Période » mal formée : {value!r} (attendu « X au Y »).")
            continue

        # --- simple direct mappings -------------------------------------
        if label in SIMPLE_MAP:
            section, key = SIMPLE_MAP[label]
            payslip[section][key] = value
            continue

        # --- synthèse (bottom "Cumuls" band) ----------------------------
        m = SYNTH_RE.match(label)
        if m:
            field = m.group("field").strip()
            scope = SYNTH_SCOPE[m.group("scope")]
            if field in SYNTH_FIELD:
                payslip["totals"][scope][SYNTH_FIELD[field]] = value
            else:
                anomalies.append(f"Champ de synthèse inconnu : {label!r}.")
            continue

        # --- D.I.F. / congés counter grid -------------------------------
        m = COUNTER_RE.match(label)
        if m:
            row = counter_rows.setdefault(m.group("row"), _blank_counter_row(m.group("row")))
            row[m.group("col")] = value
            continue

        # --- cotisation lines "<code> — <attr>" -------------------------
        m = LINE_RE.match(label)
        if m:
            code = m.group("code").strip()
            attr = m.group("attr").strip()
            montant = MONTANT_RE.match(attr)
            if attr not in LINE_ATTR and not montant:
                anomalies.append(f"Attribut de ligne inconnu : {label!r}.")
                continue
            if code not in lines_by_code:
                line = _blank_line()
                line["num"] = "" if code in TOTAL_CODES else code
                line["_code"] = code
                line["_is_total"] = code in TOTAL_CODES
                lines_by_code[code] = line
                line_order.append(code)
            if montant:
                lines_by_code[code]["_montants"][int(montant.group("idx"))] = value
            else:
                lines_by_code[code][LINE_ATTR[attr]] = value
            continue

        # --- anything else ----------------------------------------------
        anomalies.append(f"Intitulé non reconnu : {label!r}.")

    # ----- counter grid, fixed D.I.F. / Congés row order -----------------
    if counter_rows:
        payslip["counters"]["rows"] = [
            counter_rows.get(row_key, _blank_counter_row(row_key))
            for row_key in COUNTER_ROW_ORDER
        ]

    # ----- place the extractor's unnamed "Montant N" values --------------
    for code, raw in lines_by_code.items():
        montants = raw["_montants"]
        if not montants:
            continue
        ordered = [montants[idx] for idx in sorted(montants)]
        targets = MONTANT_FALLBACK.get(len(ordered))
        if targets is None:
            anomalies.append(
                f"Ligne {code} : {len(ordered)} valeur(s) « Montant N » sans "
                f"colonne identifiée et aucun placement connu pour ce nombre "
                f"({' / '.join(ordered)}) — valeurs non affichées."
            )
            continue
        for key, val in zip(targets, ordered):
            raw[key] = val
        anomalies.append(
            f"Ligne {code} : {len(ordered)} valeur(s) « Montant N » sans colonne "
            f"identifiée, placée(s) par défaut en {' / '.join(targets)} "
            f"({' / '.join(ordered)}) — à confirmer."
        )

    # ----- reorder by ascending numeric code -----------------------------
    # Anchor each total to the last numbered line seen before it in the
    # original stream, then sort the numbered lines and re-insert each total
    # right after its anchor.
    non_numeric = []

    def _code_sort_key(code):
        try:
            return (0, int(code))
        except ValueError:
            non_numeric.append(code)
            return (1, code)

    last_numeric_code = None
    anchor_of_total = {}
    for code in line_order:
        if lines_by_code[code]["_is_total"]:
            anchor_of_total[code] = last_numeric_code
        else:
            last_numeric_code = code

    numeric_codes_sorted = sorted(
        (code for code in line_order if not lines_by_code[code]["_is_total"]),
        key=_code_sort_key,
    )
    # _code_sort_key runs once per comparison, so dedupe before reporting.
    for code in dict.fromkeys(non_numeric):
        anomalies.append(f"Code de ligne non numérique : {code!r}.")

    final_order = [code for code, anchor in anchor_of_total.items() if anchor is None]
    for code in numeric_codes_sorted:
        final_order.append(code)
        final_order += [tc for tc, anchor in anchor_of_total.items() if anchor == code]

    # ----- build the ordered lines[] with total flag ---------------------
    # A total row uses {"type": "total", ...}: no N°, and only the three
    # columns the bulletin fills under a short rule.
    for code in final_order:
        raw = lines_by_code[code]
        if raw["_is_total"]:
            payslip["lines"].append({
                "type": "total",
                "label": raw["label"] or code,
                "gain": raw["gain"],
                "retenueSal": raw["retenueSal"],
                "retenueMoinsPatr": raw["retenueMoinsPatr"],
            })
        else:
            payslip["lines"].append({
                k: raw[k] for k in (
                    "num", "label", "nombre", "base", "tauxSal", "gain",
                    "retenueSal", "tauxPatr", "retenuePlusPatr",
                    "retenueMoinsPatr")
            })

    # ----- net à payer ---------------------------------------------------
    # The bulletin prints a single NET A PAYER cell straddling the Période and
    # Année rows; the extractor attributes it to Année. Take whichever scope
    # carries it so the template gets one value either way.
    payslip["netToPay"]["amount"] = (payslip["totals"]["annee"]["netAPayer"]
                                     or payslip["totals"]["periode"]["netAPayer"])

    # ----- flag expected-but-empty core fields --------------------------
    if not payslip["employer"]["name"]:
        anomalies.append("Nom employeur manquant.")
    if not payslip["employee"]["name"]:
        anomalies.append("Nom travailleur manquant.")
    if not payslip["netToPay"]["amount"]:
        anomalies.append("Net à payer manquant.")
    if not payslip["period"]["from"] or not payslip["period"]["to"]:
        anomalies.append("Période de paie manquante.")
    for line in payslip["lines"]:
        if line.get("type") != "total" and not line["label"]:
            anomalies.append(f"Ligne {line['num']} sans désignation.")

    payslip["anomalies"] = anomalies
    return payslip