# api/src/docscope/services/payslip_ricoh.py
#
# Adapter: turns a document's flat (label, value) fields into the structured
# "payslip" object the ricoh HTML template consumes.
#
# Design notes (same contract as payslip_apside, different shape)
# ---------------------------------------------------------------
# * Same philosophy: single pass over the flat fields, nothing raises, every
#   deviation is appended to `anomalies` and surfaced by the front.
# * Difference vs apside #1 — NO re-sorting of the rubrique lines. Apside
#   codes are numeric and arrive out of order; Ricoh codes are alphanumeric
#   ("ISO", "UML", "A02") and the extraction order already matches the
#   bulletin's visual order. Sorting them would actively break it, so we keep
#   first-seen order.
# * Difference vs apside #2 — the money columns are Ricoh's: A payer /
#   A déduire on the employee side, Taux / Montant on the employer side.
# * Difference vs apside #3 — totals ("SALAIRE BRUT MENS", "TOT. COTIS.
#   SALAR") are *numbered* rows sitting in the normal columns, so they stay
#   ordinary lines carrying an `isTotal` flag (bold + rule in the template)
#   instead of the separate {"type": "total"} row apside needs.

import re
from collections import OrderedDict

# --- simple (label -> path) mapping ---------------------------------------
SIMPLE_MAP = {
    "Convention collective":  ("employer", "convCollLabel"),
    "Etablissement":          ("employer", "etablissement"),
    "Siret":                  ("employer", "siret"),
    "APE":                    ("employer", "ape"),
    "URSSAF":                 ("employer", "urssafDept"),
    "N° URSSAF":              ("employer", "urssafNumber"),
    "Nom travailleur":        ("employee", "name"),
    "Emploi":                 ("contract", "emploi"),
    "Affectation":            ("contract", "affectation"),
    "Date d'entrée":          ("contract", "dateEntree"),
    "Matricule":              ("contract", "matricule"),
    "Position":               ("contract", "position"),
    "Indice":                 ("contract", "indice"),
    "Ancienneté":             ("contract", "anciennete"),
    "N° SS":                  ("contract", "numSS"),
    "Salaire de base":        ("contract", "salaireBase"),
    "Horaire de référence":   ("contract", "horaireRef"),
    "Minimum conventionnel":  ("contract", "minimumConv"),
    "Mode de paiement":       ("payment", "mode"),
    "Date de paiement":       ("payment", "date"),
    "Banque":                 ("payment", "banque"),
    "IBAN":                   ("payment", "iban"),
}

# Rubrique-line attribute tokens -> key in a line dict.
LINE_ATTR = {
    "Libellé":        "label",
    "Nombre ou base": "base",
    "Taux salarial":  "tauxSal",
    "A payer":        "aPayer",
    "A déduire":      "aDeduire",
    "Taux (patr)":    "tauxPatr",
    "Part patr":      "montantPatr",
    # Observed variant (UZJ "Prévoyance Cad. TA"): the extractor drops the
    # "(patr)" qualifier when the line has no employee-side rate at all.
    # Both values do land in the employer columns on the bulletin — checked
    # against the salarial total, which excludes them.
    "Taux":    "tauxPatr",
    "Retenue": "montantPatr",
}

# "<code> — <attr>" e.g. "UML — Taux salarial". Codes are 2-4 uppercase
# alphanumerics, which keeps this regex from swallowing "Synthèse — ..." and
# "Congés acquis — ..." (both also use the em dash).
LINE_RE = re.compile(r"^(?P<code>[A-Z0-9]{2,4})\s+—\s+(?P<attr>.+)$")

# Trailing marker column on the bulletin, glued to the libellé by extraction
# ("Prime R/O EV" -> label "Prime R/O", marker "EV").
LINE_MARKERS = ("EV",)

# Codes rendered as emphasised subtotal rows. Kept as an explicit set rather
# than a heuristic; O/0 variants because the extractor's OCR mixes them.
TOTAL_CODES = {"A02", "AO2", "A08", "AO8"}

# "Synthèse — <field> (<scope>)".
SYNTH_RE = re.compile(
    r"^Synthèse\s+—\s+(?P<field>.+?)\s+\((?P<scope>mensuel|cumul annuel)\)$"
)
SYNTH_FIELD = {
    "Brut":              "brut",
    "Frais":             "frais",
    "Plafond TA":        "plafondTA",
    "Base SS plafonnée": "baseSSPlaf",
    "Net imposable":     "netImposable",
    "Net à payer":       "netAPayer",
}
SYNTH_SCOPE = {"mensuel": "mensuel", "cumul annuel": "cumul"}

# "Congés <row> — <column> (j)". Fixed 3-row (acquis/pris/solde) by 5-column
# grid; CET and RCP exist on the bulletin but are usually not extracted.
LEAVE_RE = re.compile(
    r"^Congés\s+(?P<row>acquis|pris|solde)\s+—\s+"
    r"(?P<column>Congés A-1|Congés A|RTT|CET|RCP)\s+\(j\)$"
)
LEAVE_COLUMN = {"Congés A": "congesA", "Congés A-1": "congesA1",
                "RTT": "rtt", "CET": "cet", "RCP": "rcp"}
LEAVE_ROW_LABEL = {"acquis": "Acq", "pris": "Pris", "solde": "Solde"}
LEAVE_ROW_ORDER = ("acquis", "pris", "solde")

# "RICOH France Page 1" -> the page marker bleeds into the employer name.
PAGE_SUFFIX_RE = re.compile(r"\s+Page\s+(?P<page>\d+)\s*$", re.IGNORECASE)

# "du 01/01/16 au 31/01/16" / "01/01/16 au 31/01/16".
PERIOD_SPLIT_RE = re.compile(r"\s+au\s+")


def _blank_line():
    return {"code": "", "label": "", "marker": "", "base": "", "tauxSal": "",
            "aPayer": "", "aDeduire": "", "tauxPatr": "", "montantPatr": "",
            "isTotal": False}


def _blank_leave_row(row_key):
    return {"label": LEAVE_ROW_LABEL[row_key], "congesA": "", "congesA1": "",
            "rtt": "", "cet": "", "rcp": ""}


def _blank_totals_row():
    return {"brut": "", "frais": "", "plafondTA": "", "baseSSPlaf": "",
            "netImposable": "", "netAPayer": ""}


def build_payslip(fields: list[dict]) -> dict:
    """fields: [{"label": str, "value": str|None}, ...] in display order.
    Returns the structured payslip plus an "anomalies" list."""
    anomalies: list[str] = []

    # scaffold with empty defaults so the template never hits `undefined`
    payslip = {
        "docTitle": "Bulletin de paie",
        "page": "",
        "employer": {"name": "", "addressLines": [], "etablissement": "",
                     "siret": "", "ape": "", "urssafDept": "",
                     "urssafNumber": "", "convCollLabel": ""},
        "employee": {"civility": "", "name": "", "addressLines": []},
        "period": {"from": "", "to": ""},
        "payment": {"mode": "", "date": "", "banque": "", "iban": ""},
        "contract": {"emploi": "", "affectation": "", "dateEntree": "",
                     "matricule": "", "position": "", "indice": "",
                     "anciennete": "", "numSS": "", "salaireBase": "",
                     "horaireRef": "", "minimumConv": ""},
        "lines": [],
        "totals": {"mensuel": _blank_totals_row(), "cumul": _blank_totals_row()},
        "netToPay": {"amount": "", "currency": "EUR"},
        "leave": {"rows": [], "rttTheorique": ""},
        "footerNote": ("Pour vous aider à faire valoir vos droits, conservez "
                       "ce bulletin de paie sans limitation de durée"),
    }

    # holding areas that need assembly after the pass
    lines_by_code = OrderedDict()   # code -> line dict, in first-seen order
    leave_rows = {}                 # row key (acquis/pris/solde) -> row dict

    def norm(v):
        return "" if v is None else str(v)

    for f in fields:
        label = (f.get("label") or "").strip()
        value = norm(f.get("value")).strip()

        # --- employer name, minus the page marker the extractor glues on ---
        if label == "Nom employeur":
            m = PAGE_SUFFIX_RE.search(value)
            if m:
                payslip["page"] = m.group("page")
                value = value[:m.start()].strip()
                anomalies.append(
                    f"« Nom employeur » contenait un marqueur de page "
                    f"(« Page {payslip['page']} »), retiré du nom."
                )
            payslip["employer"]["name"] = value
            continue

        # --- addresses (kept raw, one line, like the apside adapter) -------
        if label == "Adresse employeur":
            payslip["employer"]["addressLines"] = [value] if value else []
            continue
        if label == "Adresse travailleur":
            payslip["employee"]["addressLines"] = [value] if value else []
            continue

        # --- période "du 01/01/16 au 31/01/16" -----------------------------
        if label == "Période":
            raw = re.sub(r"^du\s+", "", value, flags=re.IGNORECASE)
            parts = PERIOD_SPLIT_RE.split(raw)
            payslip["period"]["from"] = parts[0].strip() if parts else ""
            payslip["period"]["to"] = parts[1].strip() if len(parts) > 1 else ""
            if len(parts) != 2:
                anomalies.append(f"« Période » mal formée : {value!r} (attendu « X au Y »).")
            continue

        # --- RTT théorique, free-standing note under the leave grid --------
        if label == "RTT théorique":
            payslip["leave"]["rttTheorique"] = value
            continue

        # --- simple direct mappings ----------------------------------------
        if label in SIMPLE_MAP:
            section, key = SIMPLE_MAP[label]
            payslip[section][key] = value
            continue

        # --- synthèse (TOTAUX band) ----------------------------------------
        m = SYNTH_RE.match(label)
        if m:
            field = m.group("field").strip()
            scope = SYNTH_SCOPE[m.group("scope")]
            if field in SYNTH_FIELD:
                payslip["totals"][scope][SYNTH_FIELD[field]] = value
            else:
                anomalies.append(f"Champ de synthèse inconnu : {label!r}.")
            continue

        # --- leave/congés grid ---------------------------------------------
        m = LEAVE_RE.match(label)
        if m:
            row = leave_rows.setdefault(m.group("row"), _blank_leave_row(m.group("row")))
            row[LEAVE_COLUMN[m.group("column")]] = value
            continue

        # --- rubrique lines "<code> — <attr>" ------------------------------
        m = LINE_RE.match(label)
        if m:
            code = m.group("code").strip()
            attr = m.group("attr").strip()
            if attr not in LINE_ATTR:
                anomalies.append(f"Attribut de ligne inconnu : {label!r}.")
                continue
            if code not in lines_by_code:
                line = _blank_line()
                line["code"] = code
                line["isTotal"] = code in TOTAL_CODES
                lines_by_code[code] = line
            key = LINE_ATTR[attr]
            if key == "label":
                value, marker = _split_marker(value)
                lines_by_code[code]["marker"] = marker
            lines_by_code[code][key] = value
            continue

        # --- anything else ---------------------------------------------------
        anomalies.append(f"Intitulé non reconnu : {label!r}.")

    # ----- lines: first-seen order is the bulletin order (see design notes)
    payslip["lines"] = list(lines_by_code.values())

    # ----- leave grid, fixed Acq/Pris/Solde row order ---------------------
    if leave_rows:
        payslip["leave"]["rows"] = [
            leave_rows.get(row_key, _blank_leave_row(row_key))
            for row_key in LEAVE_ROW_ORDER
        ]

    # ----- net à payer mirrors the monthly synthèse cell ------------------
    payslip["netToPay"]["amount"] = payslip["totals"]["mensuel"]["netAPayer"]

    # ----- flag expected-but-empty core fields ----------------------------
    if not payslip["employer"]["name"]:
        anomalies.append("Nom employeur manquant.")
    if not payslip["employee"]["name"]:
        anomalies.append("Nom travailleur manquant.")
    if not payslip["netToPay"]["amount"]:
        anomalies.append("Net à payer manquant.")
    if not payslip["period"]["from"] or not payslip["period"]["to"]:
        anomalies.append("Période de paie manquante (non extraite du bulletin).")

    payslip["anomalies"] = anomalies
    return payslip


def _split_marker(label_value: str):
    """"Prime R/O EV" -> ("Prime R/O", "EV"). The bulletin prints these in a
    narrow column of their own, the extractor appends them to the libellé."""
    for marker in LINE_MARKERS:
        suffix = " " + marker
        if label_value.endswith(suffix):
            return label_value[: -len(suffix)].rstrip(), marker
    return label_value, ""