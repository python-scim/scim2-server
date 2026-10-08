import sqlite3
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC
from datetime import datetime
from typing import Any

import aiosqlite
from scim2_models import NotFoundException
from scim2_models import PreconditionFailedException
from scim2_models import Resource
from scim2_models import ResourceType
from scim2_models import ResponseParameters
from scim2_models import SearchRequest
from scim2_models import UniquenessException

from doc._examples.library_storage import TABLES
from scim2_server.storage import AsyncScimStorage
from scim2_server.storage import SearchPage


class AsyncLibraryStorage(AsyncScimStorage):
    """Serve the members and the books of the library, with aiosqlite."""

    def __init__(self, path: str):
        self.path = path
        self.connection: aiosqlite.Connection | None = None

    async def connect(self) -> aiosqlite.Connection:
        """Open the database on the first call."""
        if self.connection is None:
            self.connection = await aiosqlite.connect(self.path)
            self.connection.row_factory = aiosqlite.Row
        return self.connection

    async def close(self) -> None:
        """Close the database, when the application stops."""
        if self.connection is not None:
            await self.connection.close()
            self.connection = None

    async def get(
        self,
        resource_type: ResourceType,
        resource_id: str,
        *,
        response_parameters: ResponseParameters[Any] | None = None,
    ) -> Resource[Any]:
        table = TABLES[resource_type.name]
        connection = await self.connect()
        row = None
        if resource_id.isdigit():
            cursor = await connection.execute(
                f"SELECT * FROM {table.name} WHERE id = ?", (int(resource_id),)
            )
            row = await cursor.fetchone()
        if row is None:
            raise NotFoundException(
                detail=f"{resource_type.name} {resource_id} not found"
            )
        return table.to_resource(row)

    async def create(
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
        async with self.writing() as connection:
            cursor = await connection.execute(
                f"INSERT INTO {table.name} ({columns}) VALUES ({values})", row
            )
        return await self.get(resource_type, str(cursor.lastrowid))

    async def update(
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
        table = TABLES[resource_type.name]
        query = f"DELETE FROM {table.name} WHERE id = :id"
        parameters = {"id": resource_id}
        if expected_version is not None:
            query += " AND updated_at = :version"
            parameters["version"] = expected_version.removeprefix("W/").strip('"')
        async with self.writing() as connection:
            cursor = await connection.execute(query, parameters)
        if cursor.rowcount == 0:
            await self.get(resource_type, resource_id)
            raise PreconditionFailedException

    @asynccontextmanager
    async def writing(self) -> AsyncIterator[aiosqlite.Connection]:
        """Commit a write, and refuse a value that a UNIQUE column already holds."""
        connection = await self.connect()
        try:
            yield connection
            await connection.commit()
        except sqlite3.IntegrityError:
            await connection.rollback()
            raise UniquenessException from None

    async def search(
        self,
        resource_types: list[ResourceType],
        search_request: SearchRequest[Any],
        *,
        position: Any = None,
    ) -> SearchPage:
        connection = await self.connect()
        found: list[Resource[Any]] = []
        for resource_type in resource_types:
            table = TABLES[resource_type.name]
            cursor = await connection.execute(f"SELECT * FROM {table.name} ORDER BY id")
            found += [table.to_resource(row) for row in await cursor.fetchall()]
        if search_request.filter is not None:
            found = [
                resource for resource in found if search_request.filter.match(resource)
            ]
        found = search_request.sort(found)
        page = found[search_request.start_index_0 : search_request.stop_index_0]
        return SearchPage(len(found), page)
