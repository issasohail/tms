

import smtplib
from email.mime.text import MIMEText
from django.conf import settings


def test_smtp_connection():
    sender = settings.EMAIL_HOST_USER
    password = settings.EMAIL_HOST_PASSWORD
    recipient = "your.email@gmail.com"  # Replace with your email

    msg = MIMEText('This is a test email from Django SMTP')
    msg['Subject'] = 'SMTP Connection Test'
    msg['From'] = sender
    msg['To'] = recipient

    try:
        with smtplib.SMTP(settings.EMAIL_HOST, settings.EMAIL_PORT) as server:
            server.starttls()
            server.login(sender, password)
            server.sendmail(sender, [recipient], msg.as_string())
        print("✅ Email sent successfully!")
    except Exception as e:
        print(f"❌ Failed to send email: {e}")


if __name__ == "__main__":
    test_smtp_connection()
