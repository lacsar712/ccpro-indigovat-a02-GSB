"""染缸状态业务规则。"""

from decimal import Decimal
from typing import Optional

from sqlalchemy.orm import Session

from app.models import DipLot, Vat
from app.services.mix_orders import (
    MixRuleError,
    assert_can_start_reduction,
)


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


def validate_vat_status_change(
    db: Session, vat: Vat, new_status: str, latest: Optional[DipLot]
) -> None:
    """改缸状态的统一入口（页面与任何状态写入共用）。

    new_status 不合法或不满足对应门槛时抛 VatRuleError（中文）。

    - idle → reducing：本自然周须有未作废的合格兑比单，且开单日不超过 5 个自然日；
    - → ready：最新浸染批次 redoxMv 已填且 ≤ -500。
    """
    if new_status not in (Vat.STATUS_IDLE, Vat.STATUS_REDUCING, Vat.STATUS_READY):
        raise VatRuleError(f"未知缸状态：{new_status}")
    if new_status == Vat.STATUS_REDUCING and vat.status == Vat.STATUS_IDLE:
        # 共用判定函数：绕过兑比专页直接改状态同样会被拦下
        try:
            assert_can_start_reduction(db, vat)
        except MixRuleError as exc:
            raise VatRuleError(exc.message) from exc
    if new_status == Vat.STATUS_READY:
        assert_can_mark_ready(latest)
