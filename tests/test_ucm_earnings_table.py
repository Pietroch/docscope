# tests/test_ucm_earnings_table.py
#
# Checks extract_earnings_table() (flattened-text approach, no coordinates)
# cell by cell against ground truth. The text reproduces the real layout:
# 2-uppercase-letter markers (AA, BB...) in front of each summary label,
# detached "-" sign on the next line for "ONSS travailleur", "Net à payer"
# value on the next line.

from docscope.services.models import ucm

SOURCE_TEXT = """
Rémunérations - avantages et retenues
Codes Libellés Jours Heures Unités Taux Ind. Montant EUR
0001.00 Jrs/hrs prestés 16,00 122,00 A
0005.00 Jrs/hrs vacances employé 6,00 46,00 A
1001.00 Appointement (a) A 3.723,20
1020.08 Avantage toute nature : tablette A 3,00
6506.00 ONSS travailleur de base (a) B 487,01
2026.00 Voiture de société - usage privé C 138,97
5201.00 Précompte professionnel (a) E 716,46
3090.23 Indemnité télétravail (justifié) F 120,00
3090.00 Dépenses propres à l'employeur justifiées G
4019.03 Assurance-groupe (Continuation individuelle) G -35,17
4100.00 Part personnelle chèques repas (a) 16,00 G -17,44
4900.00 Avantage toute nature (a) G -141,97
6503.00 Cotisation spéciale sécurité sociale G -31,34
6000.00 ONSS Patronal 961,36
8100.23 Cheques repas (Tot. 8,00 Eur - Trav 1,09 Eur ) 16,00 128,00
AA Brut ONSS 3.726,20
BB ONSS travailleur -
487,01
CC Brut non ONSS 138,97
DD Cotis. Diverses -
EE Imposable 3.378,16
FF Précompte -716,46
GG Net 2.661,70
HH Divers + 120,00
II Divers - -225,92
JJ Net à payer
2.555,78 EUR
KK Charges patronales 961,36
"""

# One entry per code, one column per cell (Jours / Heures / Unités /
# Ind. / Montant EUR). Column absent on the line -> None.
EXPECTED_TABLE = [
    {"Code": "0001.00", "Libellé": "Jrs/hrs prestés", "Jours": "16,00", "Heures": "122,00", "Unités": None, "Ind.": "A", "Montant EUR": None},
    {"Code": "0005.00", "Libellé": "Jrs/hrs vacances employé", "Jours": "6,00", "Heures": "46,00", "Unités": None, "Ind.": "A", "Montant EUR": None},
    {"Code": "1001.00", "Libellé": "Appointement (a)", "Jours": None, "Heures": None, "Unités": None, "Ind.": "A", "Montant EUR": "3.723,20"},
    {"Code": "1020.08", "Libellé": "Avantage toute nature : tablette", "Jours": None, "Heures": None, "Unités": None, "Ind.": "A", "Montant EUR": "3,00"},
    {"Code": "6506.00", "Libellé": "ONSS travailleur de base (a)", "Jours": None, "Heures": None, "Unités": None, "Ind.": "B", "Montant EUR": "487,01"},
    {"Code": "2026.00", "Libellé": "Voiture de société - usage privé", "Jours": None, "Heures": None, "Unités": None, "Ind.": "C", "Montant EUR": "138,97"},
    {"Code": "5201.00", "Libellé": "Précompte professionnel (a)", "Jours": None, "Heures": None, "Unités": None, "Ind.": "E", "Montant EUR": "716,46"},
    {"Code": "3090.23", "Libellé": "Indemnité télétravail (justifié)", "Jours": None, "Heures": None, "Unités": None, "Ind.": "F", "Montant EUR": "120,00"},
    {"Code": "3090.00", "Libellé": "Dépenses propres à l'employeur justifiées", "Jours": None, "Heures": None, "Unités": None, "Ind.": "G", "Montant EUR": None},
    {"Code": "4019.03", "Libellé": "Assurance-groupe (Continuation individuelle)", "Jours": None, "Heures": None, "Unités": None, "Ind.": "G", "Montant EUR": "-35,17"},
    {"Code": "4100.00", "Libellé": "Part personnelle chèques repas (a)", "Jours": "16,00", "Heures": None, "Unités": None, "Ind.": "G", "Montant EUR": "-17,44"},
    {"Code": "4900.00", "Libellé": "Avantage toute nature (a)", "Jours": None, "Heures": None, "Unités": None, "Ind.": "G", "Montant EUR": "-141,97"},
    {"Code": "6503.00", "Libellé": "Cotisation spéciale sécurité sociale", "Jours": None, "Heures": None, "Unités": None, "Ind.": "G", "Montant EUR": "-31,34"},
    {"Code": "6000.00", "Libellé": "ONSS Patronal", "Jours": None, "Heures": None, "Unités": None, "Ind.": None, "Montant EUR": "961,36"},
    # Known limitation: with no indicator letter, the line's decimals are
    # assigned positionally (Jours/Heures/Unités then Montant). Here "8,00"
    # and "1,09" come from the source label ("Tot. 8,00 Eur - Trav 1,09
    # Eur") and end up wrongly in Jours/Heures.
    {
        "Code": "8100.23",
        "Libellé": "Cheques repas (Tot. Eur - Trav Eur )",
        "Jours": "8,00", "Heures": "1,09", "Unités": "16,00",
        "Ind.": None, "Montant EUR": "128,00",
    },
]

EXPECTED_SUMMARY = {
    "Brut ONSS": "3.726,20",
    "ONSS travailleur": "-487,01",
    "Brut non ONSS": "138,97",
    "Cotis. Diverses": "-",
    "Imposable": "3.378,16",
    "Précompte": "-716,46",
    "Net": "2.661,70",
    "Divers +": "120,00",
    "Divers -": "-225,92",
    "Net à payer": "2.555,78 EUR",
    "Charges patronales": "961,36",
}


def _group_by_code(table_lines):
    by_code = {}
    order = []
    for code, column, value in table_lines:
        if code not in by_code:
            by_code[code] = {}
            order.append(code)
        by_code[code][column] = value
    return order, by_code


def test_extract_earnings_table():
    table_lines, _ = ucm.extract_earnings_table(SOURCE_TEXT)

    order, by_code = _group_by_code(table_lines)
    columns = ["Libellé", "Jours", "Heures", "Unités", "Ind.", "Montant EUR"]

    errors = []

    expected_codes = [row["Code"] for row in EXPECTED_TABLE]
    if order != expected_codes:
        errors.append(f"Row order: expected {expected_codes}, got {order}")

    for expected_row in EXPECTED_TABLE:
        code = expected_row["Code"]
        cells = by_code.get(code, {})
        for column in columns:
            expected_value = expected_row[column]
            actual_value = cells.get(column)
            if actual_value != expected_value:
                errors.append(
                    f"{code} / {column}: expected {expected_value!r}, got {actual_value!r}"
                )

    assert not errors, "\n" + "\n".join(errors)


def test_extract_earnings_summary():
    _, summary_lines = ucm.extract_earnings_table(SOURCE_TEXT)

    actual = {label: value for _, label, value in summary_lines}

    errors = [
        f"{label}: expected {expected_value!r}, got {actual.get(label)!r}"
        for label, expected_value in EXPECTED_SUMMARY.items()
        if actual.get(label) != expected_value
    ]

    assert not errors, "\n" + "\n".join(errors)
