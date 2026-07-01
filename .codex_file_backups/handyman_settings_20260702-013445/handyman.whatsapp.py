from whatsapp.models import PendingWhatsAppMedia
from whatsapp.services.media_processor import create_pending_media

from .models import HandymanJobAttachment, HandymanProfile
from .services import active_assignment_for_handyman_phone


COMMAND_TO_FIELD = {
    "profile photo": "photo",
    "id front": "id_card_front",
    "id back": "id_card_back",
}


def handle_handyman_whatsapp_message(message_log, conversation, text, message_type, identity):
    lowered = (text or "").strip().lower()
    if lowered in COMMAND_TO_FIELD:
        if not _handyman_for_phone(message_log.phone_number):
            return None
        conversation.pending_state = "handyman_profile_upload"
        conversation.context["handyman_profile_field"] = COMMAND_TO_FIELD[lowered]
        conversation.save(update_fields=["pending_state", "context", "updated_at"])
        return "Please send the image now.", "handyman_profile_upload_prompt", {}
    if lowered in {"invoice", "photo"}:
        assignment = active_assignment_for_handyman_phone(message_log.phone_number)
        if not assignment:
            return None
        conversation.pending_state = "handyman_job_upload"
        conversation.context["handyman_attachment_type"] = "invoice" if lowered == "invoice" else "job_photo"
        conversation.context["handyman_assignment_id"] = assignment.pk
        conversation.save(update_fields=["pending_state", "context", "updated_at"])
        return "Please send the file now.", "handyman_job_upload_prompt", {"assignment_id": assignment.pk}
    return None


def handle_handyman_media_message(message_log, conversation, text, message_type, identity):
    if conversation.pending_state == "handyman_profile_upload":
        field_name = conversation.context.get("handyman_profile_field")
        if field_name not in {"photo", "id_card_front", "id_card_back"}:
            return None
        handyman = _handyman_for_phone(message_log.phone_number)
        if not handyman:
            return None
        media = create_pending_media(message_log, conversation)
        if media.file:
            getattr(handyman, field_name).save(media.original_filename or media.file.name, media.file, save=True)
            media.status = PendingWhatsAppMedia.STATUS_APPROVED
            media.purpose = PendingWhatsAppMedia.PURPOSE_OTHER
            media.save(update_fields=["status", "purpose", "updated_at"])
        conversation.pending_state = ""
        conversation.context.pop("handyman_profile_field", None)
        conversation.save(update_fields=["pending_state", "context", "updated_at"])
        return "Updated. Thank you.", "handyman_profile_upload_saved", {"handyman_id": handyman.pk}

    if conversation.pending_state == "handyman_job_upload":
        assignment_id = conversation.context.get("handyman_assignment_id")
        attachment_type = conversation.context.get("handyman_attachment_type")
        assignment = active_assignment_for_handyman_phone(message_log.phone_number)
        if not assignment or assignment.pk != assignment_id:
            return None
        media = create_pending_media(message_log, conversation, getattr(assignment.maintenance_request, "lease", None))
        HandymanJobAttachment.objects.create(
            assignment=assignment,
            file=media.file,
            attachment_type=attachment_type if attachment_type in {"invoice", "job_photo"} else "job_photo",
            original_filename=media.original_filename,
            source="whatsapp",
            whatsapp_media_id=media.whatsapp_media_id,
        )
        media.status = PendingWhatsAppMedia.STATUS_APPROVED
        media.purpose = PendingWhatsAppMedia.PURPOSE_MAINTENANCE
        media.save(update_fields=["status", "purpose", "updated_at"])
        conversation.pending_state = ""
        conversation.context.pop("handyman_assignment_id", None)
        conversation.context.pop("handyman_attachment_type", None)
        conversation.save(update_fields=["pending_state", "context", "updated_at"])
        return "Attached to your active maintenance job. Thank you.", "handyman_job_upload_saved", {"assignment_id": assignment.pk}
    return None


def _handyman_for_phone(phone_number):
    digits = _digits(phone_number)
    if not digits:
        return None
    for handyman in HandymanProfile.objects.filter(is_active=True):
        if _phone_matches(digits, handyman.whatsapp_number or handyman.phone):
            return handyman
    return None


def _digits(value):
    return "".join(ch for ch in str(value or "") if ch.isdigit())


def _phone_matches(target_digits, candidate):
    candidate_digits = _digits(candidate)
    return bool(candidate_digits and (candidate_digits.endswith(target_digits[-10:]) or target_digits.endswith(candidate_digits[-10:])))
