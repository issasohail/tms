from .safety import safe_summary


def format_verified_results(results, language="en", follow_up="", max_length=1200):
    lines = []
    for item in results:
        name = item["name"]
        data = item["result"]
        if not data.get("ok"):
            continue
        if name == "get_tenant_balance":
            lines.append(f"Outstanding balance: PKR {data['balance']}\n{data['property']} - {data['unit']}")
        elif name == "get_last_payment":
            payment = data.get("payment")
            lines.append("No payment is recorded yet." if not payment else f"Last payment: PKR {payment['amount']} on {payment['date']}" + (f"\nReference: {payment['reference']}" if payment.get("reference") else ""))
        elif name == "get_payment_history":
            rows = data.get("payments") or []
            lines.append("Recent payments:\n" + "\n".join(f"- {row['date']}: PKR {row['amount']}" for row in rows) if rows else "No recent payments are recorded.")
        elif name in {"get_latest_invoice", "get_invoice_link"}:
            invoice = data.get("invoice")
            lines.append("No invoice is available." if not invoice else f"Latest invoice {invoice['number']}: PKR {invoice['amount']}\nDue: {invoice['due_date']}" + (f"\n{invoice['path']}" if invoice.get("path") else ""))
        elif name == "get_ledger_link":
            lines.append(f"Ledger:\n{data['path']}")
        elif name == "get_active_lease":
            lines.append(f"Active lease: {data['property']} - {data['unit']}\n{data['start']} to {data['end']}\nRent: PKR {data['rent']}")
        elif name == "get_lease_expiry":
            lines.append(f"Lease end date: {data['end_date']}")
        elif name == "get_family_members":
            rows = data.get("family_members") or []
            lines.append("Family members:\n" + "\n".join(f"- {row['name']} ({row['relationship'] or 'relation not set'})" for row in rows) if rows else "No family members are linked.")
        elif name == "get_maintenance_status":
            rows = data.get("requests") or []
            lines.append("Maintenance status:\n" + "\n".join(f"- {row['title']}: {row['status']}" for row in rows) if rows else "No recent maintenance requests are recorded.")
        elif name == "create_maintenance_draft":
            lines.append(f"Maintenance draft {data['draft_reference']} was created and is waiting for staff approval.")
    if follow_up:
        lines.append(follow_up)
    reply = "\n\n".join(lines)
    return safe_summary(_localize(reply, language), max_length)


def _localize(reply, language):
    """Keep verified values unchanged while localizing common WhatsApp labels."""
    translations = {
        "roman_urdu": {
            "Outstanding balance:": "Baqaya balance:",
            "Last payment:": "Aakhri payment:",
            "Recent payments:": "Haalya payments:",
            "Latest invoice": "Aakhri invoice",
            "Due:": "Adaigi ki tareekh:",
            "Lease end date:": "Lease khatam honay ki tareekh:",
            "Family members:": "Family members:",
            "Maintenance status:": "Maintenance status:",
            "No payment is recorded yet.": "Abhi koi payment record nahin hai.",
            "No recent payments are recorded.": "Koi haalya payment record nahin hai.",
        },
        "ur": {
            "Outstanding balance:": "بقایا رقم:",
            "Last payment:": "آخری ادائیگی:",
            "Recent payments:": "حالیہ ادائیگیاں:",
            "Latest invoice": "تازہ ترین انوائس",
            "Due:": "آخری تاریخ:",
            "Lease end date:": "لیز ختم ہونے کی تاریخ:",
            "Family members:": "گھر کے افراد:",
            "Maintenance status:": "مرمت کی صورتحال:",
            "No payment is recorded yet.": "ابھی کوئی ادائیگی درج نہیں ہے۔",
            "No recent payments are recorded.": "کوئی حالیہ ادائیگی درج نہیں ہے۔",
        },
    }
    for source, target in translations.get(language, {}).items():
        reply = reply.replace(source, target)
    return reply
