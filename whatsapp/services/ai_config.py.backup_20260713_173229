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


def get_whatsapp_ai_config():
    defaults = {
        "enabled": getattr(settings, "WHATSAPP_AI_ENABLED", True),
        "provider": getattr(settings, "WHATSAPP_AI_PROVIDER", "rules"),
        "model": getattr(settings, "OPENAI_WHATSAPP_AI_MODEL", "gpt-4o-mini"),
        "ocr_provider": getattr(settings, "WHATSAPP_AI_OCR_PROVIDER", "basic"),
        "use_celery": getattr(settings, "WHATSAPP_AI_USE_CELERY", False),
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
    )
