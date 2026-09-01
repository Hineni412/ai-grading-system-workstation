from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, Query, Request, Response

from .daily_table_schemas import (
    CellsPutRequest,
    TableColumnCreateRequest,
    TableColumnPatchRequest,
    TableCreateRequest,
    TablePatchRequest,
)
from .router import _call, _no_store, _require_trusted_mutation, _service


def create_daily_table_router() -> APIRouter:
    router = APIRouter(prefix="/daily")

    @router.get("/roster-classes")
    def get_roster_classes(request: Request, response: Response):
        _no_store(response)
        return _call(lambda: _service(request).daily_tables.roster_classes())

    @router.get("/tables")
    def list_tables(
        request: Request,
        response: Response,
        status: str = Query(default="all"),
    ):
        _no_store(response)
        return _call(
            lambda: _service(request).daily_tables.list_tables(status=status)
        )

    @router.post("/tables")
    def post_table(
        request: Request,
        body: TableCreateRequest,
        response: Response,
    ):
        _require_trusted_mutation(request)
        _no_store(response)
        return _call(
            lambda: _service(request).daily_tables.create_table(
                **body.model_dump()
            )
        )

    @router.get("/tables/{table_id}")
    def get_table(table_id: str, request: Request, response: Response):
        _no_store(response)
        return _call(lambda: _service(request).daily_tables.get_table(table_id))

    @router.patch("/tables/{table_id}")
    def patch_table(
        table_id: str,
        request: Request,
        body: TablePatchRequest,
        response: Response,
    ):
        _require_trusted_mutation(request)
        _no_store(response)
        return _call(
            lambda: _service(request).daily_tables.update_table(
                table_id, **body.model_dump(exclude_unset=True)
            )
        )

    @router.delete("/tables/{table_id}")
    def delete_table(table_id: str, request: Request, response: Response):
        _require_trusted_mutation(request)
        _no_store(response)
        return _call(
            lambda: _service(request).daily_tables.delete_table(table_id)
        )

    @router.post("/tables/{table_id}/columns")
    def post_column(
        table_id: str,
        request: Request,
        body: TableColumnCreateRequest,
        response: Response,
    ):
        _require_trusted_mutation(request)
        _no_store(response)
        return _call(
            lambda: _service(request).daily_tables.add_column(
                table_id, **body.model_dump()
            )
        )

    @router.patch("/tables/{table_id}/columns/{column_id}")
    def patch_column(
        table_id: str,
        column_id: str,
        request: Request,
        body: TableColumnPatchRequest,
        response: Response,
    ):
        _require_trusted_mutation(request)
        _no_store(response)
        return _call(
            lambda: _service(request).daily_tables.update_column(
                table_id, column_id, **body.model_dump(exclude_unset=True)
            )
        )

    @router.delete("/tables/{table_id}/columns/{column_id}")
    def delete_column(
        table_id: str,
        column_id: str,
        request: Request,
        response: Response,
    ):
        _require_trusted_mutation(request)
        _no_store(response)
        return _call(
            lambda: _service(request).daily_tables.delete_column(
                table_id, column_id
            )
        )

    @router.put("/tables/{table_id}/cells")
    def put_cells(
        table_id: str,
        request: Request,
        body: CellsPutRequest,
        response: Response,
    ):
        _require_trusted_mutation(request)
        _no_store(response)
        return _call(
            lambda: _service(request).daily_tables.put_cells(
                table_id, updates=body.model_dump()["updates"]
            )
        )

    @router.get("/tables/{table_id}/export.csv")
    def export_table_csv(table_id: str, request: Request):
        filename, content = _call(
            lambda: _service(request).daily_tables.export_csv(table_id)
        )
        return Response(
            content=content.encode("utf-8"),
            media_type="text/csv; charset=utf-8",
            headers={
                "Cache-Control": "no-store, max-age=0",
                "Pragma": "no-cache",
                "Content-Disposition": (
                    'attachment; filename="daily-table.csv"; '
                    f"filename*=UTF-8''{quote(filename)}"
                ),
            },
        )

    return router


__all__ = ["create_daily_table_router"]
