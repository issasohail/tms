from dataclasses import dataclass
from invoices.models import Invoice
from payments.models import Payment
from whatsapp.services.maintenance_ai import create_pending_maintenance
from whatsapp.services.tenant_context import build_lease_context

from .safety import sanitize_tool_arguments


@dataclass
class ToolContext:
    sender: object
    conversation: object
    message_log: object
    lease: object = None


def _require_lease(context):
    if not context.lease:
        return {"ok": False, "error": "No uniquely selected active lease."}
    return None


def get_tenant_balance(context, arguments):
    error = _require_lease(context)
    if error:
        return error
    data = build_lease_context(context.lease)
    return {"ok": True, "balance": str(data.balance), "currency": "PKR", "property": str(data.property), "unit": str(data.unit)}


def get_last_payment(context, arguments):
    error = _require_lease(context)
    if error:
        return error
    payment = Payment.objects.filter(lease=context.lease).order_by("-payment_date", "-id").first()
    if not payment:
        return {"ok": True, "payment": None}
    return {"ok": True, "payment": {"date": str(payment.payment_date), "amount": str(payment.amount), "reference": payment.reference_number or ""}}


def get_payment_history(context, arguments):
    error = _require_lease(context)
    if error:
        return error
    rows = Payment.objects.filter(lease=context.lease).order_by("-payment_date", "-id")[:5]
    return {"ok": True, "payments": [{"date": str(item.payment_date), "amount": str(item.amount), "reference": item.reference_number or ""} for item in rows]}


def get_latest_invoice(context, arguments):
    error = _require_lease(context)
    if error:
        return error
    invoice = Invoice.objects.filter(lease=context.lease).order_by("-issue_date", "-id").first()
    return {"ok": True, "invoice": None if not invoice else {"number": getattr(invoice, "invoice_number", "") or str(invoice.pk), "issue_date": str(invoice.issue_date), "due_date": str(invoice.due_date), "amount": str(invoice.amount)}}


def get_invoice_link(context, arguments):
    # The legacy workflow creates a time-limited public link. Never expose a
    # login-only/internal path from an AI-selected tool.
    return _defer_to_existing_workflow(context, arguments)


def get_ledger_link(context, arguments):
    return _defer_to_existing_workflow(context, arguments)


def get_active_lease(context, arguments):
    error = _require_lease(context)
    if error:
        return error
    lease = context.lease
    return {"ok": True, "property": str(lease.unit.property), "unit": str(lease.unit), "start": str(lease.start_date), "end": str(lease.end_date), "rent": str(lease.monthly_rent)}


def get_lease_expiry(context, arguments):
    error = _require_lease(context)
    return error or {"ok": True, "end_date": str(context.lease.end_date)}


def get_family_members(context, arguments):
    error = _require_lease(context)
    if error:
        return error
    rows = context.lease.family_members.select_related("family_member", "relationship_type").all()
    return {"ok": True, "family_members": [{"name": item.family_member.get_full_name(), "relationship": str(item.relationship_type or "")} for item in rows]}


def get_maintenance_status(context, arguments):
    error = _require_lease(context)
    if error:
        return error
    rows = context.lease.maintenance_requests.order_by("-reported_date", "-id")[:5]
    return {"ok": True, "requests": [{"title": item.title, "status": item.get_status_display(), "reported": str(item.reported_date)} for item in rows]}


def create_maintenance_draft(context, arguments):
    error = _require_lease(context)
    if error:
        return error
    pending = create_pending_maintenance(context.message_log, context.conversation, context.lease, extracted=arguments)
    return {"ok": True, "draft_reference": f"MAINT-{pending.pk}", "status": "pending approval"}


def get_vehicle_information(context, arguments):
    error = _require_lease(context)
    if error:
        return error
    rows = context.lease.vehicles.all()[:10] if hasattr(context.lease, "vehicles") else []
    return {"ok": True, "vehicles": [str(item) for item in rows]}


def _defer_to_existing_workflow(context, arguments):
    return {"ok": False, "defer_to_existing_workflow": True}


TOOL_HANDLERS = {
    "get_tenant_balance": get_tenant_balance,
    "get_last_payment": get_last_payment,
    "get_payment_history": get_payment_history,
    "get_latest_invoice": get_latest_invoice,
    "get_invoice_link": get_invoice_link,
    "get_ledger_link": get_ledger_link,
    "get_active_lease": get_active_lease,
    "get_lease_expiry": get_lease_expiry,
    "get_family_members": get_family_members,
    "get_vehicle_information": get_vehicle_information,
    "get_meter_information": _defer_to_existing_workflow,
    "get_missing_documents": _defer_to_existing_workflow,
    "get_maintenance_status": get_maintenance_status,
    "create_maintenance_draft": create_maintenance_draft,
    "submit_payment_receipt": _defer_to_existing_workflow,
    "request_lease_renewal": _defer_to_existing_workflow,
    "request_move_out": _defer_to_existing_workflow,
    "get_office_contact": _defer_to_existing_workflow,
    "list_vacant_units": _defer_to_existing_workflow,
    "create_handover": _defer_to_existing_workflow,
    "get_staff_inbox": _defer_to_existing_workflow,
    "get_handover_details": _defer_to_existing_workflow,
    "accept_handover": _defer_to_existing_workflow,
    "send_staff_reply": _defer_to_existing_workflow,
    "mark_tenant_called": _defer_to_existing_workflow,
    "assign_handover": _defer_to_existing_workflow,
    "close_handover": _defer_to_existing_workflow,
    "return_handover_to_ai": _defer_to_existing_workflow,
}


TOOL_ARGUMENTS = {
    "create_maintenance_draft": {"issue_type", "location", "urgency", "description", "requires_follow_up", "follow_up_question"},
}


def execute_tool(name, arguments, context):
    if name not in TOOL_HANDLERS:
        return {"ok": False, "error": "Tool is not registered."}
    safe_arguments = sanitize_tool_arguments(arguments, TOOL_ARGUMENTS.get(name, set()))
    return TOOL_HANDLERS[name](context, safe_arguments)
