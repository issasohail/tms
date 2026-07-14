import json
import logging
from dataclasses import dataclass, field

from django.conf import settings

from whatsapp.services.handover.lifecycle import create_handover
from whatsapp.services.handover.notifications import notify_new_handover
from whatsapp.services.handover.workflow import detect_handover_request

from .context_builder import build_safe_context
from .prompts import ROUTER_PROMPT
from .response_generator import format_verified_results
from .safety import safe_summary
from .schemas import AIDecision, ToolCall
from .tool_registry import TOOL_HANDLERS, ToolContext, execute_tool

logger = logging.getLogger(__name__)


@dataclass
class AIOrchestrationResult:
    handled: bool = False
    reply: str = ""
    intent: str = "general"
    metadata: dict = field(default_factory=dict)


class WhatsAppAIOrchestrator:
    def __init__(self, config, service=None):
        self.config = config
        self.service = service

    def handle(self, text, sender, conversation, message_log, lease=None):
        if not self.config.routing_enabled:
            return AIOrchestrationResult()
        safe_context = build_safe_context(sender, conversation, lease=lease, history_limit=self.config.history_limit)
        decision, fallback_used, provider_error, usage = self._decision(text, safe_context)
        if decision.language:
            conversation.preferred_language = decision.language
        conversation.last_ai_confidence = decision.confidence
        conversation.save(update_fields=["preferred_language", "last_ai_confidence", "updated_at"])

        if decision.handover or decision.confidence < self.config.min_confidence:
            if not self.config.handover_enabled:
                return AIOrchestrationResult(
                    handled=False,
                    intent=decision.intents[0] if decision.intents else "general",
                    metadata=self._metadata(decision, [], fallback_used, provider_error, usage),
                )
            reason = decision.handover_reason or "AI confidence below configured threshold"
            handover, _created = create_handover(
                conversation,
                message_log,
                reason=reason,
                department=decision.department or "general",
                priority=decision.priority or "normal",
                ai_summary=safe_summary(text, 1000) if self.config.handover_ai_summary_enabled else "",
            )
            notify_new_handover(handover, service=self.service)
            return AIOrchestrationResult(
                handled=True,
                reply=f"Your message has been sent to management. Reference: {handover.reference}. Staff will decide whether to reply or call you.",
                intent="handover",
                metadata=self._metadata(decision, [], fallback_used, provider_error, usage, handover),
            )

        calls = decision.tool_calls if self.config.multiple_tools_enabled else decision.tool_calls[:1]
        context = ToolContext(sender=sender, conversation=conversation, message_log=message_log, lease=lease)
        results = []
        for call in calls[: self.config.max_tool_rounds]:
            results.append({"name": call.name, "result": execute_tool(call.name, call.arguments, context)})
        successful = [item for item in results if item["result"].get("ok")]
        if not successful:
            return AIOrchestrationResult(
                handled=False,
                intent=decision.intents[0] if decision.intents else "general",
                metadata=self._metadata(decision, results, fallback_used, provider_error, usage),
            )
        reply = format_verified_results(
            results,
            language=decision.language,
            follow_up=decision.follow_up_question,
            max_length=self.config.max_reply_length,
        )
        if reply and self.config.generated_responses_enabled and self.config.provider == "openai" and self.config.openai_api_key_configured:
            reply, generated_usage = self._generate_reply(reply, decision.language)
            usage["prompt_tokens"] = usage.get("prompt_tokens", 0) + generated_usage.get("prompt_tokens", 0)
            usage["completion_tokens"] = usage.get("completion_tokens", 0) + generated_usage.get("completion_tokens", 0)
        if reply and self.config.satisfaction_enabled and decision.intents and decision.intents[0] in {"balance", "maintenance_status", "maintenance"}:
            reply = f"{reply}\n\n{_satisfaction_text(decision.language)}"
        return AIOrchestrationResult(
            handled=bool(reply),
            reply=reply,
            intent="+".join(decision.intents)[:80] or "ai_tools",
            metadata=self._metadata(decision, results, fallback_used, provider_error, usage),
        )

    def _decision(self, text, context):
        provider_error = ""
        usage = {}
        if self.config.provider == "openai" and self.config.openai_api_key_configured:
            try:
                decision, usage = self._openai_decision(text, context)
                return decision, False, "", usage
            except Exception as exc:
                provider_error = str(exc)
                logger.exception("WhatsApp AI router failed; using deterministic fallback.")
                if not self.config.fallback_to_rules:
                    return AIDecision(confidence=0, handover=True, handover_reason="AI provider unavailable"), True, provider_error, usage
        return fallback_decision(text), True, provider_error, usage

    def _openai_decision(self, text, context):
        from openai import OpenAI
        prompt = (
            f"{ROUTER_PROMPT}\n\nAllowed tools: {', '.join(sorted(TOOL_HANDLERS))}\n"
            f"Safe server context: {json.dumps(context, default=str)}\n"
            f"Message: {safe_summary(text, 1200)}"
        )
        response = OpenAI(api_key=settings.OPENAI_API_KEY).responses.create(
            model=self.config.model,
            temperature=self.config.temperature,
            input=[{"role": "user", "content": [{"type": "input_text", "text": prompt}]}],
        )
        raw = getattr(response, "output_text", "") or "{}"
        data = _parse_json(raw)
        decision = AIDecision.from_dict(data)
        decision.tool_calls = [call for call in decision.tool_calls if call.name in TOOL_HANDLERS]
        usage_obj = getattr(response, "usage", None)
        usage = {
            "prompt_tokens": int(getattr(usage_obj, "input_tokens", 0) or 0),
            "completion_tokens": int(getattr(usage_obj, "output_tokens", 0) or 0),
        }
        return decision, usage

    def _generate_reply(self, verified_reply, language):
        """Optionally rewrite verified output; failure always preserves deterministic output."""
        try:
            from openai import OpenAI
            response = OpenAI(api_key=settings.OPENAI_API_KEY).responses.create(
                model=self.config.model,
                input=(
                    "Rewrite the following verified TMS result as a concise WhatsApp reply in "
                    f"language code {language or 'en'}. Preserve every amount, date, status and reference exactly. "
                    "Do not add facts, names, promises, URLs or calculations.\n\n"
                    f"VERIFIED RESULT:\n{verified_reply}"
                ),
            )
            generated = safe_summary(getattr(response, "output_text", ""), self.config.max_reply_length)
            usage_obj = getattr(response, "usage", None)
            usage = {
                "prompt_tokens": int(getattr(usage_obj, "input_tokens", 0) or 0),
                "completion_tokens": int(getattr(usage_obj, "output_tokens", 0) or 0),
            }
            return (generated or verified_reply), usage
        except Exception:
            logger.exception("WhatsApp natural response generation failed; using verified deterministic reply.")
            return verified_reply, {}

    def _metadata(self, decision, results, fallback_used, provider_error, usage, handover=None):
        return {
            "ai_decision": {
                "intents": decision.intents,
                "confidence": decision.confidence,
                "language": decision.language,
                "handover": decision.handover,
                "handover_reason": safe_summary(decision.handover_reason, 300),
                "department": decision.department,
                "priority": decision.priority,
            },
            "tool_calls": [{"name": item.name, "arguments": item.arguments} for item in decision.tool_calls],
            "tool_results": results,
            "fallback_used": fallback_used,
            "provider_error": provider_error,
            "usage": usage,
            "handover_id": handover.pk if handover else None,
            "handover_reference": handover.reference if handover else "",
        }


def fallback_decision(text):
    lowered = (text or "").strip().lower()
    handover = detect_handover_request(text)
    if handover:
        reason, department, priority = handover
        return AIDecision(intents=["handover"], confidence=100, language=detect_language(text), handover=True, handover_reason=reason, department=department, priority=priority)
    calls = []
    intents = []
    mappings = [
        (("balance", "remaining", "due", "baqaya"), "balance", "get_tenant_balance"),
        (("last payment", "latest payment", "akhri payment"), "last_payment", "get_last_payment"),
        (("payment history", "recent payments"), "payments", "get_payment_history"),
        (("latest invoice", "last invoice"), "latest_invoice", "get_latest_invoice"),
        (("ledger", "statement"), "ledger", "get_ledger_link"),
        (("lease expiry", "lease end", "lease kab"), "lease_expiry", "get_lease_expiry"),
        (("family member", "family members"), "family", "get_family_members"),
        (("maintenance status", "complaint status"), "maintenance_status", "get_maintenance_status"),
    ]
    for phrases, intent, tool in mappings:
        if any(phrase in lowered for phrase in phrases):
            intents.append(intent)
            calls.append(ToolCall(tool, {}))
    maintenance_words = ("leak", "leaking", "pani", "bathroom", "plumbing", "broken", "kharab", "repair")
    if any(word in lowered for word in maintenance_words) and "maintenance_status" not in intents:
        intents.append("maintenance")
        calls.append(ToolCall("create_maintenance_draft", {"description": text, "issue_type": "Water Leakage" if any(word in lowered for word in ("leak", "pani")) else "Other", "location": "Bathroom" if "bathroom" in lowered else "", "urgency": "urgent" if any(word in lowered for word in ("urgent", "fire", "flood")) else "normal"}))
    return AIDecision(intents=intents or ["general"], tool_calls=calls, confidence=90 if calls else 45, language=detect_language(text))


def detect_language(text):
    value = text or ""
    if any("\u0600" <= char <= "\u06ff" for char in value):
        return "ur"
    lowered = value.lower()
    if any(word in lowered.split() for word in ("mera", "meri", "mujhe", "bata", "pani", "kharab", "karain", "hain")):
        return "roman_urdu"
    return "en"


def _satisfaction_text(language):
    if language == "ur":
        return "کیا یہ مددگار تھا؟\n1. ہاں\n2. اسٹاف سے بات کریں"
    if language == "roman_urdu":
        return "Kya yeh madadgar tha?\n1. Haan\n2. Staff se baat karein"
    return "Was this helpful?\n1. Yes\n2. Talk to Staff"


def _parse_json(raw):
    value = (raw or "").strip()
    if value.startswith("```"):
        value = value.strip("`")
        if value.lower().startswith("json"):
            value = value[4:].strip()
    return json.loads(value)
