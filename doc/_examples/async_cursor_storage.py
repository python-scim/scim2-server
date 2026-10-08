from doc._examples.async_sqlite_storage import AsyncSQLiteStorage
from scim2_server.storage import SearchPage


class AsyncCursorSQLiteStorage(AsyncSQLiteStorage):
    supports_cursors = True

    async def search(self, resource_types, search_request, *, position=None):
        types = {resource_type.name: resource_type for resource_type in resource_types}
        [(total,)] = await self._select(types, "COUNT(*)")
        if search_request.cursor is None:
            return await self._search_index(types, total, search_request)
        return await self._search_cursor(types, total, search_request, position)

    async def _search_index(self, types, total, search_request):
        count = search_request.count
        page = [-1 if count is None else count, search_request.start_index_0]
        rows = await self._select(types, "*", "ORDER BY id LIMIT ? OFFSET ?", page)
        return SearchPage(total, self._to_resources(types, rows))

    async def _search_cursor(self, types, total, search_request, position):
        count = search_request.count
        limit = -1 if count is None else count + 1
        if position is None or "after" in position:
            after = "" if position is None else position["after"]
            clause = "AND id > ? ORDER BY id LIMIT ?"
            rows = await self._select(types, "*", clause, [after, limit])
            resources = self._to_resources(types, rows[:count])
            more_after, more_before = len(rows) > len(resources), position is not None
        else:
            clause = "AND id < ? ORDER BY id DESC LIMIT ?"
            rows = await self._select(types, "*", clause, [position["before"], limit])
            resources = self._to_resources(types, rows[:count][::-1])
            more_after, more_before = True, len(rows) > len(resources)

        result = SearchPage(total, resources)
        if resources and more_after:
            result.next = {"after": resources[-1].id}
        if resources and more_before:
            result.previous = {"before": resources[0].id}
        return result

    async def _select(self, types, columns, clause="", parameters=()):
        connection = await self.connect()
        among = ", ".join("?" * len(types))
        cursor = await connection.execute(
            f"SELECT {columns} FROM resources WHERE resource_type IN ({among}) {clause}",
            [*types, *parameters],
        )
        return list(await cursor.fetchall())

    def _to_resources(self, types, rows):
        return [self.row_to_resource(types[row["resource_type"]], row) for row in rows]
