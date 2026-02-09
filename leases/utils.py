# leases/utils.py

from django.template.defaultfilters import intcomma
from leases.placeholder_registry import PLACEHOLDER_REGISTRY  # example import
from django import template
from django.http import HttpResponse
from django.template.loader import render_to_string
from django.utils import timezone
from django.contrib.humanize.templatetags.humanize import intcomma
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY, TA_LEFT
from reportlab.lib import colors
from reportlab.lib.units import inch
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from io import BytesIO

from leases.models import PLACEHOLDER_REGISTRY, Lease

# Utility: Convert numbers to words


def number_to_words(n):
    """Convert numbers to words (e.g., 1000 -> 'One Thousand')"""
    ones = ["", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine", "Ten",
            "Eleven", "Twelve", "Thirteen", "Fourteen", "Fifteen", "Sixteen", "Seventeen", "Eighteen", "Nineteen"]
    tens = ["", "", "Twenty", "Thirty", "Forty",
            "Fifty", "Sixty", "Seventy", "Eighty", "Ninety"]

    if n == 0:
        return "Zero"

    def convert_under_1000(num):
        if num == 0:
            return ""
        elif num < 20:
            return ones[num]
        elif num < 100:
            return tens[num // 10] + (" " + ones[num % 10] if num % 10 > 0 else "")
        else:
            return ones[num // 100] + " Hundred" + (" and " + convert_under_1000(num % 100) if num % 100 > 0 else "")

    parts = []
    if n >= 1_000_000:
        parts.append(convert_under_1000(n // 1_000_000) + " Million")
        n %= 1_000_000
    if n >= 1_000:
        parts.append(convert_under_1000(n // 1_000) + " Thousand")
        n %= 1_000
    if n > 0:
        parts.append(convert_under_1000(n))

    return " ".join(parts)


# Lease Agreement PDF Generator
def generate_lease_agreement(lease_id):
    lease = Lease.objects.get(id=lease_id)
    buffer = BytesIO()

    doc = SimpleDocTemplate(buffer, pagesize=letter,
                            rightMargin=0.5 * inch,
                            leftMargin=0.5 * inch,
                            topMargin=0.5 * inch,
                            bottomMargin=0.5 * inch)

    styles = getSampleStyleSheet()

    # Add custom styles if not already defined
    if 'JustifyText' not in styles:
        styles.add(ParagraphStyle(name='JustifyText',
                   parent=styles['Normal'], alignment=TA_JUSTIFY, spaceAfter=12))

    if 'Header1' not in styles:
        styles.add(ParagraphStyle(name='Header1', parent=styles['Heading1'], fontSize=14, alignment=TA_CENTER,
                                  spaceAfter=12, textColor=colors.darkblue))

    if 'SectionHeader' not in styles:
        styles.add(ParagraphStyle(name='SectionHeader', parent=styles['Heading2'], fontSize=12, alignment=TA_LEFT,
                                  spaceAfter=6, textColor=colors.darkblue))

    elements = []

    # Title
    elements.append(Paragraph("LEASE AGREEMENT", styles['Header1']))
    elements.append(Spacer(1, 0.25 * inch))

    property = lease.unit.property

    # Parties Section
    elements.append(Paragraph(
        f"This Lease Agreement is made on {lease.start_date.strftime('%B %d, %Y')} between:",
        styles['JustifyText']))
    elements.append(Spacer(1, 0.25 * inch))

    # Landlord Info
    elements.append(Paragraph("<b>LANDLORD:</b>", styles['SectionHeader']))
    elements.append(Paragraph(property.owner_name, styles['Normal']))
    landlord_info = f"{property.owner_address}<br/>CNIC: {property.owner_cnic}<br/>Phone: {property.owner_phone}"
    elements.append(Paragraph(landlord_info, styles['Normal']))
    elements.append(Spacer(1, 0.25 * inch))

    # Tenant Info
    elements.append(Paragraph("<b>TENANT:</b>", styles['SectionHeader']))
    elements.append(Paragraph(
        f"{lease.tenant.first_name} {lease.tenant.last_name}", styles['Normal']))
    if lease.tenant.address:
        elements.append(Paragraph(lease.tenant.address, styles['Normal']))
    if getattr(lease.tenant, 'phone', None):
        elements.append(
            Paragraph(f"Phone: {lease.tenant.phone}", styles['Normal']))
    elements.append(Spacer(1, 0.25 * inch))

    # Property Details
    elements.append(Paragraph("<b>1. PREMISES</b>", styles['SectionHeader']))
    property_address = f"""
    {property.property_name}<br/>
    {property.property_address1}<br/>
    {property.property_address2 or ''} {property.property_city}, {property.property_state} {property.property_zipcode}
    """
    elements.append(Paragraph(
        f"The Landlord leases to the Tenant:<br/><br/>{property_address}<br/>"
        f"Unit: {lease.unit.unit_number}<br/>"
        f"Type: {property.get_property_type_display()}",
        styles['JustifyText']))
    elements.append(Spacer(1, 0.25 * inch))

    # Lease Term
    elements.append(Paragraph("<b>2. TERM</b>", styles['SectionHeader']))
    elements.append(Paragraph(
        f"Term: {lease.get_lease_duration()} months "
        f"({lease.start_date.strftime('%b %d, %Y')} to {lease.end_date.strftime('%b %d, %Y')})",
        styles['JustifyText']))
    elements.append(Spacer(1, 0.25 * inch))

    # Rent Information
    elements.append(Paragraph("<b>3. RENT</b>", styles['SectionHeader']))
    rent_data = [
        ['Monthly Rent:', f"${lease.monthly_rent:,.2f}"],
        ['Maintenance:',
            f"${lease.society_maintenance:,.2f}" if lease.society_maintenance else "$0.00"],
        ['Total Monthly:',
            f"${lease.monthly_rent + (lease.society_maintenance or 0):,.2f}"],
        ['Security Deposit:', f"${lease.security_deposit:,.2f}"],
        ['Due Date:', '1st of each month'],
        ['Late Fee:', '5% after 5th day'],
    ]
    rent_table = Table(rent_data, colWidths=[2.5 * inch, 2.5 * inch])
    rent_table.setStyle(TableStyle([
        ('FONTNAME', (0, 0), (-1, -1), 'Helvetica'),
        ('FONTSIZE', (0, 0), (-1, -1), 10),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('ALIGN', (0, 0), (0, -1), 'LEFT'),
        ('ALIGN', (1, 0), (1, -1), 'RIGHT'),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.lightgrey),
    ]))
    elements.append(rent_table)
    elements.append(Spacer(1, 0.25 * inch))

    # Standard Sections
    sections = [
        ("4. PAYMENTS", "Payments by bank transfer to account specified by Landlord."),
        ("5. UTILITIES", "Tenant responsible for electricity, water, gas, and internet."),
        ("6. MAINTENANCE", "Landlord handles structural repairs and common areas."),
        ("7. RULES", "Tenant must comply with all property rules and regulations."),
        ("8. GOVERNING LAW", f"Governed by laws of {property.property_state}.")
    ]

    for header, content in sections:
        elements.append(Paragraph(f"<b>{header}</b>", styles['SectionHeader']))
        elements.append(Paragraph(content, styles['JustifyText']))
        elements.append(Spacer(1, 0.25 * inch))

    # Additional Terms
    if lease.terms:
        elements.append(
            Paragraph("<b>9. ADDITIONAL TERMS</b>", styles['SectionHeader']))
        elements.append(Paragraph(lease.terms, styles['JustifyText']))
        elements.append(Spacer(1, 0.25 * inch))

    # Signatures
    elements.append(Spacer(1, 0.5 * inch))
    signature_data = [
        ["LANDLORD:", "TENANT:"],
        [property.owner_name,
            f"{lease.tenant.first_name} {lease.tenant.last_name}"],
        ["", ""],
        ["Date: ___________________", "Date: ___________________"]
    ]
    signature_table = Table(signature_data, colWidths=[3 * inch, 3 * inch])
    signature_table.setStyle(TableStyle([
        ('FONTNAME', (0, 0), (-1, -1), 'Helvetica'),
        ('FONTSIZE', (0, 0), (-1, 0), 12),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('LINEABOVE', (0, 2), (0, 2), 0.5, colors.black),
        ('LINEABOVE', (1, 2), (1, 2), 0.5, colors.black),
    ]))
    elements.append(signature_table)

    # Build PDF
    doc.build(elements)
    buffer.seek(0)
    return buffer


# Resolve Placeholders in Clauses
def resolve_placeholders(lease, clause_text):
    for placeholder, resolver in PLACEHOLDER_REGISTRY.items():
        search = f'[{placeholder}]'
        if search in clause_text:
            try:
                clause_text = clause_text.replace(search, str(resolver(lease)))
            except Exception as e:
                clause_text = clause_text.replace(search, f'[ERROR: {e}]')

    return clause_text.replace('[GENERATION_TIMESTAMP]', timezone.now().strftime('%d-%m-%Y %H:%M:%S'))


# Generate HTML Agreement
def generate_agreement_html(lease):
    header_html = render_to_string('leases/agreement_header.html', {
        'AGREEMENT_DATE': timezone.now().strftime('%d-%m-%Y'),
        'OWNER_NAME': lease.unit.property.owner_name,
    })

    clauses_html = ""
    for clause in lease.clauses.order_by('clause_number'):
        resolved_text = resolve_placeholders(lease, clause.template_text)
        clauses_html += f'<p class="clause">{clause.clause_number}. {resolved_text}</p>\n'

    signature_html = f"""
    <div class="signature-section">
        <div class="signature"><p>_________________________</p><p>Owner: {lease.unit.property.owner_name}</p></div>
        <div class="signature"><p>_________________________</p><p>Tenant: {lease.tenant.get_full_name()}</p></div>
    </div>
    <div class="footer">Generated at: {timezone.now().strftime('%d-%m-%Y %H:%M:%S')}</div>
    """

    return f"""
    <!DOCTYPE html>
    <html><head><style>/* Agreement styling */</style></head>
    <body>
        {header_html}
        <div class="clauses-container">{clauses_html}</div>
        {signature_html}
    </body>
    </html>
    """


# leases/templatetags/lease_tags.py

register = template.Library()


# leases/utils.py


def do_replace_placeholders(text, lease):
    """Replace placeholders in clause text with actual values"""
    for placeholder, func in PLACEHOLDER_REGISTRY.items():
        search_str = f'[{placeholder}]'
        if search_str in text:
            try:
                replacement = func(lease)
                if any(term in placeholder for term in ['RENT', 'DEPOSIT', 'MAINTENANCE', 'TOTAL']):
                    try:
                        replacement = f"<strong>Rs.{intcomma(int(replacement))}/-</strong>"
                    except (TypeError, ValueError):
                        pass
                text = text.replace(search_str, str(replacement))
            except Exception as e:
                print(f"Error replacing {placeholder}: {e}")
    return text


# Django template filter
register = template.Library()


@register.filter
def replace_placeholders(text, lease):
    return do_replace_placeholders(text, lease)
