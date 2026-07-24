from whatsapp.models import PendingWhatsAppMedia
from whatsapp.services.media_processor import create_pending_media

from .models import HandymanJobAttachment, HandymanProfile, MaintenanceHandymanAssignment


def handle_handyman_whatsapp_message(message_log, conversation, text, message_type, identity):
    lowered = (text or "").strip().lower()
    config = _handyman_settings()
    profile_commands = _profile_commands(config)
    if config.handyman_enable_whatsapp_profile_updates and lowered in profile_commands:
        if not (identity.handyman or _handyman_for_phone(message_log.phone_number)):
            return None
        conversation.pending_state = "handyman_profile_upload"
        conversation.context["handyman_profile_field"] = profile_commands[lowered]
        conversation.save(update_fields=["pending_state", "context", "updated_at"])
        return "Please send the image now.", "handyman_profile_upload_prompt", {}
    job_commands = _job_commands(config)
    command, requested_job_id = _parse_job_command(lowered, job_commands)
    if config.handyman_enable_whatsapp_job_uploads and command:
        handyman = identity.handyman or _handyman_for_phone(message_log.phone_number)
        assignments = _active_assignments(handyman)
        if not assignments:
            return None
        if requested_job_id:
            assignment = next(
                (
                    item
                    for item in assignments
                    if item.maintenance_request_id == requested_job_id
                ),
                None,
            )
            if not assignment:
                return (
                    f"Job #{requested_job_id} is not one of your active requests. "
                    + _job_selection_text(assignments, command),
                    "handyman_job_upload_invalid_job",
                    {},
                )
        elif len(assignments) > 1:
            return (
                _job_selection_text(assignments, command),
                "handyman_job_upload_select_job",
                {"assignment_ids": [item.pk for item in assignments]},
            )
        else:
            assignment = assignments[0]
        conversation.pending_state = "handyman_job_upload"
        conversation.context["handyman_attachment_type"] = job_commands[command]
        conversation.context["handyman_assignment_id"] = assignment.pk
        conversation.save(update_fields=["pending_state", "context", "updated_at"])
        return (
            f"Please send the file now for Job #{assignment.maintenance_request_id} "
            f"({assignment.maintenance_request.title}).",
            "handyman_job_upload_prompt",
            {"assignment_id": assignment.pk},
        )
    return None


def handle_handyman_media_message(message_log, conversation, text, message_type, identity):
    if conversation.pending_state == "handyman_profile_upload":
        if not _handyman_settings().handyman_enable_whatsapp_profile_updates:
            return None
        field_name = conversation.context.get("handyman_profile_field")
        if field_name not in {"photo", "id_card_front", "id_card_back"}:
            return None
        handyman = identity.handyman or _handyman_for_phone(message_log.phone_number)
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
        if not _handyman_settings().handyman_enable_whatsapp_job_uploads:
            return None
        assignment_id = conversation.context.get("handyman_assignment_id")
        attachment_type = conversation.context.get("handyman_attachment_type")
        handyman = identity.handyman or _handyman_for_phone(message_log.phone_number)
        assignment = (
            MaintenanceHandymanAssignment.objects.select_related(
                "maintenance_request", "handyman"
            )
            .filter(
                pk=assignment_id,
                handyman=handyman,
                is_current=True,
                status__in=["assigned", "accepted", "in_progress"],
            )
            .first()
        )
        if not assignment:
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
        return (
            f"Attached to Job #{assignment.maintenance_request_id}. Thank you.",
            "handyman_job_upload_saved",
            {"assignment_id": assignment.pk},
        )
    return None


def _handyman_for_phone(phone_number):
    digits = _digits(phone_number)
    if not digits:
        return None
    for handyman in HandymanProfile.objects.filter(is_active=True):
        if _phone_matches(digits, handyman.whatsapp_number or handyman.phone):
            return handyman
    return None


def _active_assignments(handyman):
    if not handyman:
        return []
    return list(
        handyman.assignments.select_related("maintenance_request", "handyman")
        .filter(is_current=True, status__in=["assigned", "accepted", "in_progress"])
        .order_by("-assigned_at", "-id")
    )


def _parse_job_command(lowered, job_commands):
    parts = (lowered or "").split()
    if not parts or parts[0] not in job_commands:
        return "", None
    if len(parts) == 1:
        return parts[0], None
    job_value = parts[1].lstrip("#")
    if job_value.isdigit():
        return parts[0], int(job_value)
    return "", None


def _job_selection_text(assignments, command):
    command_label = command.upper()
    jobs = "\n".join(
        f"- Job #{item.maintenance_request_id}: {item.maintenance_request.title}"
        for item in assignments
    )
    return (
        "You have multiple active maintenance requests. Choose the correct job:\n"
        f"{jobs}\n"
        f"Reply {command_label} followed by the job number, for example: "
        f"{command_label} {assignments[0].maintenance_request_id}"
    )


def _digits(value):
    return "".join(ch for ch in str(value or "") if ch.isdigit())


def _phone_matches(target_digits, candidate):
    candidate_digits = _digits(candidate)
    return bool(candidate_digits and (candidate_digits.endswith(target_digits[-10:]) or target_digits.endswith(candidate_digits[-10:])))


def _handyman_settings():
    from core.models import GlobalSettings

    return GlobalSettings.get_solo()


def _profile_commands(config):
    commands = {
        _normalized_command(config.handyman_profile_photo_command): "photo",
        _normalized_command(config.handyman_id_front_command): "id_card_front",
        _normalized_command(config.handyman_id_back_command): "id_card_back",
    }
    return {command: field for command, field in commands.items() if command}


def _job_commands(config):
    commands = {
        _normalized_command(config.handyman_invoice_command): "invoice",
        _normalized_command(config.handyman_job_photo_command): "job_photo",
    }
    return {command: attachment_type for command, attachment_type in commands.items() if command}


def _normalized_command(value):
    return str(value or "").strip().lower()
