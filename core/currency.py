from decimal import Decimal, InvalidOperation


CURRENCY_SYMBOLS = {
    "PKR": "Rs.",
    "USD": "$",
    "GBP": "£",
    "EUR": "€",
    "AED": "AED",
    "SAR": "SAR",
    "CAD": "C$",
    "AUD": "A$",
    "INR": "₹",
}


def currency_symbol(settings_obj=None):
    code = (getattr(settings_obj, "currency_code", None) or "PKR").upper()
    return CURRENCY_SYMBOLS.get(code, code)


def format_money(value, settings_obj=None, decimals=2):
    symbol = currency_symbol(settings_obj)
    try:
        amount = Decimal(value or 0)
    except (InvalidOperation, TypeError, ValueError):
        amount = Decimal("0")
    return f"{symbol} {amount:,.{decimals}f}"
