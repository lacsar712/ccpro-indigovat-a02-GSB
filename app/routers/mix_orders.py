"""还原母液兑比单专页：列表、染缸工开单、主管作废。"""

from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Optional

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session, joinedload

from app.auth import get_current_user
from app.db import get_db
from app.models import MixOrder, User, Vat, Workshop
from app.services.mix_orders import (
    MAX_ORDER_AGE_DAYS,
    MixPermissionError,
    MixRuleError,
    create_mix_order,
    void_mix_order,
    week_range,
)

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")

STATUS_LABELS = {
    Vat.STATUS_IDLE: "闲置",
    Vat.STATUS_REDUCING: "还原中",
    Vat.STATUS_READY: "可染色",
}


def _render(request: Request, name: str, context: dict, status_code: int = 200):
    ctx = {k: v for k, v in context.items() if k != "request"}
    return templates.TemplateResponse(request, name, ctx, status_code=status_code)


def _page_context(
    request: Request,
    db: Session,
    user: User,
    *,
    error: Optional[str] = None,
    form: Optional[dict] = None,
):
    today = date.today()
    monday, sunday = week_range(today)
    orders = (
        db.query(MixOrder)
        .options(joinedload(MixOrder.vat).joinedload(Vat.workshop))
        .order_by(MixOrder.issuedOn.desc(), MixOrder.id.desc())
        .all()
    )
    users = {u.id: u.username for u in db.query(User).all()}
    vats = (
        db.query(Vat)
        .options(joinedload(Vat.workshop))
        .order_by(Vat.code)
        .all()
    )

    def order_payload(o: MixOrder) -> dict:
        ratio = (
            round(float(o.motherL) / float(o.waterL) * 100, 1)
            if o.waterL and float(o.waterL) != 0
            else None
        )
        return {
            "id": o.id,
            "orderNo": o.order_no,
            "vatCode": o.vat.code if o.vat else "—",
            "workshopName": o.vat.workshop.name if o.vat and o.vat.workshop else "",
            "issuedOn": o.issuedOn.isoformat(),
            "motherL": float(o.motherL),
            "waterL": float(o.waterL),
            "ratioPct": ratio,
            "qualified": o.isQualified,
            "chemist": o.chemist,
            "createdByName": users.get(o.created_by, str(o.created_by)),
            "voided": o.voided,
            "voidedByName": users.get(o.voided_by) if o.voided_by else None,
            "voidedAt": o.voidedAt.strftime("%Y-%m-%d %H:%M") if o.voidedAt else None,
            "inThisWeek": monday <= o.issuedOn <= sunday,
            "ageDays": (today - o.issuedOn).days,
        }

    return {
        "request": request,
        "user": user,
        "orders": [order_payload(o) for o in orders],
        "vats": [
            {
                "id": v.id,
                "code": v.code,
                "workshopName": v.workshop.name if v.workshop else "",
                "statusLabel": STATUS_LABELS.get(v.status, v.status),
            }
            for v in vats
        ],
        "today": today.isoformat(),
        "weekMonday": monday.isoformat(),
        "weekSunday": sunday.isoformat(),
        "maxAgeDays": MAX_ORDER_AGE_DAYS,
        "error": error,
        "form": form or {},
        "active": "mix",
    }


@router.get("/mix-orders", response_class=HTMLResponse)
async def mix_orders_page(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if not user:
        return RedirectResponse("/login", status_code=303)
    return _render(request, "mix_orders.html", _page_context(request, db, user))


@router.post("/mix-orders", response_class=HTMLResponse)
async def mix_orders_create(
    request: Request,
    vat_id: str = Form(...),
    issuedOn: str = Form(...),
    motherL: str = Form(...),
    waterL: str = Form(...),
    chemist: str = Form(...),
    isQualified: str = Form(""),
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
        "isQualified": isQualified,
    }
    error: Optional[str] = None
    try:
        # 染缸工负责开单（登录后均可开，开单人留痕）；作废另限主管
        try:
            vat = db.get(Vat, int(vat_id))
        except (TypeError, ValueError):
            vat = None
        if vat is None:
            raise MixRuleError("请选择有效的染缸。")
        try:
            issued_on = date.fromisoformat(issuedOn)
            mother = Decimal(motherL)
            water = Decimal(waterL)
        except (ValueError, InvalidOperation):
            raise MixRuleError("开单日、母液升、清水升的格式不正确。")

        create_mix_order(
            db,
            vat=vat,
            issued_on=issued_on,
            mother_l=mother,
            water_l=water,
            is_qualified=(isQualified == "on"),
            chemist=chemist,
            created_by=user,
        )
        db.commit()
        return RedirectResponse("/mix-orders", status_code=303)
    except (MixRuleError, MixPermissionError) as exc:
        error = exc.message
        db.rollback()
    return _render(
        request,
        "mix_orders.html",
        _page_context(request, db, user, error=error, form=form),
        status_code=400,
    )


@router.post("/mix-orders/{pk}/void", response_class=HTMLResponse)
async def mix_orders_void(pk: int, request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if not user:
        return RedirectResponse("/login", status_code=303)

    order = db.get(MixOrder, pk)
    if order is None:
        return RedirectResponse("/mix-orders", status_code=303)
    error: Optional[str] = None
    try:
        # 仅主管可作废；作废后该单不再进入本周合格依据
        void_mix_order(db, order, user)
        db.commit()
        return RedirectResponse("/mix-orders", status_code=303)
    except (MixRuleError, MixPermissionError) as exc:
        error = exc.message
        db.rollback()
    return _render(
        request,
        "mix_orders.html",
        _page_context(request, db, user, error=error),
        status_code=403,
    )
