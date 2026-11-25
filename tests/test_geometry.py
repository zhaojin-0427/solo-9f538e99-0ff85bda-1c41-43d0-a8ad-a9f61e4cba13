"""几何核心算法单测。"""
from __future__ import annotations

import pytest

from app.errors import InvalidRingError
from app.geometry import (
    has_interior_overlap,
    normalize_ring,
    plan_ring_reads,
    shared_segments,
)


def rect(x0, y0, x1, y1):
    return [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]


def test_normalize_rectangle_ccw_and_strip_closing_point():
    ring = normalize_ring([[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]])
    assert ring == [(0, 0), (10, 0), (10, 10), (0, 10)]
    # shoelace positive after normalization
    from app.geometry import signed_area2

    assert signed_area2(ring) == 200


def test_collinear_vertices_are_merged():
    ring = normalize_ring([(0, 0), (5, 0), (10, 0), (10, 10), (0, 10)])
    assert (5, 0) not in ring
    assert len(ring) == 4


@pytest.mark.parametrize(
    "raw",
    [
        [(0, 0), (1, 1), (2, 0), (2, 2), (0, 2)],  # 斜边
        [(0, 0), (10, 0), (10, 10)],  # 顶点不足 / 退化
        [(0, 0), (10, 0), (10, 10), (10, 5), (10, 10), (0, 10)],  # 原路折回
    ],
)
def test_invalid_rings_rejected(raw):
    with pytest.raises(InvalidRingError):
        normalize_ring(raw, "X")


def test_coord_out_of_range_rejected():
    with pytest.raises(InvalidRingError):
        normalize_ring(rect(0, 0, 1_000_001, 10))
    with pytest.raises(InvalidRingError):
        normalize_ring([(-1_000_001, 0), (0, 0), (0, 1), (-1_000_001, 1)])


def test_non_integer_coord_rejected():
    with pytest.raises(InvalidRingError):
        normalize_ring([(0, 0), (10.5, 0), (10, 10), (0, 10)])


def test_self_intersecting_orthogonal_ring_rejected():
    # 轴对齐但横边 y=4/6 穿过非相邻竖边 x=10，构成自交
    bad = [
        (0, 0), (10, 0), (10, 10), (0, 10), (0, 6),
        (12, 6), (12, 4), (-2, 4), (-2, 0),
    ]
    with pytest.raises(InvalidRingError):
        normalize_ring(bad, "BAD")


def test_adjacent_parcels_shared_vertical_edge():
    a = normalize_ring(rect(0, 0, 10, 10))
    b = normalize_ring(rect(10, 0, 20, 10))
    assert has_interior_overlap(a, b) is False
    segs = shared_segments(a, b)
    assert segs == [("v", 10, 0, 10)]


def test_single_point_contact_is_not_shared_edge():
    a = normalize_ring(rect(0, 0, 10, 10))
    b = normalize_ring(rect(10, 10, 20, 20))  # 仅在 (10,10) 接触
    assert has_interior_overlap(a, b) is False
    assert shared_segments(a, b) == []


def test_overlapping_interiors_are_conflict():
    a = normalize_ring(rect(0, 0, 10, 10))
    b = normalize_ring(rect(5, 5, 15, 15))
    assert has_interior_overlap(a, b) is True


def test_crossing_bars_conflict_without_vertex_inside():
    horizontal = normalize_ring(rect(0, 4, 10, 6))
    vertical = normalize_ring(rect(4, 0, 6, 10))
    assert has_interior_overlap(horizontal, vertical) is True


def test_containment_is_conflict():
    outer = normalize_ring(rect(0, 0, 20, 20))
    inner = normalize_ring(rect(5, 5, 10, 10))
    assert has_interior_overlap(outer, inner) is True
    assert has_interior_overlap(inner, outer) is True


def test_identical_parcels_are_conflict():
    a = normalize_ring(rect(0, 0, 10, 10))
    b = normalize_ring(rect(0, 0, 10, 10))
    assert has_interior_overlap(a, b) is True


def test_t_junction_attachment_is_allowed():
    # A 为大块，B 从上方贴住 A 顶边的一段，内侧方向相反 => 合法 T 接共边
    a = normalize_ring(rect(0, 0, 10, 10))
    b = normalize_ring(rect(2, 10, 8, 14))
    assert has_interior_overlap(a, b) is False
    assert shared_segments(a, b) == [("h", 10, 2, 8)]


def test_partial_shared_interval_is_reported():
    a = normalize_ring(rect(0, 0, 10, 10))
    b = normalize_ring(rect(10, 2, 20, 8))
    assert has_interior_overlap(a, b) is False
    assert shared_segments(a, b) == [("v", 10, 2, 8)]


def test_l_shaped_polygon_shared_segments():
    # L 形地块与两块相邻地分别共边
    l_shape = normalize_ring([(0, 0), (10, 0), (10, 5), (5, 5), (5, 10), (0, 10)])
    right = normalize_ring(rect(10, 0, 15, 5))
    top = normalize_ring(rect(0, 10, 5, 15))
    assert has_interior_overlap(l_shape, right) is False
    assert shared_segments(l_shape, right) == [("v", 10, 0, 5)]
    assert shared_segments(l_shape, top) == [("h", 10, 0, 5)]


def test_plan_ring_reads_uses_any_new_or_old_bbox_intersection():
    all_boxes = [
        ("NEAR", (9, -1, 11, 11)),
        ("FAR", (100, 100, 120, 120)),
    ]
    selected = plan_ring_reads(
        all_boxes,
        order_parcel_nos={"A"},
        query_boxes=[(0, 0, 10, 10), (0, 0, 8, 8)],
    )
    assert "A" in selected
    assert "NEAR" in selected
    assert "FAR" not in selected
