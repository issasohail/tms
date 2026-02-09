# leases/utils/email_service.py
from django.core.mail import EmailMessage
from django.template.loader import render_to_string
from django.conf import settings


def send_lease_agreement_email(lease, recipient_emails):
    """
    Send generated lease agreement via email
    """
    if not lease.generated_agreement_pdf:
        return False

    context = {
        'tenant_name': lease.tenant.get_full_name(),
        'property_name': lease.unit.property.property_name,
        'unit_number': lease.unit.unit_number,
        'generation_date': lease.generated_date.strftime('%d-%m-%Y'),
    }

    email_subject = f"Lease Agreement for {lease.unit.property.property_name} - Unit {lease.unit.unit_number}"
    email_body = render_to_string('leases/email/agreement_email.txt', context)

    email = EmailMessage(
        email_subject,
        email_body,
        settings.DEFAULT_FROM_EMAIL,
        recipient_emails,
    )

    email.attach_file(lease.generated_agreement_pdf.path)

    try:
        email.send()
        return True
    except Exception as e:
        print(f"Error sending email: {str(e)}")
        return False
