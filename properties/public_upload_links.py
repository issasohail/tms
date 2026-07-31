import secrets

from django.core import signing


UNIT_PHOTO_UPLOAD_SALT = "tms.public-unit-photo-upload.v1"
UNIT_PHOTO_UPLOAD_MAX_AGE = 48 * 60 * 60


def make_unit_photo_upload_token(lease):
    return signing.dumps(
        {
            "lease_id": lease.pk,
            "unit_id": lease.unit_id,
            "nonce": secrets.token_urlsafe(8),
        },
        salt=UNIT_PHOTO_UPLOAD_SALT,
        compress=True,
    )


def read_unit_photo_upload_token(token):
    return signing.loads(
        token,
        salt=UNIT_PHOTO_UPLOAD_SALT,
        max_age=UNIT_PHOTO_UPLOAD_MAX_AGE,
    )
