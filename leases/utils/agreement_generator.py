# leases/utils/agreement_generator.py
import os
from datetime import datetime
from io import BytesIO
from django.core.files import File
from django.conf import settings
from docxtpl import DocxTemplate
import subprocess
from num2words import num2words
import glob


def find_template():
    return glob.glob(os.path.join(
        settings.BASE_DIR,
        'leases',
        'templates',
        'leases',
        '*.docx'
    ))[0]  # Gets first .docx file found


def get_template_path():
    """Returns verified absolute path to template"""
    # Use forward slashes for consistent path handling
    template_rel_path = 'leases/templates/leases/lease_agreement_template.docx'
    path = os.path.normpath(os.path.join(settings.BASE_DIR, template_rel_path))

    # Debug output (remove after verification)
    print(f"Final resolved path: {path}")
    print(f"Path exists: {os.path.exists(path)}")

    if not os.path.exists(path):
        raise FileNotFoundError(
            f"Template file missing at:\n{path}\n"
            "Please ensure:\n"
            "1. The file exists exactly at this location\n"
            "2. The file extension is .docx (not .doc)\n"
            "3. The file isn't open in Microsoft Word"
        )
    return path


def generate_lease_agreement(lease):
    """
    Generate lease agreement in both DOCX and PDF formats
    """
    template_path = get_template_path()

    print("BASE_DIR:", settings.BASE_DIR)
    print("Looking for template at:", template_path)
    print("Directory contents:", os.listdir(os.path.dirname(template_path)))

    # Verify template exists
    if not os.path.exists(template_path):
        raise FileNotFoundError(
            f"Lease agreement template not found at: {template_path}\n"
            f"Please create the template file at this location."
        )

    # Ensure the output directories exist
    os.makedirs(os.path.join(settings.MEDIA_ROOT,
                'leases/agreements/generated'), exist_ok=True)

    # Load the template
    template_path = os.path.join(
        settings.BASE_DIR, 'leases', 'templates', 'lease_agreement_template.docx')

    # Prepare context data
    context = {
        'rent_words': num2words(lease.monthly_rent, lang='en_IN').title(),
        'security_deposit_words': num2words(lease.security_deposit, lang='en_IN').title(),
        'maintenance_words': num2words(lease.society_maintenance, lang='en_IN').title(),
        'generation_date': datetime.now().strftime('%d-%m-%Y'),
        'owner_name': lease.unit.property.owner_name,
        'owner_cnic': lease.unit.property.owner_cnic,
        'owner_address': lease.unit.property.owner_address,
        'tenant_name': f"{lease.tenant.first_name} {lease.tenant.last_name}",
        'tenant_cnic': lease.tenant.cnic,
        'tenant_address': lease.tenant.address,
        'property_number': lease.unit.unit_number,
        'property_address': f"{lease.unit.property.property_address1}",
        'portion_details': lease.unit.comments,
        'monthly_rent': lease.monthly_rent,
        'rent_words': num2words(lease.monthly_rent, lang='en_IN').title(),
        'society_maintenance': lease.society_maintenance,
        'total_monthly': lease.total_payment,
        'advance_months': 1,  # or calculate
        'advance_amount': lease.total_payment,
        'security_deposit': lease.security_deposit,
        'lease_duration': lease.get_lease_duration(),
        'lease_start': lease.start_date.strftime('%d-%m-%Y'),
        'lease_end': lease.end_date.strftime('%d-%m-%Y'),
        'min_stay_months': 6,  # from your sample
        'early_vacation_penalty': 2000,
        'ceiling_fans': lease.unit.ceiling_fan,
        'fans_condition': 'Working condition',
        'ceiling_lights': lease.unit.ceiling_lights,
        'lights_condition': 'All functional',
        'exhaust_fans': lease.unit.exhaust_fan,
        'exhaust_condition': 'Good condition',
        'stove': lease.unit.stove,
        'stove_condition': '3-burner functional',
        'wardrobes': lease.unit.wardrobes,
        'wardrobes_condition': 'No damage',
        'keys': lease.unit.keys,
        'keys_condition': 'All provided',

        # Special conditions
        'special_condition_1': 'No nails or drilling on walls',
        'special_condition_2': 'No subletting without permission',
    }

    # Generate DOCX
    doc = DocxTemplate(template_path)
    doc.render(context)

    docx_buffer = BytesIO()
    doc.save(docx_buffer)
    docx_buffer.seek(0)

    # Save DOCX version
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    docx_filename = f"lease_{lease.id}_{timestamp}.docx"
    lease.generated_agreement_docx.save(docx_filename, File(docx_buffer))

    # Generate PDF using LibreOffice (more reliable than pdfkit for DOCX to PDF)
    try:
        # Save temporary DOCX file
        temp_docx_path = os.path.join(
            settings.MEDIA_ROOT, 'temp', f'temp_{lease.id}_{timestamp}.docx')
        os.makedirs(os.path.dirname(temp_docx_path), exist_ok=True)
        with open(temp_docx_path, 'wb') as f:
            f.write(docx_buffer.getvalue())

        # Convert to PDF using LibreOffice
        temp_pdf_path = os.path.join(
            settings.MEDIA_ROOT, 'temp', f'temp_{lease.id}_{timestamp}.pdf')
        cmd = [
            'soffice',
            '--headless',
            '--convert-to', 'pdf',
            '--outdir', os.path.dirname(temp_pdf_path),
            temp_docx_path
        ]

        subprocess.run(cmd, check=True)

        # Save PDF version
        with open(temp_pdf_path, 'rb') as pdf_file:
            pdf_filename = f"lease_{lease.id}_{timestamp}.pdf"
            lease.generated_agreement_pdf.save(pdf_filename, File(pdf_file))

        # Clean up temporary files
        os.remove(temp_docx_path)
        os.remove(temp_pdf_path)

    except Exception as e:
        print(f"PDF conversion failed: {str(e)}")
        lease.generated_agreement_pdf = None

    lease.generated_date = datetime.now()
    lease.save()

    return lease
