"""请求 / 响应模型（同时用于 /docs 展示）。"""
from __future__ import annotations

from typing import Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, StrictInt, field_validator


class ParcelRingIn(BaseModel):
    parcel_no: str = Field(..., min_length=1, max_length=64, description="宗地号")
    # 严格整数：1.0、true 之类不会被接受
    ring: list[tuple[StrictInt, StrictInt]] = Field(
        ..., min_length=4, description="地块外环顶点，按顺序首尾不必重复；无洞"
    )
    # 领域约束为“地块无洞”：允许显式传空数组，传非空内环一律拒绝
    holes: list[list[tuple[StrictInt, StrictInt]]] = Field(
        default_factory=list, description="必须为空：本地块模型只有外环、无洞"
    )

    @field_validator("parcel_no")
    @classmethod
    def _strip_no(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("宗地号不能为空白")
        return v

    @field_validator("holes")
    @classmethod
    def _no_holes(cls, v: list) -> list:
        if v:
            raise ValueError("地块无洞：不接受任何内环 holes")
        return v


class OrderIn(BaseModel):
    order_no: str | None = Field(
        default=None, max_length=64, description="单号；不传则服务端生成 ORD-xxxx"
    )
    parcels: list[ParcelRingIn] = Field(..., min_length=1, description="本单整单替换的宗地外环")

    @field_validator("order_no")
    @classmethod
    def _strip_order_no(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = v.strip()
        return v or None

    @field_validator("parcels")
    @classmethod
    def _unique_parcel_numbers(cls, v: list[ParcelRingIn]) -> list[ParcelRingIn]:
        nos = [p.parcel_no for p in v]
        dup = sorted({n for n in nos if nos.count(n) > 1})
        if dup:
            raise ValueError(f"同一单内宗地号重复: {dup}")
        return v


class DirectRingIn(BaseModel):
    """单宗地录入 / 更正的便捷入口（内部仍按一宗地的整单处理）。"""

    order_no: str | None = Field(default=None, max_length=64)
    ring: list[tuple[StrictInt, StrictInt]] = Field(..., min_length=4)


class SignIn(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    verifier: str = Field(..., min_length=1, max_length=64, description="核验员标识 / 姓名")
    # 标准字段名 seen_version；同时兼容调用方直接传 version
    seen_version: int = Field(
        ...,
        ge=1,
        description="核验员在查询结果中看到的共边版本（也可用字段名 version）",
        validation_alias=AliasChoices("seen_version", "version"),
    )

    @field_validator("verifier")
    @classmethod
    def _strip_verifier(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("verifier 不能为空白")
        return v


class PairSignItemIn(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    edge_id: int = Field(..., description="宗地对查询结果中的 edge_id")
    seen_version: int = Field(
        ...,
        ge=1,
        validation_alias=AliasChoices("seen_version", "version"),
        description="所见版本（也可用字段名 version）",
    )


class PairSignIn(BaseModel):
    verifier: str = Field(..., min_length=1, max_length=64)
    items: list[PairSignItemIn] = Field(..., min_length=1, description="本批签收的线及各自所见版本")

    @field_validator("verifier")
    @classmethod
    def _strip_verifier(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("verifier 不能为空白")
        return v


Orientation = Literal["horizontal", "vertical"]
EdgeState = Literal["signed", "unsigned", "removed"]
SignStatus = Literal["signed", "unsigned", "stale", "removed"]
