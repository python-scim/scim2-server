import sqlite3
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC
from datetime import datetime
from typing import Any

from scim2_models import Meta
from scim2_models import NotFoundException
from scim2_models import PreconditionFailedException
from scim2_models import Resource
from scim2_models import ResourceType
from scim2_models import ResponseParameters
from scim2_models import ScimProvider
from scim2_models import SearchRequest
from scim2_models import UniquenessException

from scim2_server.storage import ScimStorage

TABLE = """
CREATE TABLE IF NOT EXISTS resources (
    resource_type TEXT NOT NULL,
    id TEXT NOT NULL,
    user_name TEXT COLLATE NOCASE,
    attributes TEXT NOT NULL,
    created TEXT NOT NULL,
    last_modified TEXT NOT NULL,
    version INTEGER NOT NULL,
    PRIMARY KEY (resource_type, id),
    UNIQUE (resource_type, user_name)
)
"""


class SQLiteStorage(ScimStorage):
    """Keep the resources in a table of a SQLite database."""

    def __init__(self, connection: sqlite3.Connection, provider: ScimProvider):
        self.connection = connection
        self.connection.row_factory = sqlite3.Row
        self.connection.execute(TABLE)
        self.provider = provider

    def row_to_resource(
        self, resource_type: ResourceType, row: sqlite3.Row
    ) -> Resource[Any]:
        model = self.provider.model_for(resource_type)
        resource = model.model_validate_json(row["attributes"])
        resource.id = row["id"]
        resource.meta = Meta(
            resource_type=resource_type.name,
            created=row["created"],
            last_modified=row["last_modified"],
            version=f'W/"{row["version"]}"',
        )
        return resource

    def get(
        self,
        resource_type: ResourceType,
        resource_id: str,
        *,
        response_parameters: ResponseParameters[Any] | None = None,
    ) -> Resource[Any]:
        row = self.connection.execute(
            "SELECT * FROM resources WHERE resource_type = ? AND id = ?",
            (resource_type.name, resource_id),
        ).fetchone()
        if row is None:
            raise NotFoundException(
                detail=f"{resource_type.name} {resource_id} not found"
            )
        return self.row_to_resource(resource_type, row)

    def create(
        self, resource_type: ResourceType, resource: Resource[Any]
    ) -> Resource[Any]:
        resource_id = uuid.uuid4().hex
        now = datetime.now(UTC).isoformat()
        with self.writing():
            self.connection.execute(
                "INSERT INTO resources VALUES (?, ?, ?, ?, ?, ?, 1)",
                (
                    resource_type.name,
                    resource_id,
                    getattr(resource, "user_name", None),
                    resource.model_dump_json(),
                    now,
                    now,
                ),
            )
        return self.get(resource_type, resource_id)

    def update(
        self,
        resource_type: ResourceType,
        resource: Resource[Any],
        *,
        expected_version: str | None = None,
    ) -> Resource[Any]:
        query = (
            "UPDATE resources SET user_name = ?, attributes = ?, last_modified = ?,"
            " version = version + 1 WHERE resource_type = ? AND id = ?"
        )
        parameters = [
            getattr(resource, "user_name", None),
            resource.model_dump_json(),
            datetime.now(UTC).isoformat(),
            resource_type.name,
            resource.id,
        ]
        if expected_version is not None:
            query += " AND version = ?"
            parameters.append(expected_version.removeprefix("W/").strip('"'))
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
        query = "DELETE FROM resources WHERE resource_type = ? AND id = ?"
        parameters = [resource_type.name, resource_id]
        if expected_version is not None:
            query += " AND version = ?"
            parameters.append(expected_version.removeprefix("W/").strip('"'))
        with self.writing():
            cursor = self.connection.execute(query, parameters)
        if cursor.rowcount == 0:
            self.get(resource_type, resource_id)
            raise PreconditionFailedException

    @contextmanager
    def writing(self) -> Iterator[None]:
        """Commit a write, and refuse a userName another user already has."""
        try:
            with self.connection:
                yield
        except sqlite3.IntegrityError:
            raise UniquenessException from None

    def search(
        self, resource_types: list[ResourceType], search_request: SearchRequest[Any]
    ) -> tuple[int, list[Resource[Any]]]:
        found = []
        for resource_type in resource_types:
            rows = self.connection.execute(
                "SELECT * FROM resources WHERE resource_type = ? ORDER BY created",
                (resource_type.name,),
            )
            found += [self.row_to_resource(resource_type, row) for row in rows]
        if search_request.filter is not None:
            found = [
                resource for resource in found if search_request.filter.match(resource)
            ]
        found = search_request.sort(found)
        start = (search_request.start_index or 1) - 1
        stop = None if search_request.count is None else start + search_request.count
        return len(found), found[start:stop]
