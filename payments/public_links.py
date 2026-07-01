from django.core import signing


PUBLIC_PAYMENT_RECEIPT_SALT = "payments.public-payment-receipt"
PUBLIC_PAYMENT_RECEIPT_MAX_AGE = 60 * 60 * 24 * 7


def make_public_payment_receipt_token(payment_id):
    return signing.dumps({"payment_id": int(payment_id)}, salt=PUBLIC_PAYMENT_RECEIPT_SALT)


def load_public_payment_receipt_token(token):
    return signing.loads(
        token,
        salt=PUBLIC_PAYMENT_RECEIPT_SALT,
        max_age=PUBLIC_PAYMENT_RECEIPT_MAX_AGE,
    )
