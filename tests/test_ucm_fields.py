# tests/test_ucm_fields.py
#
# Safety net for extract_fields() before refactoring ucm.py: it had zero
# test coverage. Text mimics the real 3-column flattened layout (see
# comments in ucm.py) with one field per label group. "Date de sortie"
# is left empty on purpose to cover the documented None case.

from docscope.services.models import ucm

PAYSLIP_TEXT = """
Période Janvier 2026
Calculé le 05/01/2026
N° journal des paies 12345
Employeur
ACME SPRL
Rue de la Paix 10
1000 Bruxelles
Dossier 987654 CONFIDENTIEL
N° d'entreprise 0123.456.789 Monsieur Dupont Jean
N° ONSS 12345678901 Rue des Fleurs 5
Allocations familiales 1050 Ixelles
Référence Dossier REF001 Date d'entrée 01/01/2020 Solde vacances 12,00 heures
Statut Employé Date de sortie 0,00 heures
Fonction Comptable N° de contrat d'apprentissage
Commission paritaire 200 Date de naissance 15/05/1990 3,00 heures
Catégorie Employé Sexe Masculin
Temps de travail Temps plein N.reg nat 90051512345
Rémunération Mensuelle Etat civil Marié
Lieu de travail Bruxelles Personnes à charge 2
Enfant(s) handicapé(s) : 0
Autre(s) : 0
IBAN BE12 3456 7890 1234 Enfant(s) à charge 2
BIC bénéficiaire GKCCBEBB Autre(s) info
Solde récupérations 5,00 heures
""".strip("\n")

EXPECTED_FIELDS = [
    ("Période", "Janvier 2026"),
    ("Date de calcul", "05/01/2026"),
    ("N° du bulletin", "12345"),
    ("Nom employeur", "ACME SPRL"),
    ("Adresse employeur", "Rue de la Paix 10 1000 Bruxelles"),
    ("Numéro de dossier", "987654"),
    ("Numéro d'entreprise", "0123.456.789"),
    ("Numéro ONSS", "12345678901"),
    ("Nom", "Dupont"),
    ("Prénom", "Jean"),
    ("Adresse", "Rue des Fleurs 5 1050 Ixelles"),
    ("Référence Dossier", "REF001"),
    ("Statut", "Employé"),
    ("Fonction", "Comptable"),
    ("Commission paritaire", "200"),
    ("Catégorie", "Employé"),
    ("Temps de travail", "Temps plein"),
    ("Rémunération", "Mensuelle"),
    ("Lieu de travail", "Bruxelles"),
    ("IBAN", "BE12 3456 7890 1234"),
    ("BIC bénéficiaire", "GKCCBEBB"),
    ("Date d'entrée", "01/01/2020"),
    ("Date de sortie", None),
    ("Date de naissance", "15/05/1990"),
    ("Sexe", "Masculin"),
    ("Numéro de registre national", "90051512345"),
    ("Etat civil", "Marié"),
    ("Personnes à charge", "2 Enfant(s) handicapé(s) : 0 Autre(s) : 0"),
    ("Solde vacances", "12,00 heures"),
    ("Solde récupération", "5,00 heures"),
]


def test_extract_fields_from_payslip():
    result = ucm.extract_fields(PAYSLIP_TEXT)
    assert result == EXPECTED_FIELDS
