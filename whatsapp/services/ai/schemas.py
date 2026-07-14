from dataclasses import dataclass, field


@dataclass
class ToolCall:
    name: str
    arguments: dict = field(default_factory=dict)


@dataclass
class AIDecision:
    intents: list = field(default_factory=list)
    tool_calls: list = field(default_factory=list)
    confidence: int = 0
    language: str = "en"
    handover: bool = False
    handover_reason: str = ""
    department: str = "general"
    priority: str = "normal"
    follow_up_question: str = ""

    @classmethod
    def from_dict(cls, data):
        calls = []
        for item in (data or {}).get("tool_calls") or []:
            if isinstance(item, dict) and item.get("name"):
                calls.append(ToolCall(str(item["name"]), item.get("arguments") or {}))
        return cls(
            intents=[str(item) for item in ((data or {}).get("intents") or [])][:6],
            tool_calls=calls[:6],
            confidence=max(0, min(100, int((data or {}).get("confidence") or 0))),
            language=str((data or {}).get("language") or "en")[:20],
            handover=bool((data or {}).get("handover")),
            handover_reason=str((data or {}).get("handover_reason") or "")[:160],
            department=str((data or {}).get("department") or "general")[:30],
            priority=str((data or {}).get("priority") or "normal")[:20],
            follow_up_question=str((data or {}).get("follow_up_question") or "")[:300],
        )
