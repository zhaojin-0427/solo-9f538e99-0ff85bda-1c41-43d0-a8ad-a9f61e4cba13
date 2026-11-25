"""轴对齐多边形的纯向量几何运算。

设计约束（对应任务要求）：
- 地块无洞，只有一个外环；
- 边全部与坐标轴平行；
- 顶点为 ±100 万米内的整数坐标；
- 不逐米铺格，所有关系都用整数坐标上的线段 / 区间运算判定；
- 单点接触不算共边：共边只保留长度严格大于 0 的重叠区间。

环在本模块内统一规范化为：去掉闭合尾点与共线冗余点、逆时针 (CCW)、
元素为 ``(x: int, y: int)`` 的列表。
"""
from __future__ import annotations

from typing import Any, Iterable

from .config import MAX_COORD, MIN_COORD
from .errors import InvalidRingError

Point = tuple[int, int]
BBox = tuple[int, int, int, int]  # minx, miny, maxx, maxy
# (orientation, fixed, lo, hi)；h: fixed=y, lo/hi=x；v: fixed=x, lo/hi=y
Seg = tuple[str, int, int, int]
# 带内侧方向的边：side=+1 表示内部在 fixed 增大一侧
DSeg = tuple[str, int, int, int, int]

_MIN_VERTICES = 4


# ---------------------------------------------------------------- 环规范化

def normalize_ring(raw: Any, parcel_no: str | None = None) -> list[Point]:
    """校验并规范化一个外环，失败时抛 :class:`InvalidRingError`。"""
    label = f"宗地 {parcel_no}" if parcel_no else "地块"
    errors: list[str] = []

    if not isinstance(raw, (list, tuple)) or len(raw) == 0:
        raise InvalidRingError(f"{label}：外环必须是非空的 [x, y] 顶点数组")

    pts: list[Point] = []
    for i, p in enumerate(raw):
        where = f"{label}第 {i + 1} 个顶点"
        if not isinstance(p, (list, tuple)) or len(p) != 2:
            errors.append(f"{where}必须是 [x, y] 两元组")
            continue
        x, y = p
        if isinstance(x, bool) or isinstance(y, bool) or not isinstance(x, int) or not isinstance(y, int):
            errors.append(f"{where}坐标必须是整数")
            continue
        if not (MIN_COORD <= x <= MAX_COORD and MIN_COORD <= y <= MAX_COORD):
            errors.append(f"{where}=({x},{y}) 超出 ±100 万米范围")
            continue
        pts.append((x, y))

    if errors:
        raise InvalidRingError(f"{label}外环校验失败", parcel_no=parcel_no, errors=errors)

    # 去掉显式闭合点（首尾相同），可重复处理
    while len(pts) >= 2 and pts[0] == pts[-1]:
        pts.pop()

    # 去掉连续重复点
    deduped: list[Point] = []
    for p in pts:
        if not deduped or deduped[-1] != p:
            deduped.append(p)
    if len(deduped) >= 2 and deduped[0] == deduped[-1]:
        deduped.pop()
    pts = deduped

    if len(pts) < _MIN_VERTICES:
        raise InvalidRingError(
            f"{label}：轴对齐非退化外环至少需要 {_MIN_VERTICES} 个不重复顶点，当前 {len(pts)} 个",
            parcel_no=parcel_no,
        )

    n = len(pts)
    for i in range(n):
        ax, ay = pts[i]
        bx, by = pts[(i + 1) % n]
        if ax != bx and ay != by:
            raise InvalidRingError(
                f"{label}：边 ({ax},{ay})→({bx},{by}) 不与坐标轴平行",
                parcel_no=parcel_no,
            )

    pts = _simplify_orthogonal(pts, label, parcel_no)

    area2 = signed_area2(pts)
    if area2 == 0:
        raise InvalidRingError(f"{label}：外环面积为 0（退化环）", parcel_no=parcel_no)
    if area2 < 0:
        pts.reverse()  # 统一为 CCW

    _assert_simple(pts, label, parcel_no)
    return pts


def _simplify_orthogonal(pts: list[Point], label: str, parcel_no: str | None) -> list[Point]:
    """合并共线连续边；遇到原路折回直接判为退化 / 自重叠。"""
    n = len(pts)
    directions: list[str] = []
    for i in range(n):
        ax, ay = pts[i]
        bx, by = pts[(i + 1) % n]
        dx, dy = bx - ax, by - ay
        if dx == 0 and dy == 0:
            raise InvalidRingError(f"{label}：存在零长度边", parcel_no=parcel_no)
        if dx != 0 and dy != 0:
            raise InvalidRingError(f"{label}：边不与坐标轴平行", parcel_no=parcel_no)
        if dx > 0:
            directions.append("E")
        elif dx < 0:
            directions.append("W")
        elif dy > 0:
            directions.append("N")
        else:
            directions.append("S")

    opposite = {"E": "W", "W": "E", "N": "S", "S": "N"}
    for i in range(n):
        incoming = directions[(i - 1) % n]
        outgoing = directions[i]
        if outgoing == opposite[incoming]:
            raise InvalidRingError(
                f"{label}：顶点 {pts[i]} 处边界原路折回，形成退化 / 自重叠",
                parcel_no=parcel_no,
            )

    kept = [pts[i] for i in range(n) if directions[(i - 1) % n] != directions[i]]
    if len(kept) < _MIN_VERTICES:
        raise InvalidRingError(f"{label}：简化后顶点不足，图形退化", parcel_no=parcel_no)
    return kept


def _assert_simple(pts: list[Point], label: str, parcel_no: str | None) -> None:
    """非相邻边不允许有任何交点（含端点接触），保证是简单外环。"""
    segs = [canonical_seg(pts[i], pts[(i + 1) % len(pts)]) for i in range(len(pts))]
    n = len(segs)
    for i in range(n):
        for j in range(i + 1, n):
            if j == i + 1 or (i == 0 and j == n - 1):
                continue  # 相邻边共享端点，合法
            if segs_intersect_any(segs[i], segs[j]):
                raise InvalidRingError(
                    f"{label}：外环自相交（边 {i + 1} 与边 {j + 1} 相交）",
                    parcel_no=parcel_no,
                )


# ---------------------------------------------------------------- 基础运算

def signed_area2(ring: list[Point]) -> int:
    """两倍有向面积（整数坐标下必为整数）。"""
    s = 0
    n = len(ring)
    for i in range(n):
        x1, y1 = ring[i]
        x2, y2 = ring[(i + 1) % n]
        s += x1 * y2 - x2 * y1
    return s


def bbox(ring: Iterable[Point]) -> BBox:
    xs = [p[0] for p in ring]
    ys = [p[1] for p in ring]
    return min(xs), min(ys), max(xs), max(ys)


def bboxes_intersect(a: BBox, b: BBox) -> bool:
    """外接框是否相交（边界相接也算相交，作为候选集采取宁多勿漏策略）。"""
    return a[0] <= b[2] and b[0] <= a[2] and a[1] <= b[3] and b[1] <= a[3]


def canonical_seg(a: Point, b: Point) -> Seg:
    if a[1] == b[1]:
        return ("h", a[1], min(a[0], b[0]), max(a[0], b[0]))
    return ("v", a[0], min(a[1], b[1]), max(a[1], b[1]))


def directed_seg(a: Point, b: Point) -> DSeg:
    """返回规范区间以及 CCW 环中该边的内部朝向。

    side=+1：水平边内部在 y 更大一侧 / 竖直边内部在 x 更大一侧。
    """
    seg = canonical_seg(a, b)
    if a[1] == b[1]:  # h
        side = 1 if b[0] > a[0] else -1  # 向右走(E)左侧为 +y
    else:  # v：向上走(N)左侧为 -x
        side = -1 if b[1] > a[1] else 1
    return (seg[0], seg[1], seg[2], seg[3], side)


def segs_intersect_any(s: Seg, t: Seg) -> bool:
    """两条轴对齐线段是否有任意公共点（含端点）。"""
    o1, f1, l1, h1 = s
    o2, f2, l2, h2 = t
    if o1 == o2:
        return f1 == f2 and max(l1, l2) <= min(h1, h2)
    hs, vs = (s, t) if o1 == "h" else (t, s)
    _, hy, xl, xr = hs
    _, vx, yl, yr = vs
    return xl <= vx <= xr and yl <= hy <= yr


def point_relation(p: Point, ring: list[Point]) -> str:
    """整点相对环的位置：``inside`` / ``boundary`` / ``outside``。

    射线法 + 严格的边界判定，全部使用整数比较。
    """
    x, y = p
    inside = False
    n = len(ring)
    for i in range(n):
        ax, ay = ring[i]
        bx, by = ring[(i + 1) % n]
        if ay == by:  # 水平边
            if y == ay and min(ax, bx) <= x <= max(ax, bx):
                return "boundary"
        else:  # 竖直边（轴对齐前提下 ax == bx）
            if x == ax and min(ay, by) <= y <= max(ay, by):
                return "boundary"
        # 严格跨纬度判定，消除顶点处的二义性
        if (ay > y) != (by > y):
            x_cross = ax  # 跨 y 的必为竖直边
            if x < x_cross:
                inside = not inside
    return "inside" if inside else "outside"


# ---------------------------------------------------------------- 宗地关系

def has_interior_overlap(a: list[Point], b: list[Point]) -> bool:
    """两宗地块的内部是否相交（正面积重叠）。

    仅边界重合 / 单点接触返回 False（允许共边、对点）。
    """
    ba, bb = bbox(a), bbox(b)
    # 外接框交集若退化为线或点，内部不可能相交
    if min(ba[2], bb[2]) <= max(ba[0], bb[0]):
        return False
    if min(ba[3], bb[3]) <= max(ba[1], bb[1]):
        return False

    # 1) 任一顶点严格落入对方内部（覆盖包含、嵌套等无穿越情形）
    for p in a:
        if point_relation(p, b) == "inside":
            return True
    for p in b:
        if point_relation(p, a) == "inside":
            return True

    # 2) 边对判定
    ea = [directed_seg(a[i], a[(i + 1) % len(a)]) for i in range(len(a))]
    eb = [directed_seg(b[i], b[(i + 1) % len(b)]) for i in range(len(b))]

    for o1, f1, l1, h1, side1 in ea:
        for o2, f2, l2, h2, side2 in eb:
            if o1 == o2:
                if f1 != f2:
                    continue
                lo = max(l1, l2)
                hi = min(h1, h2)
                if hi <= lo:
                    continue  # 仅端点接触或不接触
                # 正长度共线重叠：内部同侧 => 面积重叠冲突；
                # 异侧 => 合法共边。
                if side1 == side2:
                    return True
                continue

            # 垂直相交
            if o1 == "h":
                hy, xl, xr = f1, l1, h1
                vx, yl, yr = f2, l2, h2
            else:
                hy, xl, xr = f2, l2, h2
                vx, yl, yr = f1, l1, h1
            if not (xl <= vx <= xr and yl <= hy <= yr):
                continue
            # 交点同时严格位于两条边内部 => 边界真正穿越，内部必重叠。
            # 端点 / T 接情形由“共线重叠内侧判定”和“顶点严格入内”覆盖。
            if xl < vx < xr and yl < hy < yr:
                return True

    return False


def shared_segments(a: list[Point], b: list[Point]) -> list[Seg]:
    """提取两环之间的全部极大共边区间。

    只保留长度严格大于 0 的共线重叠；同一支撑线上的重叠区间会合并成
    极大区间。返回排序后的 ``(orientation, fixed, lo, hi)`` 列表。
    单点接触天然被 ``hi > lo`` 排除。
    """
    groups: dict[tuple[str, int], list[tuple[int, int]]] = {}
    sa = [canonical_seg(a[i], a[(i + 1) % len(a)]) for i in range(len(a))]
    sb = [canonical_seg(b[i], b[(i + 1) % len(b)]) for i in range(len(b))]

    for o1, f1, l1, h1 in sa:
        for o2, f2, l2, h2 in sb:
            if o1 != o2 or f1 != f2:
                continue
            lo = max(l1, l2)
            hi = min(h1, h2)
            if hi > lo:  # 严格正长度，排除单点
                groups.setdefault((o1, f1), []).append((lo, hi))

    result: list[Seg] = []
    for (o, f), intervals in groups.items():
        intervals.sort()
        cur_lo, cur_hi = intervals[0]
        for lo, hi in intervals[1:]:
            if lo <= cur_hi:  # 重叠或端点相接 => 合并为极大范围
                cur_hi = max(cur_hi, hi)
            else:
                result.append((o, f, cur_lo, cur_hi))
                cur_lo, cur_hi = lo, hi
        result.append((o, f, cur_lo, cur_hi))

    result.sort()
    return result


# ---------------------------------------------------------------- 候选集规划

def plan_ring_reads(
    all_bboxes: Iterable[tuple[str, BBox]],
    order_parcel_nos: set[str],
    query_boxes: Iterable[BBox],
) -> set[str]:
    """决定本单需要回读哪些宗地的外环。

    规则（对应原文）：回读“外接框与本单任一新、旧外接框相交”的宗地外环；
    本单录入 / 更正的宗地无论如何都在集合内。只做外接框整数比较。
    """
    boxes = list(query_boxes)
    selected: set[str] = set(order_parcel_nos)
    for parcel_no, box in all_bboxes:
        if any(bboxes_intersect(box, q) for q in boxes):
            selected.add(parcel_no)
    return selected
