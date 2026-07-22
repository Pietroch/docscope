# api/src/docscope/services/documents.py
#
# `document`: one row per uploaded file, linked to the company (`company`)
# and worker (`person`) it belongs to - both parsed from the file name and
# get-or-created on insert - plus its extracted fields (`document_field`).
# create_tables() must run once at startup (see main.py) before any insert.
# Table names are singular nouns, no exceptions (person/people would be the
# one irregular case a plural convention forces you to special-case).

import re
from datetime import date, datetime

from sqlalchemy import CheckConstraint, ForeignKey, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

from docscope.core.database import engine

TEMPLATES = ("ucm", "delvaux", "apside")

# Expected file name: "NOM Prenom_ENTREPRISE_TYPE_YYYYMMDD[.ext]".
# TYPE becomes the document's `type` column - only "Bulletin" is accepted
# for now, other types may be added to the pattern later.
NAME_RE = re.compile(
    r"^(?P<last_name>\S+) (?P<first_name>\S+)_(?P<company>\S+)_(?P<type>Bulletin)_(?P<date>\d{8})(?:\.\w+)?$"
)


class Base(DeclarativeBase):
    pass


class Company(Base):
    __tablename__ = "company"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(unique=True)


class Person(Base):
    __tablename__ = "person"
    __table_args__ = (UniqueConstraint("last_name", "first_name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    last_name: Mapped[str]
    first_name: Mapped[str]


class Document(Base):
    __tablename__ = "document"
    __table_args__ = (CheckConstraint(f"template IN {TEMPLATES}", name="ck_document_template"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str]
    type: Mapped[str]
    template: Mapped[str]
    date_document: Mapped[date]
    company_id: Mapped[int] = mapped_column(ForeignKey("company.id"))
    person_id: Mapped[int] = mapped_column(ForeignKey("person.id"))


class DocumentField(Base):
    __tablename__ = "document_field"

    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("document.id"))
    label: Mapped[str]
    value: Mapped[str | None]


def create_tables() -> None:
    Base.metadata.create_all(engine)


class InvalidDocumentName(Exception):
    """Raised when one or more file names don't match the expected format."""

    def __init__(self, names: list[str]):
        self.names = names
        super().__init__(f"Invalid file name(s): {', '.join(names)}")


def parse_document_name(name: str) -> dict:
    """Extract last_name/first_name/company/date from a file name. Raises
    ValueError if the format isn't respected."""
    m = NAME_RE.match(name)
    if not m:
        raise ValueError(name)
    return {
        "last_name": m.group("last_name"),
        "first_name": m.group("first_name"),
        "company": m.group("company"),
        "type": m.group("type"),
        "date": datetime.strptime(m.group("date"), "%Y%m%d").date(),
    }


def _get_or_create(session: Session, model, **filters):
    instance = session.query(model).filter_by(**filters).one_or_none()
    if instance is None:
        instance = model(**filters)
        session.add(instance)
        session.flush()  # assigns instance.id before it's used as a FK
    return instance


def create_documents(documents: list[dict], template: str) -> list[int]:
    """Insert several documents and their extracted fields in one
    transaction. Each item: {"name": str, "fields": [(label, value), ...]}.
    `type` is not client-provided: it's the type token parsed from the
    name (see parse_document_name). Every name must match the expected
    format - if one doesn't, nothing is inserted."""
    infos, invalid = [], []
    for doc in documents:
        try:
            infos.append(parse_document_name(doc["name"]))
        except ValueError:
            invalid.append(doc["name"])
    if invalid:
        raise InvalidDocumentName(invalid)

    with Session(engine) as session:
        ids = []

        for doc, info in zip(documents, infos):
            company = _get_or_create(session, Company, name=info["company"])
            person = _get_or_create(
                session, Person, last_name=info["last_name"], first_name=info["first_name"]
            )

            document = Document(
                name=doc["name"],
                type=info["type"],
                template=template,
                date_document=info["date"],
                company_id=company.id,
                person_id=person.id,
            )
            session.add(document)
            session.flush()  # assigns document.id before the fields reference it

            for label, value in doc["fields"]:
                session.add(DocumentField(document_id=document.id, label=label, value=value))

            ids.append(document.id)

        session.commit()
        return ids


def _document_dict(document: Document, company: Company, person: Person) -> dict:
    return {
        "id": document.id,
        "name": document.name,
        "type": document.type,
        "template": document.template,
        "date": document.date_document.isoformat(),
        "company": company.name,
        "person": f"{person.last_name} {person.first_name}",
    }


def list_documents() -> list[dict]:
    """All documents with their company/worker names resolved, most recent
    first. Sorting and filtering are left to the client: the dataset is
    small enough that query-side pagination isn't needed yet."""
    with Session(engine) as session:
        rows = (
            session.query(Document, Company, Person)
            .join(Company, Document.company_id == Company.id)
            .join(Person, Document.person_id == Person.id)
            .order_by(Document.date_document.desc())
            .all()
        )
        return [_document_dict(*row) for row in rows]


def get_document(document_id: int) -> dict | None:
    """Single document with its company/worker names resolved plus its
    extracted fields, or None if it doesn't exist."""
    with Session(engine) as session:
        row = (
            session.query(Document, Company, Person)
            .join(Company, Document.company_id == Company.id)
            .join(Person, Document.person_id == Person.id)
            .filter(Document.id == document_id)
            .one_or_none()
        )
        if row is None:
            return None

        result = _document_dict(*row)
        fields = (
            session.query(DocumentField)
            .filter(DocumentField.document_id == document_id)
            .order_by(DocumentField.id)
            .all()
        )
        result["fields"] = [{"id": f.id, "label": f.label, "value": f.value} for f in fields]
        return result


def update_field(document_id: int, field_id: int, label: str, value: str | None) -> dict | None:
    """Correct a single extracted field's label and/or value. Scoped to
    document_id so a field id can't be used to edit another document's
    data. Returns the updated field, or None if it doesn't belong to that
    document."""
    with Session(engine) as session:
        field = (
            session.query(DocumentField)
            .filter(DocumentField.id == field_id, DocumentField.document_id == document_id)
            .one_or_none()
        )
        if field is None:
            return None
        field.label = label
        field.value = value
        session.commit()
        return {"id": field.id, "label": field.label, "value": field.value}
