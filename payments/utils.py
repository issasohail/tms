from django.core.mail import EmailMessage
from django.template.loader import render_to_string
from twilio.rest import Client
import os
from io import BytesIO
from django.http import HttpResponse
from xhtml2pdf import pisa
from django.contrib.contenttypes.models import ContentType


def render_to_pdf(template_name, context={}):
    """
    Renders an HTML template into a PDF file.
    """
    full_template_path = f'payments/{template_name}'
    html = render_to_string(full_template_path, context)
    result = BytesIO()

    # Convert HTML to PDF
    pdf = pisa.pisaDocument(BytesIO(html.encode("UTF-8")), result)

    if not pdf.err:
        return result.getvalue()
    return None


def send_ledger(tenant, method='email'):
    transactions = TenantLedgerView().get_queryset(tenant.pk)

    if method == 'email':
        msg = render_to_string('payments/ledger_email.html', {
            'tenant': tenant,
            'transactions': transactions
        })

        email = EmailMessage(
            f'Rent Ledger for {tenant}',
            msg,
            to=[tenant.email]
        )
        email.send()

    elif method == 'whatsapp':
        client = Client(os.getenv('TWILIO_SID'), os.getenv('TWILIO_TOKEN'))

        message = f"*Rent Ledger for {tenant}*\n\n"
        for t in transactions:
            message += f"{t['date']}: {t['type']} {t['amount']} (Balance: {t['balance']})\n"

        client.messages.create(
            body=message,
            from_='whatsapp:+14155238886',
            to=f'whatsapp:{tenant.phone}'
        )


def model_type(obj):
    return ContentType.objects.get_for_model(obj).model
