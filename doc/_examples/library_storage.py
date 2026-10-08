import secrets
import sqlite3
from collections.abc import Callable
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC
from datetime import datetime
from typing import Annotated
from typing import Any

from scim2_models import URN
from scim2_models import Email
from scim2_models import Extension
from scim2_models import Meta
from scim2_models import Mutability
from scim2_models import Name
from scim2_models import NotFoundException
from scim2_models import PreconditionFailedException
from scim2_models import Required
from scim2_models import Resource
from scim2_models import ResourceType
from scim2_models import ResponseParameters
from scim2_models import SearchRequest
from scim2_models import Uniqueness
from scim2_models import UniquenessException
from scim2_models import User

from scim2_server.storage import ScimStorage
from scim2_server.storage import SearchPage

MEMBERS = """
CREATE TABLE IF NOT EXISTS members (
    id INTEGER PRIMARY KEY,
    login TEXT NOT NULL UNIQUE COLLATE NOCASE,
    email TEXT,
    first_name TEXT,
    last_name TEXT,
    card_number TEXT NOT NULL,
    active INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
)
"""

BOOKS = """
CREATE TABLE IF NOT EXISTS books (
    id INTEGER PRIMARY KEY,
    title TEXT NOT NULL,
    isbn TEXT UNIQUE,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
)
"""


class LibraryMember(Extension):
    __schema__ = URN("urn:example:params:scim:schemas:extension:library:2.0:User")
    card_number: Annotated[str | None, Mutability.read_only] = None


class Book(Resource):
    __schema__ = URN("urn:example:params:scim:schemas:library:2.0:Book")
    title: Annotated[str | None, Required.true] = None
    isbn: Annotated[str | None, Uniqueness.server] = None


Member = User[LibraryMember]


def meta(resource_type: str, row: sqlite3.Row) -> Meta:
    return Meta(
        resource_type=resource_type,
        created=row["created_at"],
        last_modified=row["updated_at"],
        version=f'W/"{row["updated_at"]}"',
    )


def row_to_user(row: sqlite3.Row) -> User[LibraryMember]:
    user = Member(
        id=str(row["id"]),
        user_name=row["login"],
        name=Name(given_name=row["first_name"], family_name=row["last_name"]),
        emails=[Email(value=row["email"], primary=True)] if row["email"] else None,
        active=bool(row["active"]),
        meta=meta("User", row),
    )
    user[LibraryMember] = LibraryMember(card_number=row["card_number"])
    return user


def user_to_row(user: User[Any]) -> dict[str, Any]:
    """Return the columns that a user sets."""
    emails = user.emails or []
    email = next((e for e in emails if e.primary), emails[0] if emails else None)
    name = user.name or Name()
    return {
        "login": user.user_name,
        "email": email.value if email else None,
        "first_name": name.given_name,
        "last_name": name.family_name,
        "active": user.active is not False,
    }


def new_card_number() -> dict[str, Any]:
    return {"card_number": f"{secrets.randbelow(10**8):08}"}


def row_to_book(row: sqlite3.Row) -> Book:
    return Book(
        id=str(row["id"]),
        title=row["title"],
        isbn=row["isbn"],
        meta=meta("Book", row),
    )


def book_to_row(book: Book) -> dict[str, Any]:
    """Return the columns that a book sets."""
    return {"title": book.title, "isbn": book.isbn}


@dataclass
class Table:
    """The table of a resource type, and the conversions between its rows and the resources."""

    name: str
    to_resource: Callable[[sqlite3.Row], Resource[Any]]
    to_row: Callable[[Any], dict[str, Any]]
    assigned: Callable[[], dict[str, Any]] = dict


TABLES = {
    "User": Table("members", row_to_user, user_to_row, new_card_number),
    "Book": Table("books", row_to_book, book_to_row),
}


class LibraryStorage(ScimStorage):
    """Serve the members and the books of the library."""

    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection
        self.connection.row_factory = sqlite3.Row

    def get(
        self,
        resource_type: ResourceType,
        resource_id: str,
        *,
        response_parameters: ResponseParameters[Any] | None = None,
    ) -> Resource[Any]:
        table = TABLES[resource_type.name]
        row = None
        if resource_id.isdigit():
            row = self.connection.execute(
                f"SELECT * FROM {table.name} WHERE id = ?", (int(resource_id),)
            ).fetchone()
        if row is None:
            raise NotFoundException(
                detail=f"{resource_type.name} {resource_id} not found"
            )
        return table.to_resource(row)

    def create(
        self, resource_type: ResourceType, resource: Resource[Any]
    ) -> Resource[Any]:
        table = TABLES[resource_type.name]
        now = datetime.now(UTC).isoformat()
        row = {
            **table.to_row(resource),
            **table.assigned(),
            "created_at": now,
            "updated_at": now,
        }
        columns = ", ".join(row)
        values = ", ".join(f":{column}" for column in row)
        with self.writing():
            cursor = self.connection.execute(
                f"INSERT INTO {table.name} ({columns}) VALUES ({values})", row
            )
        return self.get(resource_type, str(cursor.lastrowid))

    def update(
        self,
        resource_type: ResourceType,
        resource: Resource[Any],
        *,
        expected_version: str | None = None,
    ) -> Resource[Any]:
        table = TABLES[resource_type.name]
        row = {**table.to_row(resource), "updated_at": datetime.now(UTC).isoformat()}
        assignments = ", ".join(f"{column} = :{column}" for column in row)
        query = f"UPDATE {table.name} SET {assignments} WHERE id = :id"
        parameters = {**row, "id": resource.id}
        if expected_version is not None:
            query += " AND updated_at = :version"
            parameters["version"] = expected_version.removeprefix("W/").strip('"')
        with self.writing():
            cursor = self.connection.execute(query, parameters)
        if cursor.rowcount == 0:
            self.get(resource_type, str(resource.id))
            raise PreconditionFailedException
        return self.get(resource_type, str(resource.id))

    def delete(
        self,
        resource_type: ResourceType,
        resource_id: str,
        *,
        expected_version: str | None = None,
    ) -> None:
        table = TABLES[resource_type.name]
        query = f"DELETE FROM {table.name} WHERE id = :id"
        parameters = {"id": resource_id}
        if expected_version is not None:
            query += " AND updated_at = :version"
            parameters["version"] = expected_version.removeprefix("W/").strip('"')
        with self.writing():
            cursor = self.connection.execute(query, parameters)
        if cursor.rowcount == 0:
            self.get(resource_type, resource_id)
            raise PreconditionFailedException

    @contextmanager
    def writing(self) -> Iterator[None]:
        """Commit a write, and refuse a value that a UNIQUE column already holds."""
        try:
            with self.connection:
                yield
        except sqlite3.IntegrityError:
            raise UniquenessException from None

    def search(
        self,
        resource_types: list[ResourceType],
        search_request: SearchRequest[Any],
        *,
        position: Any = None,
    ) -> SearchPage:
        found: list[Resource[Any]] = []
        for resource_type in resource_types:
            table = TABLES[resource_type.name]
            rows = self.connection.execute(f"SELECT * FROM {table.name} ORDER BY id")
            found += [table.to_resource(row) for row in rows]
        if search_request.filter is not None:
            found = [
                resource for resource in found if search_request.filter.match(resource)
            ]
        found = search_request.sort(found)
        page = found[search_request.start_index_0 : search_request.stop_index_0]
        return SearchPage(len(found), page)
