"""业务错误类型，由 main 中注册的处理器统一转成结构化 JSON。"""
from __future__ import annotations

from typing import Any


class AppError(Exception):
    status_code = 400
    code = "bad_request"

    def __init__(self, message: str | None = None, **payload: Any):
        super().__init__(message or self.code)
        self.message = message or self.code
        self.payload = payload

    def to_body(self) -> dict[str, Any]:
        body: dict[str, Any] = {"error": self.code, "message": self.message}
        body.update(self.payload)
        return body


class InvalidRingError(AppError):
    status_code = 422
    code = "invalid_ring"


class ParcelConflictError(AppError):
    status_code = 409
    code = "parcel_interior_conflict"

    def __init__(self, conflicts: list[dict[str, Any]]):
        parcel_nos = sorted({p for c in conflicts for p in c["parcels"]})
        super().__init__(
            "整单校验未通过：宗地内部相交，整单已退回，图形与签收状态均未改变",
            conflicts=conflicts,
            conflict_parcel_nos=parcel_nos,
        )


class OrderNoExistsError(AppError):
    status_code = 409
    code = "order_no_exists"


class NotFoundError(AppError):
    status_code = 404
    code = "not_found"


class StaleSignoffError(AppError):
    status_code = 409
    code = "stale_signoff"


class EdgeRemovedError(AppError):
    status_code = 409
    code = "edge_removed"
