# tests/test_schema_naming.py
#
# Guards the "table names are always singular" convention mechanically:
# fails the build the moment a new model declares a plural __tablename__,
# instead of relying on catching it in review. inflect resolves irregular
# plurals (people/person) the same way a hand-rolled suffix check couldn't.

import inflect

from docscope.services.documents import Base

engine = inflect.engine()


def test_all_table_names_are_singular():
    plural_tables = [
        name for name in Base.metadata.tables if engine.singular_noun(name) is not False
    ]
    assert plural_tables == [], f"plural table name(s): {plural_tables} - use the singular form"
