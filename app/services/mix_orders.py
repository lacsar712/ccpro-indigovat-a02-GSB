"""还原母液兑比单业务规则。

两条时间规则并行，缺一不可：

1. 自然周：每周一为一周起点。同一染缸同一自然周内最多保留一张未作废
   兑比单；闲置缸改还原中只认「本周」最新的合格、未作废单。
2. 五日新鲜度：本周合格单的开单日距今不得超过 5 个自然日
   （开单日当天记 0 天）。
"""

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Optional

from sqlalchemy.orm import Session

from app.models import MixOrder, Vat, User

# 母液升占清水升的比例上限
MOTHER_RATIO_LIMIT = Decimal("0.40")
# 合格单开单日距今天数上限（5 个自然日）
MAX_ORDER_AGE_DAYS = 5


class MixRuleError(Exception):
    def __init__(self, message: str):
        self.message = message
        super().__init__(message)


class MixPermissionError(Exception):
    """需要主管权限。"""

    def __init__(self, message: str = "只有主管可以作废兑比单。"):
        self.message = message
        super().__init__(message)


def week_start(day: date) -> date:
    """返回 day 所在自然周的周一（Monday-based）。"""
    return day - timedelta(days=day.weekday())


def week_range(day: date) -> tuple[date, date]:
    monday = week_start(day)
    return monday, monday + timedelta(days=6)


def _orders_in_week(db: Session, vat_id: int, day: date) -> list[MixOrder]:
    monday, sunday = week_range(day)
    return (
        db.query(MixOrder)
        .filter(
            MixOrder.vat_id == vat_id,
            MixOrder.voided.is_(False),
            MixOrder.issuedOn >= monday,
            MixOrder.issuedOn <= sunday,
        )
        .order_by(MixOrder.issuedOn.desc(), MixOrder.id.desc())
        .all()
    )


def latest_qualified_order(
    db: Session, vat_id: int, today: Optional[date] = None
) -> Optional[MixOrder]:
    """该缸本自然周最新一张「合格且未作废」的兑比单；无则 None。

    作废单（voided=True）与不合格单一律不作为合格依据。
    """
    today = today or date.today()
    monday, sunday = week_range(today)
    return (
        db.query(MixOrder)
        .filter(
            MixOrder.vat_id == vat_id,
            MixOrder.isQualified.is_(True),
            MixOrder.voided.is_(False),
            MixOrder.issuedOn >= monday,
            MixOrder.issuedOn <= sunday,
        )
        .order_by(MixOrder.issuedOn.desc(), MixOrder.id.desc())
        .first()
    )


def create_mix_order(
    db: Session,
    *,
    vat: Vat,
    issued_on: date,
    mother_l: Decimal,
    water_l: Decimal,
    is_qualified: bool,
    chemist: str,
    created_by: User,
    today: Optional[date] = None,
) -> MixOrder:
    """开一张兑比单（染缸工负责开单）。校验失败抛 MixRuleError。"""
    today = today or date.today()

    if issued_on > today:
        raise MixRuleError("开单日不能晚于今天。")
    chemist = chemist.strip()
    if not chemist:
        raise MixRuleError("化验人不能为空。")
    if mother_l <= 0 or water_l <= 0:
        raise MixRuleError("母液升数与清水升数均须大于 0。")
    if mother_l > water_l * MOTHER_RATIO_LIMIT:
        raise MixRuleError(
            f"母液 {mother_l} 升超过清水 {water_l} 升的 40%"
            f"（上限 {water_l * MOTHER_RATIO_LIMIT} 升），不能开单。"
        )

    # 同缸同自然周最多保留一张未作废单（合格/不合格都占位）
    existing = _orders_in_week(db, vat.id, issued_on)
    if existing:
        held = existing[0]
        raise MixRuleError(
            f"染缸 {vat.code} 在本自然周（周一起）已有未作废兑比单 "
            f"{held.order_no}，不能重复开单；如需重开请先由主管作废原单。"
        )

    order = MixOrder(
        vat_id=vat.id,
        issuedOn=issued_on,
        motherL=mother_l,
        waterL=water_l,
        isQualified=is_qualified,
        chemist=chemist,
        created_by=created_by.id,
        voided=False,
        createdAt=datetime.now(timezone.utc),
    )
    db.add(order)
    db.flush()  # 取单号 order_no
    return order


def void_mix_order(db: Session, order: MixOrder, supervisor: User) -> MixOrder:
    """主管作废。作废后该单不再作为合格依据。"""
    if not supervisor.is_superuser:
        raise MixPermissionError()
    if order.voided:
        raise MixRuleError(f"兑比单 {order.order_no} 已作废，不能重复作废。")
    order.voided = True
    order.voided_by = supervisor.id
    order.voidedAt = datetime.now(timezone.utc)
    db.flush()
    return order


def assert_can_start_reduction(
    db: Session, vat: Vat, today: Optional[date] = None
) -> MixOrder:
    """闲置缸改还原中的共用判定：须有本周合格单且开单日不超过 5 个自然日。

    通过则返回作为依据的兑比单；否则抛 MixRuleError（中文拒绝）。
    状态变更端点必须调用本函数，绕过专页直接改状态同样受此约束。
    """
    today = today or date.today()
    order = latest_qualified_order(db, vat.id, today)
    if order is None:
        raise MixRuleError(
            f"染缸 {vat.code} 本自然周（周一起）没有合格兑比单，"
            "不能改为还原中：请先到「兑比单」专页开单并判为合格。"
        )
    age = (today - order.issuedOn).days
    if age > MAX_ORDER_AGE_DAYS:
        raise MixRuleError(
            f"染缸 {vat.code} 本周最新合格兑比单 {order.order_no} "
            f"开单日为 {order.issuedOn.isoformat()}，距今已 {age} 个自然日"
            f"（超过 {MAX_ORDER_AGE_DAYS} 日），不能作为还原依据，请重新开单。"
        )
    return order
