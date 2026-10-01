"""Draft and issue invoices, then download a Unicode PDF."""
import io
import secrets
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import RedirectResponse, StreamingResponse
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from .ingest import field, money
from .models import Bot, Company, Customer, Invoice, InvoiceLine, Payment, PaymentVerification, Project
from .security import audit, csrf, require_admin
from .web import page

router = APIRouter()
FONT = Path(__file__).parent / 'static' / 'fonts' / 'DejaVuSans.ttf'


def get_invoice(db, invoice_id):
    invoice = db.scalar(select(Invoice).options(selectinload(Invoice.lines)).where(Invoice.id == invoice_id))
    if invoice is None:
        raise HTTPException(404)
    return invoice


@router.get('/invoices')
def invoice_list(request: Request):
    with request.app.state.session_factory() as db:
        require_admin(request, db)
        return page(request, 'invoices.html', db,
            rows=db.scalars(select(Invoice).order_by(Invoice.id.desc()).limit(250)).all(),
            customers=db.scalars(select(Customer).order_by(Customer.id.desc()).limit(200)).all(),
            companies=db.scalars(select(Company).order_by(Company.id.desc()).limit(200)).all(),
            projects=db.scalars(select(Project).order_by(Project.id.desc()).limit(200)).all(),
            seller_name=request.app.state.settings.seller_name,
            seller_details=request.app.state.settings.seller_details)


@router.post('/invoices')
def invoice_create(request: Request, token: str = Form(), seller_name: str = Form(), bill_to: str = Form(),
                   currency: str = Form('UAH'), seller_details: str = Form(''), bill_to_details: str = Form(''),
                   due_at: str = Form(''), notes: str = Form(''), customer_id: str = Form(''),
                   company_id: str = Form(''), project_id: str = Form('')):
    with request.app.state.session_factory() as db:
        admin = require_admin(request, db)
        csrf(request, token)
        currency = field(currency, 3, True).upper()
        if len(currency) != 3 or not currency.isalpha():
            raise HTTPException(422, 'Invalid currency')
        try:
            due = datetime.fromisoformat(due_at).replace(tzinfo=timezone.utc) if due_at else None
        except ValueError:
            raise HTTPException(422, 'Invalid due date') from None
        ids = []
        for raw, model in ((customer_id, Customer), (company_id, Company), (project_id, Project)):
            if raw and (not raw.isdigit() or not db.get(model, int(raw))):
                raise HTTPException(422, 'Unknown client, company or project')
            ids.append(int(raw) if raw else None)
        invoice = Invoice(number='TEMP-' + secrets.token_hex(12),
            seller_name=field(seller_name, 200, True), seller_details=field(seller_details, 4000),
            bill_to=field(bill_to, 200, True), bill_to_details=field(bill_to_details, 4000),
            currency=currency, notes=field(notes, 4000), due_at=due,
            customer_id=ids[0], company_id=ids[1], project_id=ids[2], total=Decimal('0'))
        db.add(invoice); db.flush()
        invoice.number = f'INV-{datetime.now(timezone.utc).year}-{invoice.id:06d}'
        audit(db, 'invoice.create', str(invoice.id), admin.id)
        db.commit()
        return RedirectResponse(f'/invoices/{invoice.id}', status_code=303)


@router.get('/invoices/{invoice_id}')
def invoice_detail(request: Request, invoice_id: int):
    with request.app.state.session_factory() as db:
        require_admin(request, db)
        invoice = get_invoice(db, invoice_id)
        payments = []
        if invoice.customer_id and invoice.status == 'issued':
            payments = db.scalars(select(Payment).join(PaymentVerification, PaymentVerification.payment_id == Payment.id)
                .where(Payment.customer_id == invoice.customer_id, Payment.currency == invoice.currency,
                       Payment.amount == invoice.total, PaymentVerification.status == 'verified')).all()
        return page(request, 'invoice_detail.html', db, invoice=invoice, payments=payments)


@router.post('/invoices/{invoice_id}/lines')
def invoice_add_line(request: Request, invoice_id: int, token: str = Form(), title: str = Form(),
                     quantity: str = Form(), unit_price: str = Form()):
    with request.app.state.session_factory() as db:
        admin = require_admin(request, db)
        csrf(request, token)
        invoice = get_invoice(db, invoice_id)
        if invoice.status != 'draft':
            raise HTTPException(409, 'Only drafts can be edited')
        qty = money(quantity)
        price = money(unit_price)
        if qty <= 0 or qty > 10000:
            raise HTTPException(422, 'Invalid quantity')
        new_total = invoice.total + qty * price
        if new_total > Decimal('999999999999.99'):
            raise HTTPException(422, 'Invoice total exceeds limit')
        db.add(InvoiceLine(invoice_id=invoice.id, title=field(title, 200, True), quantity=qty, unit_price=price))
        invoice.total = new_total
        audit(db, 'invoice.line.create', str(invoice.id), admin.id)
        db.commit()
        return RedirectResponse(f'/invoices/{invoice_id}', status_code=303)


@router.post('/invoices/{invoice_id}/lines/{line_id}/delete')
def invoice_delete_line(request: Request, invoice_id: int, line_id: int, token: str = Form()):
    with request.app.state.session_factory() as db:
        admin = require_admin(request, db); csrf(request, token)
        invoice = get_invoice(db, invoice_id)
        line = db.get(InvoiceLine, line_id)
        if invoice.status != 'draft' or line is None or line.invoice_id != invoice_id:
            raise HTTPException(409, 'Only draft lines can be removed')
        invoice.total -= line.quantity * line.unit_price
        db.delete(line)
        audit(db, 'invoice.line.delete', str(invoice.id), admin.id)
        db.commit()
        return RedirectResponse(f'/invoices/{invoice_id}', status_code=303)


@router.post('/invoices/{invoice_id}/issue')
def invoice_issue(request: Request, invoice_id: int, token: str = Form()):
    with request.app.state.session_factory() as db:
        admin = require_admin(request, db); csrf(request, token)
        invoice = get_invoice(db, invoice_id)
        if invoice.status != 'draft' or invoice.total <= 0 or not invoice.lines:
            raise HTTPException(409, 'Add priced items before issuing the invoice')
        invoice.status = 'issued'
        invoice.issue_at = datetime.now(timezone.utc)
        audit(db, 'invoice.issue', invoice.number, admin.id)
        db.commit()
        return RedirectResponse(f'/invoices/{invoice_id}', status_code=303)


@router.post('/invoices/{invoice_id}/settle')
def invoice_settle(request: Request, invoice_id: int, token: str = Form(), payment_id: int = Form()):
    with request.app.state.session_factory() as db:
        admin = require_admin(request, db); csrf(request, token)
        invoice = get_invoice(db, invoice_id)
        payment = db.get(Payment, payment_id)
        proof = db.scalar(select(PaymentVerification).where(PaymentVerification.payment_id == payment_id,
                                                            PaymentVerification.status == 'verified'))
        already_used = db.scalar(select(Invoice.id).where(Invoice.payment_id == payment_id, Invoice.id != invoice_id))
        if invoice.status != 'issued' or not invoice.customer_id or not payment or not proof or \
           already_used or payment.customer_id != invoice.customer_id or payment.amount != invoice.total or payment.currency != invoice.currency:
            raise HTTPException(409, 'A matching verified payment is required')
        invoice.status, invoice.payment_id = 'paid', payment_id
        audit(db, 'invoice.settle', invoice.number, admin.id, f'payment:{payment_id}')
        db.commit()
        return RedirectResponse(f'/invoices/{invoice_id}', status_code=303)


def render_invoice_pdf(invoice):
    from xml.sax.saxutils import escape
    if 'DejaVuSans' not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont('DejaVuSans', str(FONT)))
    stream = io.BytesIO()
    doc = SimpleDocTemplate(stream, pagesize=A4, leftMargin=48, rightMargin=48, topMargin=44, bottomMargin=44)
    normal = ParagraphStyle('InvoiceNormal', fontName='DejaVuSans', fontSize=10, leading=16, textColor=colors.HexColor('#26314d'))
    small = ParagraphStyle('InvoiceSmall', parent=normal, fontSize=8, leading=12)
    headline = ParagraphStyle('InvoiceHeader', parent=normal, fontSize=22, leading=28, textColor=colors.HexColor('#4e56b7'), spaceAfter=20)
    para = lambda value, style=normal: Paragraph(escape(str(value)).replace('\n', '<br/>'), style)
    items = [para(f'INVOICE / СЧЕТ · {invoice.number}', headline),
             para(f'Date / Дата: {invoice.issue_at.date() if invoice.issue_at else "DRAFT / ЧЕРНОВИК"}'),
             para(f'Due / Срок: {invoice.due_at.date() if invoice.due_at else "—"}'), Spacer(1, 20),
             para('FROM / ОТ', small), para(invoice.seller_name), para(invoice.seller_details, small), Spacer(1, 18),
             para('BILL TO / КОМУ', small), para(invoice.bill_to), para(invoice.bill_to_details, small), Spacer(1, 24)]
    table = [[para('ITEM / УСЛУГА', small), para('QTY', small), para('PRICE', small), para('TOTAL', small)]]
    for line in invoice.lines:
        table.append([para(line.title, small), para(line.quantity, small),
                      para(f'{line.unit_price} {invoice.currency}', small),
                      para(f'{line.quantity * line.unit_price} {invoice.currency}', small)])
    table.append([para('TOTAL / ИТОГО'), '', '', para(f'{invoice.total} {invoice.currency}')])
    box = Table(table, colWidths=[210, 55, 108, 108], repeatRows=1, hAlign='LEFT')
    box.setStyle(TableStyle([('VALIGN', (0,0),(-1,-1),'TOP'),('BACKGROUND',(0,0),(-1,0),colors.HexColor('#eef2ff')),
                             ('LINEBELOW',(0,0),(-1,-1),.3,colors.HexColor('#d9dded')),
                             ('LEFTPADDING',(0,0),(-1,-1),7),('RIGHTPADDING',(0,0),(-1,-1),7),
                             ('TOPPADDING',(0,0),(-1,-1),9),('BOTTOMPADDING',(0,0),(-1,-1),9)]))
    items.extend([box, Spacer(1, 24), para(invoice.notes, small)])
    doc.build(items)
    return stream.getvalue()


@router.get('/invoices/{invoice_id}/download.pdf')
def invoice_pdf(request: Request, invoice_id: int):
    with request.app.state.session_factory() as db:
        require_admin(request, db)
        invoice = get_invoice(db, invoice_id)
        content = render_invoice_pdf(invoice)
        return StreamingResponse(iter([content]), media_type='application/pdf', headers={
            'Content-Disposition': f'attachment; filename="{invoice.number}.pdf"', 'Cache-Control': 'no-store'})
