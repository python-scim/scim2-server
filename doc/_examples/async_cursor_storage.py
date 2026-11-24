from doc._examples.async_sqlite_storage import AsyncSQLiteStorage
from scim2_server.keyset import Bound
from scim2_server.keyset import Position
from scim2_server.keyset import keyset_page
from scim2_server.keyset import page_rows
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
        anchor = Position.load(position, has_sort=False)
        count = search_request.count
        limit = -1 if count is None else count + 1
        if anchor is None or anchor.forward:
            clause = "AND id > ? ORDER BY id LIMIT ?"
        else:
            clause = "AND id < ? ORDER BY id DESC LIMIT ?"

        identifier = "" if anchor is None else anchor.id
        rows = await self._select(types, "*", clause, [identifier, limit])
        rows, more = page_rows(rows, search_request, anchor)
        resources = self._to_resources(types, rows)
        first = Bound(None, rows[0]["id"]) if rows else None
        last = Bound(None, rows[-1]["id"]) if rows else None
        return keyset_page(total, resources, first, last, more, anchor)

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
