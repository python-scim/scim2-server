import sqlite3
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC
from datetime import datetime
from typing import Any

import aiosqlite
from scim2_models import Meta
from scim2_models import NotFoundException
from scim2_models import PreconditionFailedException
from scim2_models import Resource
from scim2_models import ResourceType
from scim2_models import ScimProvider
from scim2_models import SearchRequest
from scim2_models import UniquenessException

from doc._examples.sqlite_storage import TABLE
from scim2_server.storage import AsyncScimStorage


class AsyncSQLiteStorage(AsyncScimStorage):
    """Keep the resources in a table of a SQLite database, with aiosqlite."""

    def __init__(self, path: str, provider: ScimProvider):
        self.path = path
        self.connection: aiosqlite.Connection | None = None
        self.provider = provider

    async def connect(self) -> aiosqlite.Connection:
        """Open the database on the first call, and create its table."""
        if self.connection is None:
            self.connection = await aiosqlite.connect(self.path)
            self.connection.row_factory = aiosqlite.Row
            await self.connection.execute(TABLE)
        return self.connection

    async def close(self) -> None:
        """Close the database, when the application stops."""
        if self.connection is not None:
            await self.connection.close()
            self.connection = None

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

    async def get(self, resource_type: ResourceType, resource_id: str) -> Resource[Any]:
        connection = await self.connect()
        cursor = await connection.execute(
            "SELECT * FROM resources WHERE resource_type = ? AND id = ?",
            (resource_type.name, resource_id),
        )
        row = await cursor.fetchone()
        if row is None:
            raise NotFoundException(
                detail=f"{resource_type.name} {resource_id} not found"
            )
        return self.row_to_resource(resource_type, row)

    async def create(
        self, resource_type: ResourceType, resource: Resource[Any]
    ) -> Resource[Any]:
        resource_id = uuid.uuid4().hex
        now = datetime.now(UTC).isoformat()
        async with self.writing() as connection:
            await connection.execute(
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
        return await self.get(resource_type, resource_id)

    async def update(
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
        async with self.writing() as connection:
            cursor = await connection.execute(query, parameters)
        if cursor.rowcount == 0:
            await self.get(resource_type, str(resource.id))
            raise PreconditionFailedException
        return await self.get(resource_type, str(resource.id))

    async def delete(
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
        async with self.writing() as connection:
            cursor = await connection.execute(query, parameters)
        if cursor.rowcount == 0:
            await self.get(resource_type, resource_id)
            raise PreconditionFailedException

    @asynccontextmanager
    async def writing(self) -> AsyncIterator[aiosqlite.Connection]:
        """Commit a write, and refuse a userName another user already has."""
        connection = await self.connect()
        try:
            yield connection
            await connection.commit()
        except sqlite3.IntegrityError:
            await connection.rollback()
            raise UniquenessException from None

    async def search(
        self, resource_types: list[ResourceType], search_request: SearchRequest[Any]
    ) -> tuple[int, list[Resource[Any]]]:
        connection = await self.connect()
        found = []
        for resource_type in resource_types:
            cursor = await connection.execute(
                "SELECT * FROM resources WHERE resource_type = ? ORDER BY created",
                (resource_type.name,),
            )
            found += [
                self.row_to_resource(resource_type, row)
                for row in await cursor.fetchall()
            ]
        if search_request.filter is not None:
            found = [
                resource for resource in found if search_request.filter.match(resource)
            ]
        found = search_request.sort(found)
        start = (search_request.start_index or 1) - 1
        stop = None if search_request.count is None else start + search_request.count
        return len(found), found[start:stop]
