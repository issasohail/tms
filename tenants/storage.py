import os

from django.conf import settings
from django.core.files.storage import FileSystemStorage
from django.utils.deconstruct import deconstructible


@deconstructible
class TenantRegistrationDraftStorage(FileSystemStorage):
    """Filesystem storage that deliberately has no public URL."""

    def __init__(self):
        location = getattr(
            settings,
            "TENANT_REGISTRATION_DRAFT_ROOT",
            os.path.join(settings.BASE_DIR, "private_uploads", "tenant_registration_drafts"),
        )
        super().__init__(
            location=os.path.abspath(location),
            base_url=None,
            file_permissions_mode=0o600,
            directory_permissions_mode=0o700,
        )

    def url(self, name):
        raise ValueError("Temporary registration documents are private.")


tenant_registration_draft_storage = TenantRegistrationDraftStorage()
