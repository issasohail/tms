from dataclasses import dataclass

from django.conf import settings


@dataclass(frozen=True)
class WhatsAppAIConfig:
    enabled: bool
    provider: str
    model: str
    ocr_provider: str
    use_celery: bool
    openai_api_key_configured: bool
    routing_enabled: bool = False
    generated_responses_enabled: bool = False
    multiple_tools_enabled: bool = True
    handover_enabled: bool = False
    satisfaction_enabled: bool = False
    temperature: float = 0.2
    max_tool_rounds: int = 3
    min_confidence: int = 65
    history_limit: int = 8
    fallback_to_rules: bool = True
    max_reply_length: int = 1200
    enable_urdu: bool = True
    enable_roman_urdu: bool = True
    mask_sensitive_fields: bool = True
    store_logs: bool = True
    staff_reply_prefix: str = "Management:"
    allow_manual_call_action: bool = True
    future_calling_enabled: bool = False
    allow_staff_reply_relay: bool = True
    allow_staff_media_relay: bool = True
    allow_handover_reassignment: bool = True
    return_to_ai_after_close: bool = False
    handover_ai_summary_enabled: bool = True


def get_whatsapp_ai_config():
    defaults = {
        "enabled": getattr(settings, "WHATSAPP_AI_ENABLED", True),
        "provider": getattr(settings, "WHATSAPP_AI_PROVIDER", "rules"),
        "model": getattr(settings, "OPENAI_WHATSAPP_AI_MODEL", "gpt-4o-mini"),
        "ocr_provider": getattr(settings, "WHATSAPP_AI_OCR_PROVIDER", "basic"),
        "use_celery": getattr(settings, "WHATSAPP_AI_USE_CELERY", False),
        "routing_enabled": False,
        "generated_responses_enabled": False,
        "multiple_tools_enabled": True,
        "handover_enabled": False,
        "satisfaction_enabled": False,
        "temperature": 0.2,
        "max_tool_rounds": 3,
        "min_confidence": 65,
        "history_limit": 8,
        "fallback_to_rules": True,
        "max_reply_length": 1200,
        "enable_urdu": True,
        "enable_roman_urdu": True,
        "mask_sensitive_fields": True,
        "store_logs": True,
        "staff_reply_prefix": "Management:",
        "allow_manual_call_action": True,
        "future_calling_enabled": False,
        "allow_staff_reply_relay": True,
        "allow_staff_media_relay": True,
        "allow_handover_reassignment": True,
        "return_to_ai_after_close": False,
        "handover_ai_summary_enabled": True,
    }
    try:
        from core.models import GlobalSettings

        settings_obj = GlobalSettings.get_solo()
        defaults.update(
            {
                "enabled": settings_obj.whatsapp_ai_enabled,
                "provider": settings_obj.whatsapp_ai_provider,
                "model": settings_obj.whatsapp_ai_model,
                "ocr_provider": settings_obj.whatsapp_ai_ocr_provider,
                "use_celery": settings_obj.whatsapp_ai_use_celery,
                "routing_enabled": settings_obj.whatsapp_ai_routing_enabled,
                "generated_responses_enabled": settings_obj.whatsapp_ai_generated_responses_enabled,
                "multiple_tools_enabled": settings_obj.whatsapp_ai_multiple_tools_enabled,
                "handover_enabled": settings_obj.whatsapp_handover_enabled,
                "satisfaction_enabled": settings_obj.whatsapp_ai_satisfaction_enabled,
                "temperature": float(settings_obj.whatsapp_ai_temperature),
                "max_tool_rounds": settings_obj.whatsapp_ai_max_tool_rounds,
                "min_confidence": settings_obj.whatsapp_ai_min_confidence,
                "history_limit": settings_obj.whatsapp_ai_history_limit,
                "fallback_to_rules": settings_obj.whatsapp_ai_fallback_to_rules,
                "max_reply_length": settings_obj.whatsapp_ai_max_reply_length,
                "enable_urdu": settings_obj.whatsapp_ai_enable_urdu,
                "enable_roman_urdu": settings_obj.whatsapp_ai_enable_roman_urdu,
                "mask_sensitive_fields": settings_obj.whatsapp_ai_mask_sensitive_fields,
                "store_logs": settings_obj.whatsapp_ai_store_logs,
                "staff_reply_prefix": settings_obj.whatsapp_staff_reply_prefix,
                "allow_manual_call_action": settings_obj.whatsapp_allow_manual_call_action,
                "future_calling_enabled": settings_obj.whatsapp_future_calling_enabled,
                "allow_staff_reply_relay": settings_obj.whatsapp_allow_staff_reply_relay,
                "allow_staff_media_relay": settings_obj.whatsapp_allow_staff_media_relay,
                "allow_handover_reassignment": settings_obj.whatsapp_allow_handover_reassignment,
                "return_to_ai_after_close": settings_obj.whatsapp_return_to_ai_after_close,
                "handover_ai_summary_enabled": settings_obj.whatsapp_handover_ai_summary_enabled,
            }
        )
    except Exception:
        pass

    return WhatsAppAIConfig(
        enabled=bool(defaults["enabled"]),
        provider=defaults["provider"] or "rules",
        model=defaults["model"] or "gpt-4o-mini",
        ocr_provider=defaults["ocr_provider"] or "basic",
        use_celery=bool(defaults["use_celery"]),
        openai_api_key_configured=bool(getattr(settings, "OPENAI_API_KEY", "")),
        routing_enabled=bool(defaults["routing_enabled"]),
        generated_responses_enabled=bool(defaults["generated_responses_enabled"]),
        multiple_tools_enabled=bool(defaults["multiple_tools_enabled"]),
        handover_enabled=bool(defaults["handover_enabled"]),
        satisfaction_enabled=bool(defaults["satisfaction_enabled"]),
        temperature=float(defaults["temperature"]),
        max_tool_rounds=int(defaults["max_tool_rounds"]),
        min_confidence=int(defaults["min_confidence"]),
        history_limit=int(defaults["history_limit"]),
        fallback_to_rules=bool(defaults["fallback_to_rules"]),
        max_reply_length=int(defaults["max_reply_length"]),
        enable_urdu=bool(defaults["enable_urdu"]),
        enable_roman_urdu=bool(defaults["enable_roman_urdu"]),
        mask_sensitive_fields=bool(defaults["mask_sensitive_fields"]),
        store_logs=bool(defaults["store_logs"]),
        staff_reply_prefix=str(defaults["staff_reply_prefix"] or "Management:"),
        allow_manual_call_action=bool(defaults["allow_manual_call_action"]),
        future_calling_enabled=bool(defaults["future_calling_enabled"]),
        allow_staff_reply_relay=bool(defaults["allow_staff_reply_relay"]),
        allow_staff_media_relay=bool(defaults["allow_staff_media_relay"]),
        allow_handover_reassignment=bool(defaults["allow_handover_reassignment"]),
        return_to_ai_after_close=bool(defaults["return_to_ai_after_close"]),
        handover_ai_summary_enabled=bool(defaults["handover_ai_summary_enabled"]),
    )
