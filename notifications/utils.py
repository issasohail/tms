from django.conf import settings
from django.core.mail import send_mail
from django.template.loader import render_to_string
from django.utils.html import strip_tags
from twilio.rest import Client
from .models import Notification  # Assuming this is the correct model name
from django.utils import timezone
import os
from leases.models import Lease
from django.utils import timezone
from django.utils.html import strip_tags
from leases.models import Lease


def send_email_notification(subject, message, recipient_list, tenant=None):
    try:
        html_message = render_to_string('notifications/email_template.html', {
            'message': message,
            'tenant': tenant,
        })
        plain_message = strip_tags(html_message)

        send_mail(
            subject=subject,
            message=plain_message,
            html_message=html_message,
            from_email=settings.EMAIL_HOST_USER,
            recipient_list=recipient_list,
            fail_silently=False,
        )

        # Log the notification - changed from NotificationLog to Notification
        Notification.objects.create(
            tenant=tenant,
            notification_type='email',
            subject=subject,
            message=message,
            status='sent'
        )
        return True
    except Exception as e:
        Notification.objects.create(
            tenant=tenant,
            notification_type='email',
            subject=subject,
            message=message,
            status='failed',
            error_message=str(e)
        )
        return False


def send_sms_notification(message, to_phone, tenant=None):
    try:
        if not all([settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN, settings.TWILIO_PHONE_NUMBER]):
            raise ValueError("Twilio credentials not configured")

        client = Client(settings.TWILIO_ACCOUNT_SID,
                        settings.TWILIO_AUTH_TOKEN)

        message = client.messages.create(
            body=message,
            from_=settings.TWILIO_PHONE_NUMBER,
            to=to_phone
        )

        # Log the notification - changed from NotificationLog to Notification
        Notification.objects.create(
            tenant=tenant,
            notification_type='sms',
            message=message.body,  # Changed to access the body of the message
            status='sent'
        )
        return True
    except Exception as e:
        Notification.objects.create(
            tenant=tenant,  # Fixed from payment.lease.tenant to tenant
            notification_type='sms',
            message=message,
            status='failed',
            error_message=str(e)
        )
        return False


def send_payment_receipt(payment):
    try:
        lease = payment.lease
        tenant = lease.tenant
        subject = f"Payment Receipt - {payment.payment_date.strftime('%Y-%m-%d')}"

        message = f"""
        Dear {tenant.first_name} {tenant.last_name},
        
        Thank you for your payment of {payment.amount} on {payment.payment_date.strftime('%Y-%m-%d')}.
        Payment Method: {payment.get_payment_method_display()}
        Reference: {payment.reference_number or 'N/A'}
        
        Your current balance is: {lease.get_balance}  # Removed parentheses as it's a property
        
        Please keep this receipt for your records.
        """

        # Send email if tenant has email
        email_sent = False
        if tenant.email:
            email_sent = send_email_notification(
                subject, message, [tenant.email], tenant)

        # Send SMS if tenant has phone
        sms_sent = False
        if tenant.phone:
            sms_sent = send_sms_notification(
                strip_tags(message), tenant.phone, tenant)

        # Update payment receipt status
        if email_sent or sms_sent:
            payment.receipt_sent = True
            payment.save()

        return email_sent or sms_sent
    except Exception as e:
        print(f"Error sending payment receipt: {str(e)}")
        return False


def send_balance_notification(tenant):
    try:
        # Get the active lease for the tenant
        lease = tenant.leases.filter(
            status='active').order_by('-start_date').first()
        if not lease:
            return False

        subject = f"Rent Balance Reminder - {timezone.now().strftime('%Y-%m-%d')}"
        message = f"""
        Dear {tenant.first_name} {tenant.last_name},
        
        This is a reminder that your current balance is: {lease.get_balance}  # Removed parentheses
        
        Please make your payment at your earliest convenience to avoid late fees.
        
        Thank you,
        Property Management
        """

        # Send email if tenant has email
        email_sent = False
        if tenant.email:
            email_sent = send_email_notification(
                subject, message, [tenant.email], tenant)

        # Send SMS if tenant has phone
        sms_sent = False
        if tenant.phone:
            sms_sent = send_sms_notification(
                strip_tags(message), tenant.phone, tenant)

        return email_sent or sms_sent
    except Exception as e:
        print(f"Error sending balance notification: {str(e)}")
        return False
    return email_sent or sms_sent


def send_balance_notification(tenant):
    # Get the active lease for the tenant
    active_lease = tenant.leases.filter(status='active').first()
    if not active_lease:
        return False  # No active lease to send notification for

    subject = f"Rent Balance Reminder - {timezone.now().strftime('%Y-%m-%d')}"

    # Use lease.get_balance() for the balance calculation
    message = f"""
    Dear {tenant.first_name} {tenant.last_name},
    
    This is a reminder that your current balance is: {active_lease.get_balance()}
    
    Please make your payment at your earliest convenience to avoid late fees.
    
    Thank you,
    Property Management
    """

    # Rest of the function remains the same
    email_sent = False
    if tenant.email:
        email_sent = send_email_notification(
            subject, message, [tenant.email], tenant)

    sms_sent = False
    if tenant.phone:
        sms_sent = send_sms_notification(
            strip_tags(message), tenant.phone, tenant)

    return email_sent or sms_sent


def send_balance_notification(tenant):
    subject = f"Rent Balance Reminder - {timezone.now().strftime('%Y-%m-%d')}"
    message = f"""
    Dear {tenant.first_name} {tenant.last_name},
    
    This is a reminder that your current balance is: {tenant.current_balance}
    
    Please make your payment at your earliest convenience to avoid late fees.
    
    Thank you,
    Property Management
    """

    # Send email if tenant has email
    email_sent = False
    if tenant.email:
        email_sent = send_email_notification(
            subject, message, [tenant.email], tenant)

    # Send SMS if tenant has phone
    sms_sent = False
    if tenant.phone:
        sms_sent = send_sms_notification(
            strip_tags(message), tenant.phone, tenant)

    return email_sent or sms_sent
