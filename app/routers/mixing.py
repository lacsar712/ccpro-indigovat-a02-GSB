"""还原母液兑比专页：列表、染缸工开单、主管作废。"""

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Optional

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session, joinedload

from app.auth import get_current_user
from app.db import get_db
from app.models import ReductionMixOrder, User, Vat
from app.services.vat_rules import (
    VatRuleError,
    assert_can_issue_mix,
    assert_supervisor_can_void,
    natural_week_start,
)

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")

STATUS_LABELS = {
    Vat.STATUS_IDLE: "闲置",
    Vat.STATUS_REDUCING: "还原中",
    Vat.STATUS_READY: "可染色",
}


def render(request: Request, context: dict, status_code: int = 200):
    ctx = {k: v for k, v in context.items() if k != "request"}
    return templates.TemplateResponse(request, "mixing.html", ctx, status_code=status_code)


def _order_payload(order: ReductionMixOrder, today: date) -> dict:
    age = (today - order.issuedOn).days
    return {
        "id": order.id,
        "code": order.code,
        "vatId": order.vat_id,
        "vatCode": order.vat.code if order.vat else f"#{order.vat_id}",
        "workshopName": order.vat.workshop.name if order.vat and order.vat.workshop else "",
        "issuedOn": order.issuedOn.isoformat(),
        "motherL": float(order.motherL),
        "waterL": float(order.waterL),
        "ratioPct": round(float(order.motherL / order.waterL * 100), 1)
        if order.waterL
        else None,
        "passed": order.passed,
        "chemist": order.chemist,
        "issuer": order.issuer.username if order.issuer else "",
        "voided": order.voided,
        "voidedAt": order.voided_at.strftime("%Y-%m-%d %H:%M") if order.voided_at else None,
        "voider": order.voider.username if order.voider else "",
        "ageDays": age,
        "inThisWeek": natural_week_start(today)
        <= order.issuedOn
        <= natural_week_start(today) + timedelta(days=6),
        "fresh": age <= 5,
    }


def _page_context(
    request: Request,
    db: Session,
    user: User,
    error: Optional[str] = None,
    form: Optional[dict] = None,
):
    today = datetime.now().date()
    week_start = natural_week_start(today)
    orders = (
        db.query(ReductionMixOrder)
        .options(
            joinedload(ReductionMixOrder.vat).joinedload(Vat.workshop),
            joinedload(ReductionMixOrder.issuer),
            joinedload(ReductionMixOrder.voider),
        )
        .order_by(ReductionMixOrder.issuedOn.desc(), ReductionMixOrder.id.desc())
        .all()
    )
    vats = (
        db.query(Vat)
        .options(joinedload(Vat.workshop))
        .order_by(Vat.code)
        .all()
    )
    return {
        "request": request,
        "user": user,
        "orders": [_order_payload(o, today) for o in orders],
        "vats": [
            {
                "id": v.id,
                "code": v.code,
                "workshopName": v.workshop.name if v.workshop else "",
                "status": v.status,
                "statusLabel": STATUS_LABELS.get(v.status, v.status),
            }
            for v in vats
        ],
        "today": today.isoformat(),
        "week_start": week_start.isoformat(),
        "week_end": (week_start + timedelta(days=6)).isoformat(),
        "error": error,
        "form": form or {},
        "active": "mixing",
        "is_supervisor": user.is_superuser,
    }


@router.get("/mixing", response_class=HTMLResponse)
async def mixing_page(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if not user:
        return RedirectResponse("/login", status_code=303)
    return render(request, _page_context(request, db, user))


@router.post("/mixing/orders", response_class=HTMLResponse)
async def create_mix_order(
    request: Request,
    vat_id: str = Form(...),
    issuedOn: str = Form(...),
    motherL: str = Form(...),
    waterL: str = Form(...),
    chemist: str = Form(...),
    passed: str = Form(""),
    db: Session = Depends(get_db),
):
    user = get_current_user(request, db)
    if not user:
        return RedirectResponse("/login", status_code=303)
    form = {
        "vat_id": vat_id,
        "issuedOn": issuedOn,
        "motherL": motherL,
        "waterL": waterL,
        "chemist": chemist,
        "passed": passed == "on",
    }
    try:
        vat_pk = int(vat_id)
        vat = db.get(Vat, vat_pk)
        if vat is None:
            raise VatRuleError("请选择染缸。")
        issued_on = date.fromisoformat(issuedOn)
        mother = Decimal(motherL)
        water = Decimal(waterL)
        chemist_name = chemist.strip()
        if not chemist_name:
            raise VatRuleError("化验人不能为空。")
        assert_can_issue_mix(
            db,
            vat_id=vat_pk,
            issued_on=issued_on,
            mother_l=mother,
            water_l=water,
            user=user,
        )
        order = ReductionMixOrder(
            vat_id=vat_pk,
            issuedOn=issued_on,
            motherL=mother,
            waterL=water,
            passed=(passed == "on"),
            chemist=chemist_name,
            issued_by_id=user.id,
        )
        db.add(order)
        db.flush()
        order.code = f"DM-{issued_on.strftime('%Y%m%d')}-{order.id:04d}"
        db.commit()
        return RedirectResponse("/mixing", status_code=303)
    except VatRuleError as exc:
        db.rollback()
        return render(
            request, _page_context(request, db, user, exc.message, form), status_code=400
        )
    except (ValueError, InvalidOperation) as exc:
        db.rollback()
        return render(
            request,
            _page_context(request, db, user, f"兑比单输入无效：{exc}", form),
            status_code=400,
        )


@router.post("/mixing/orders/{pk}/void", response_class=HTMLResponse)
async def void_mix_order(
    pk: int,
    request: Request,
    db: Session = Depends(get_db),
):
    user = get_current_user(request, db)
    if not user:
        return RedirectResponse("/login", status_code=303)
    order = db.get(ReductionMixOrder, pk)
    try:
        assert_supervisor_can_void(user)
        if order is None:
            raise VatRuleError("兑比单不存在。")
        if order.voided:
            raise VatRuleError(f"兑比单 {order.code} 已作废，无需重复操作。")
        order.voided = True
        order.voided_at = datetime.now(timezone.utc)
        order.voided_by_id = user.id
        db.commit()
        return RedirectResponse("/mixing", status_code=303)
    except VatRuleError as exc:
        db.rollback()
        return render(
            request, _page_context(request, db, user, exc.message), status_code=403
        )
