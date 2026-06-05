from django.core import signing


PUBLIC_INVOICE_SALT = "invoices.public-invoice-detail"
PUBLIC_INVOICE_MAX_AGE = 60 * 60 * 24


def make_public_invoice_token(invoice_id):
    return signing.dumps({"invoice_id": int(invoice_id)}, salt=PUBLIC_INVOICE_SALT)


def load_public_invoice_token(token):
    return signing.loads(
        token,
        salt=PUBLIC_INVOICE_SALT,
        max_age=PUBLIC_INVOICE_MAX_AGE,
    )
