"""染缸状态与还原母液兑比业务规则。"""

from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Optional

from sqlalchemy.orm import Session

from app.models import DipLot, ReductionMixOrder, User, Vat

# 母液体积不得超过清水体积的 40%
MOTHER_RATIO = Decimal("0.40")
# 合格兑比单的有效期（开单日起 5 个自然日）
ORDER_FRESH_DAYS = 5


class VatRuleError(Exception):
    def __init__(self, message: str):
        self.message = message
        super().__init__(message)


def assert_can_mark_ready(latest: Optional[DipLot]) -> None:
    """不能将染缸标为 ready，除非最新浸染批次 redoxMv 已填且 <= -500。"""
    if latest is None or latest.redoxMv is None or Decimal(latest.redoxMv) > Decimal("-500"):
        raise VatRuleError(
            "无法设为可染色：最新浸染批次的氧化还原电位为空或高于 -500 mV。"
        )


def natural_week_start(day: date) -> date:
    """自然周起点：周一。"""
    return day - timedelta(days=day.weekday())


def assert_mix_amounts(mother_l: Decimal, water_l: Decimal) -> None:
    """母液、清水均须大于 0，且母液不超过清水的 40%。"""
    if mother_l is None or water_l is None or mother_l <= 0 or water_l <= 0:
        raise VatRuleError("母液升与清水升都必须大于 0。")
    if mother_l > water_l * MOTHER_RATIO:
        raise VatRuleError(
            f"母液 {mother_l} 升超过清水 {water_l} 升的 40%（上限 "
            f"{(water_l * MOTHER_RATIO).quantize(Decimal('0.01'))} 升），兑比不合格。"
        )


def find_open_order_in_week(
    db: Session, vat_id: int, issued_on: date
) -> Optional[ReductionMixOrder]:
    """该缸在开单日所在自然周内是否已有未作废兑比单。"""
    start = natural_week_start(issued_on)
    end = start + timedelta(days=6)
    return (
        db.query(ReductionMixOrder)
        .filter(
            ReductionMixOrder.vat_id == vat_id,
            ReductionMixOrder.issuedOn >= start,
            ReductionMixOrder.issuedOn <= end,
            ReductionMixOrder.voided.is_(False),
        )
        .order_by(ReductionMixOrder.issuedOn.desc(), ReductionMixOrder.id.desc())
        .first()
    )


def assert_worker_can_issue(user: User) -> None:
    """开单是染缸工职责，主管不走开单流程。"""
    if user is None or user.is_superuser:
        raise VatRuleError("只有染缸工可以开还原母液兑比单，主管负责审核与作废。")


def assert_supervisor_can_void(user: User) -> None:
    if user is None or not user.is_superuser:
        raise VatRuleError("只有主管可以作废兑比单。")


def assert_can_issue_mix(
    db: Session,
    *,
    vat_id: int,
    issued_on: date,
    mother_l: Decimal,
    water_l: Decimal,
    user: User,
) -> None:
    assert_worker_can_issue(user)
    assert_mix_amounts(mother_l, water_l)
    existing = find_open_order_in_week(db, vat_id, issued_on)
    if existing is not None:
        raise VatRuleError(
            f"该染缸本自然周（周一算起）已有未作废兑比单 {existing.code}，"
            "不能重复开单；如需重开请先由主管作废原单。"
        )


def latest_qualified_order(
    db: Session, vat_id: int, today: date
) -> Optional[ReductionMixOrder]:
    """本自然周内该缸最新一张合格且未作废的兑比单。"""
    start = natural_week_start(today)
    return (
        db.query(ReductionMixOrder)
        .filter(
            ReductionMixOrder.vat_id == vat_id,
            ReductionMixOrder.issuedOn >= start,
            ReductionMixOrder.issuedOn <= today,
            ReductionMixOrder.passed.is_(True),
            ReductionMixOrder.voided.is_(False),
        )
        .order_by(ReductionMixOrder.issuedOn.desc(), ReductionMixOrder.id.desc())
        .first()
    )


def assert_can_start_reducing(
    db: Session, vat: Vat, today: Optional[date] = None
) -> ReductionMixOrder:
    """闲置缸进入「还原中」的唯一合格判定：本自然周最新合格单 + 开单日 5 个自然日内。

    状态改动与专页展示共用本函数，作废单不得作为合格依据。
    """
    if today is None:
        today = datetime.now().date()
    order = latest_qualified_order(db, vat.id, today)
    if order is None:
        raise VatRuleError(
            "无法改为还原中：该缸本自然周（周一算起）没有合格的还原母液兑比单，"
            "请先由染缸工在兑比专页开单并判合格。"
        )
    age = today - order.issuedOn
    if age > timedelta(days=ORDER_FRESH_DAYS):
        raise VatRuleError(
            f"无法改为还原中：本周最新合格单 {order.code} 开单日为 "
            f"{order.issuedOn.isoformat()}，已超过 5 个自然日，须重新开单。"
        )
    return order


def validate_vat_status_change(
    db: Session,
    vat: Vat,
    new_status: str,
    latest: Optional[DipLot],
    today: Optional[date] = None,
) -> None:
    if new_status == Vat.STATUS_READY:
        assert_can_mark_ready(latest)
    elif new_status == Vat.STATUS_REDUCING and vat.status == Vat.STATUS_IDLE:
        # 闲置进还原中（含绕过专页直改状态）都走同一判定
        assert_can_start_reducing(db, vat, today)
