# api/src/docscope/services/payslip_apside.py
#
# Adapter: turns a document's flat (label, value) fields into the structured
# "payslip" object the apside HTML template (v2) consumes.
#
# Design notes
# ------------
# * The template is hierarchical (employer / period / contract / employee /
#   leave / lines[] / totals / netToPay). The DB stores a flat list of
#   (label, value) pairs. This module is the only place that knows how to go
#   from one to the other, so the front stays a dumb renderer.
# * Cotisation lines are re-sorted by ascending numeric code: field arrival
#   order (DocumentField.id) doesn't always match the payslip's visual
#   order (e.g. a line like "5620" can be extracted after "8100"). Each
#   "Total ..." row has no code of its own, so it stays anchored right
#   after whichever numbered line preceded it in the original stream.
# * Nothing raises on bad/missing data: we render what we can and append a
#   human-readable string to `anomalies`, which the front surfaces.

import re
from collections import OrderedDict

# --- simple (label -> path) mapping ---------------------------------------
# Each entry says where a flat label lands in the structured object.
# Paths are ("section", "key"); a few need light post-processing (see below).
SIMPLE_MAP = {
    "Nom employeur":              ("employer", "name"),
    "SIRET":                      ("employer", "siret"),
    "NAF":                        ("employer", "naf"),
    "Cotisations URSSAF":         ("employer", "urssafRef"),
    "Convention collective":      ("employer", "convCollLabel"),
    "Date de paiement":           ("period", "paymentDate"),
    "Mode de paiement":           ("period", "paymentMode"),
    "Identifiant":                ("period", "identifiant"),
    "Ancienneté":                 ("period", "anciennete"),
    "Niveau":                     ("contract", "niveau"),
    "Coefficient":                ("contract", "coefficient"),
    "Emploi":                     ("contract", "emploi"),
    "Catégorie":                  ("contract", "categorie"),
    "Section":                    ("contract", "section"),
    "Matricule sécurité sociale": ("contract", "matriculeSS"),
    "Domiciliation":              ("contract", "domiciliation"),
    "Nom travailleur":            ("employee", "name"),
}

# Cotisation-line attribute tokens -> key in a line dict.
LINE_ATTR = {
    "Désignation":   "label",
    "Nombre":        "nombre",
    "Base":          "base",
    "Taux (sal)":    "tauxSal",
    "Gain (sal)":    "gain",
    "Retenue (sal)": "retenueSal",
    "Taux (patr)":    "tauxPatr",
    "Retenue (patr)": "retenuePatr",
}

# "<code> — <attr>"  e.g. "3010 — Retenue (patr)", "Total Brut — Gain (sal)".
# The code can be a numeric string ("3010") or a total label ("Total Brut",
# "Total Cotisations"); em dash (—) with surrounding spaces.
LINE_RE = re.compile(r"^(?P<code>.+?)\s+—\s+(?P<attr>.+)$")

# "Synthèse — <field> (<scope>)" or "Synthèse — Net à payer".
SYNTH_RE = re.compile(r"^Synthèse\s+—\s+(?P<field>.+?)(?:\s+\((?P<scope>période|année)\))?$")

SYNTH_FIELD = {
    "Heures travaillées":   "hoursWorked",
    "Heures supp.":         "hoursOvertime",
    "Brut fiscal":          "brutFiscal",
    "Base sécurité sociale": "baseSecuriteSoc",
    "Charges patronales":   "chargesPatronales",
    "Charges salariales":   "chargesSalariales",
    "Net imposable":        "netImposable",
}

# Codes that are totals rather than numbered lines.
TOTAL_CODES = {"Total Brut", "Total Cotisations"}

# "<column> (<row>)"  e.g. "RTT collaborateur (pris)", "Dates de congés
# (solde)". The leave/congés block is a fixed 3-row (pris/solde/acquis) by
# 4-column (RTT collaborateur/RTT employeur/Congés/Dates de congés) grid.
LEAVE_RE = re.compile(
    r"^(?P<column>RTT collaborateur|RTT employeur|Congés|Dates de congés)"
    r"\s+\((?P<row>pris|solde|acquis)\)$"
)
LEAVE_COLUMN = {
    "RTT collaborateur": "rttCollab",
    "RTT employeur": "rttEmployeur",
    "Congés": "conges",
}
LEAVE_ROW_LABEL = {"pris": "Pris", "solde": "Solde", "acquis": "Acquis"}
LEAVE_ROW_ORDER = ("pris", "solde", "acquis")


def _blank_line():
    return {"num": "", "label": "", "nombre": "", "base": "", "tauxSal": "",
            "gain": "", "retenueSal": "", "tauxPatr": "", "retenuePatr": ""}


def _blank_leave_row(row_key):
    return {"label": LEAVE_ROW_LABEL[row_key], "rttCollab": "", "rttEmployeur": "",
            "conges": "", "du": "", "au": ""}


def build_payslip(fields: list[dict]) -> dict:
    """fields: [{"label": str, "value": str|None}, ...] in display order.
    Returns the structured payslip plus an "anomalies" list."""
    anomalies: list[str] = []

    # scaffold with empty defaults so the template never hits `undefined`
    payslip = {
        "employer": {"name": "", "addressLines": [], "siret": "", "siretSuffix": "",
                     "naf": "", "cotisationsA": "URSSAF", "urssafRef": "",
                     "convCollCode": "", "convCollLabel": ""},
        "period": {"from": "", "to": "", "paymentDate": "", "paymentMode": "",
                   "identifiant": "", "anciennete": ""},
        "contract": {"niveau": "", "coefficient": "", "emploi": "", "categorie": "",
                     "section": "", "matriculeSS": "", "cb": "", "domiciliation": ""},
        "employee": {"civility": "", "name": "", "addressLines": []},
        "leave": {"rows": []},
        "lines": [],
        "totals": {
            "hoursWorked": {"period": "", "year": ""},
            "hoursOvertime": {"period": "", "year": ""},
            "currency": "EUR",
            "brutFiscal": {"period": "", "year": ""},
            "baseSecuriteSoc": {"period": "", "year": ""},
            "chargesPatronales": {"period": "", "year": ""},
            "chargesSalariales": {"period": "", "year": ""},
            "netImposable": {"period": "", "year": ""},
        },
        "netToPay": {"amount": "", "currency": "Euros"},
        "footerNote": ("Pour vous aider à faire valoir vos droits, conservez "
                       "ce bulletin de paie sans limitation de durée."),
    }

    # holding areas that need assembly after the pass
    employer_addr = None
    employee_addr = None
    cb_parts = {}          # code banque / guichet / compte / clé
    lines_by_code = OrderedDict()   # code -> line dict, in first-seen order
    line_order = []                 # sequence of codes as encountered
    leave_rows = {}                 # row key (pris/solde/acquis) -> row dict

    def norm(v):
        return "" if v is None else str(v)

    for f in fields:
        label = (f.get("label") or "").strip()
        value = norm(f.get("value"))

        # --- addresses (assembled, kept raw for now) ---------------------
        if label == "Adresse employeur":
            employer_addr = value
            continue
        if label == "Adresse travailleur":
            employee_addr = value
            continue

        # --- période "01/07/14 au 31/07/14" -----------------------------
        if label == "Période":
            parts = re.split(r"\s+au\s+", value)
            payslip["period"]["from"] = parts[0].strip() if parts else ""
            payslip["period"]["to"] = parts[1].strip() if len(parts) > 1 else ""
            if len(parts) != 2:
                anomalies.append(f"« Période » mal formée : {value!r} (attendu « X au Y »).")
            continue

        # --- RIB split across 4 fields ----------------------------------
        if label in ("Code banque", "Code guichet", "N° de compte", "Clé RIB"):
            cb_parts[label] = value
            continue

        # --- simple direct mappings -------------------------------------
        if label in SIMPLE_MAP:
            section, key = SIMPLE_MAP[label]
            payslip[section][key] = value
            continue

        # --- synthèse (bottom band + net) -------------------------------
        m = SYNTH_RE.match(label)
        if m:
            field = m.group("field").strip()
            scope = m.group("scope")
            if field == "Net à payer":
                payslip["netToPay"]["amount"] = value
            elif field in SYNTH_FIELD:
                key = SYNTH_FIELD[field]
                slot = "period" if scope == "période" else "year" if scope == "année" else None
                if slot is None:
                    anomalies.append(f"Synthèse « {field} » sans période/année : {label!r}.")
                else:
                    payslip["totals"][key][slot] = value
            else:
                anomalies.append(f"Champ de synthèse inconnu : {label!r}.")
            continue

        # --- leave/congés grid "<column> (<row>)" -----------------------
        m = LEAVE_RE.match(label)
        if m:
            column = m.group("column")
            row_key = m.group("row")
            row = leave_rows.setdefault(row_key, _blank_leave_row(row_key))
            if column == "Dates de congés":
                parts = re.split(r"\s+au\s+", value) if value else []
                row["du"] = parts[0].strip() if parts and parts[0] else ""
                row["au"] = parts[1].strip() if len(parts) > 1 else ""
            else:
                row[LEAVE_COLUMN[column]] = value
            continue

        # --- cotisation lines "<code> — <attr>" -------------------------
        m = LINE_RE.match(label)
        if m:
            code = m.group("code").strip()
            attr = m.group("attr").strip()
            if attr not in LINE_ATTR:
                anomalies.append(f"Attribut de ligne inconnu : {label!r}.")
                continue
            if code not in lines_by_code:
                lines_by_code[code] = _blank_line()
                lines_by_code[code]["num"] = "" if code in TOTAL_CODES else code
                lines_by_code[code]["_code"] = code
                lines_by_code[code]["_is_total"] = code in TOTAL_CODES
                line_order.append(code)
            lines_by_code[code][LINE_ATTR[attr]] = value
            continue

        # --- anything else ----------------------------------------------
        anomalies.append(f"Intitulé non reconnu : {label!r}.")

    # ----- assemble addresses (kept as single-line list for now) --------
    if employer_addr is not None:
        payslip["employer"]["addressLines"] = [employer_addr]
    if employee_addr is not None:
        payslip["employee"]["addressLines"] = [employee_addr]

    # ----- assemble the leave grid, fixed Pris/Solde/Acquis row order ----
    if leave_rows:
        payslip["leave"]["rows"] = [
            leave_rows.get(row_key, _blank_leave_row(row_key)) for row_key in LEAVE_ROW_ORDER
        ]

    # ----- assemble C.B from RIB parts ----------------------------------
    if cb_parts:
        payslip["contract"]["cb"] = (
            f"{cb_parts.get('Code banque','')} C.G {cb_parts.get('Code guichet','')} "
            f"n° cpte {cb_parts.get('N° de compte','')}  {cb_parts.get('Clé RIB','')}"
        ).strip()

    # ----- reorder by ascending numeric code -----------------------------
    # Anchor each total to the last numbered line seen before it in the
    # original stream, then sort the numbered lines and re-insert each
    # total right after its anchor.
    def _code_sort_key(code):
        try:
            return (0, int(code))
        except ValueError:
            anomalies.append(f"Code de ligne non numérique : {code!r}.")
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

    final_order = [code for code, anchor in anchor_of_total.items() if anchor is None]
    for code in numeric_codes_sorted:
        final_order.append(code)
        final_order += [total_code for total_code, anchor in anchor_of_total.items() if anchor == code]

    # ----- build the ordered lines[] with total flag -------------------
    # A "total" row uses {"type":"total", "label":..., gain/retenueSal/retenuePatr}.
    # We DON'T invent gap rows here (see note to user); the template renders
    # fine without them and inserting them requires layout knowledge the
    # fields don't carry.
    for code in final_order:
        raw = lines_by_code[code]
        if raw["_is_total"]:
            payslip["lines"].append({
                "type": "total",
                "label": raw["label"] or code,
                "gain": raw["gain"],
                "retenueSal": raw["retenueSal"],
                "retenuePatr": raw["retenuePatr"],
            })
        else:
            line = {k: raw[k] for k in ("num", "label", "nombre", "base",
                                        "tauxSal", "gain", "retenueSal",
                                        "tauxPatr", "retenuePatr")}
            payslip["lines"].append(line)

    # ----- flag expected-but-empty core fields --------------------------
    if not payslip["employer"]["name"]:
        anomalies.append("Nom employeur manquant.")
    if not payslip["employee"]["name"]:
        anomalies.append("Nom travailleur manquant.")
    if not payslip["netToPay"]["amount"]:
        anomalies.append("Net à payer manquant.")

    payslip["anomalies"] = anomalies
    return payslip