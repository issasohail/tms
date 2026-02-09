from io import BytesIO
from django.http import HttpResponse
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY, TA_LEFT
from reportlab.lib import colors
from reportlab.lib.units import inch


def generate_lease_agreement(lease):
    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=letter,
                            rightMargin=0.5*inch, leftMargin=0.5*inch,
                            topMargin=0.5*inch, bottomMargin=0.5*inch)

    styles = getSampleStyleSheet()

    # Custom styles
    styles.add(ParagraphStyle(
        name='Justify',
        parent=styles['Normal'],
        alignment=TA_JUSTIFY,
        spaceAfter=12
    ))

    styles.add(ParagraphStyle(
        name='Header1',
        parent=styles['Heading1'],
        fontSize=14,
        alignment=TA_CENTER,
        spaceAfter=12,
        textColor=colors.darkblue
    ))

    styles.add(ParagraphStyle(
        name='SectionHeader',
        parent=styles['Heading2'],
        fontSize=12,
        alignment=TA_LEFT,
        spaceAfter=6,
        textColor=colors.darkblue
    ))

    elements = []

    # Title
    elements.append(Paragraph("LEASE AGREEMENT", styles['Header1']))
    elements.append(Spacer(1, 0.25*inch))

    # Parties Section
    elements.append(Paragraph(
        f"This Lease Agreement is made and entered into this {lease.start_date.strftime('%B %d, %Y')} by and between:",
        styles['Justify']
    ))
    elements.append(Spacer(1, 0.25*inch))

    # Landlord Info
    elements.append(Paragraph("<b>LANDLORD:</b>", styles['SectionHeader']))
    elements.append(Paragraph("[Your Company Name]", styles['Normal']))
    elements.append(Paragraph("[Company Address]", styles['Normal']))
    elements.append(Spacer(1, 0.25*inch))

    # Tenant Info
    elements.append(Paragraph("<b>TENANT:</b>", styles['SectionHeader']))
    elements.append(Paragraph(
        f"{lease.tenant.first_name} {lease.tenant.last_name}",
        styles['Normal']
    ))
    if lease.tenant.address:
        elements.append(Paragraph(lease.tenant.address, styles['Normal']))
    elements.append(Spacer(1, 0.25*inch))

    # Property Details
    elements.append(Paragraph("<b>1. PREMISES</b>", styles['SectionHeader']))
    elements.append(Paragraph(
        f"The Landlord leases to the Tenant the premises located at {lease.unit.property.property_name}, "
        f"Unit {lease.unit.unit_number} for use as a residential dwelling only.",
        styles['Justify']
    ))
    elements.append(Spacer(1, 0.25*inch))

    # Lease Term
    elements.append(Paragraph("<b>2. TERM</b>", styles['SectionHeader']))
    elements.append(Paragraph(
        f"The term of this Lease shall be for {lease.get_lease_duration()} months, "
        f"commencing on {lease.start_date.strftime('%B %d, %Y')} and ending on "
        f"{lease.end_date.strftime('%B %d, %Y')}, unless sooner terminated as provided herein.",
        styles['Justify']
    ))
    elements.append(Spacer(1, 0.25*inch))

    # Rent Information
    elements.append(Paragraph("<b>3. RENT</b>", styles['SectionHeader']))

    # Create a table for rent details
    rent_data = [
        ['Monthly Rent:', f"${lease.monthly_rent:,.2f}"],
        ['Maintenance Fee:',
            f"${lease.society_maintenance:,.2f}" if lease.society_maintenance else "$0.00"],
        ['Total Monthly Payment:',
            f"${lease.monthly_rent + (lease.society_maintenance or 0):,.2f}"],
        ['Security Deposit:', f"${lease.security_deposit:,.2f}"],
        ['Due Date:', '1st day of each month']
    ]

    rent_table = Table(rent_data, colWidths=[2*inch, 2*inch])
    rent_table.setStyle(TableStyle([
        ('FONTNAME', (0, 0), (-1, -1), 'Helvetica'),
        ('FONTSIZE', (0, 0), (-1, -1), 10),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('ALIGN', (0, 0), (0, -1), 'LEFT'),
        ('ALIGN', (1, 0), (1, -1), 'RIGHT'),
    ]))

    elements.append(rent_table)
    elements.append(Spacer(1, 0.25*inch))

    # Payment Method
    elements.append(
        Paragraph("<b>4. PAYMENT METHOD</b>", styles['SectionHeader']))
    elements.append(Paragraph(
        "All payments shall be made by bank transfer to the account specified by Landlord. "
        "Late payments shall incur a penalty of 5% of the monthly rent.",
        styles['Justify']
    ))
    elements.append(Spacer(1, 0.25*inch))

    # Utilities
    elements.append(Paragraph("<b>5. UTILITIES</b>", styles['SectionHeader']))
    elements.append(Paragraph(
        "Tenant shall be responsible for all utilities including electricity, water, gas, "
        "and internet service. Landlord shall provide trash removal services.",
        styles['Justify']
    ))
    elements.append(Spacer(1, 0.25*inch))

    # Additional Terms
    if lease.terms:
        elements.append(
            Paragraph("<b>6. ADDITIONAL TERMS</b>", styles['SectionHeader']))
        elements.append(Paragraph(lease.terms, styles['Justify']))
        elements.append(Spacer(1, 0.25*inch))

    # Signature Section
    elements.append(Spacer(1, 0.5*inch))
    elements.append(Paragraph("LANDLORD:", styles['SectionHeader']))
    elements.append(Spacer(1, 0.25*inch))
    elements.append(
        Paragraph("_____________________________", styles['Normal']))
    elements.append(Paragraph("Signature", styles['Normal']))
    elements.append(Paragraph("Date: ___________________", styles['Normal']))

    elements.append(Spacer(1, 0.5*inch))
    elements.append(Paragraph("TENANT:", styles['SectionHeader']))
    elements.append(Spacer(1, 0.25*inch))
    elements.append(
        Paragraph("_____________________________", styles['Normal']))
    elements.append(Paragraph("Signature", styles['Normal']))
    elements.append(Paragraph("Date: ___________________", styles['Normal']))

    doc.build(elements)
    buffer.seek(0)
    return buffer
