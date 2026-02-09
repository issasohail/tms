import logging
from django.template.loader import render_to_string
from weasyprint import HTML, CSS
from weasyprint.text.fonts import FontConfiguration
from django.conf import settings

logger = logging.getLogger(__name__)


def generate_payment_pdf(payment, request=None):
    """Generate PDF that matches payment_detail.html exactly"""
    try:
        context = {
            'payment': payment,
            'base_url': request.build_absolute_uri('/') if request else '',
            'is_pdf': True
        }

        filename = (
            f"{payment.lease.tenant.first_name}_"
            f"{payment.lease.tenant.last_name}-"
            f"{payment.lease.unit.property.property_name.replace(' ', '_')}-"
            f"{payment.payment_date.strftime('%Y-%m-%d')}-"
            f"{payment.amount:.2f}.pdf"
        )

        html_string = render_to_string('payments/payment_pdf.html', context)
        font_config = FontConfiguration()

        css = CSS(string='''
            @page { size: A4 portrait; margin: 1cm; }
            body { font-family: Arial; font-size: 12px; }
            /* Add other CSS rules to match your template */
        ''')

        # Updated PDF generation code
        html = HTML(string=html_string)
        pdf = html.write_pdf(stylesheets=[css], font_config=font_config)

        return pdf, filename

    except Exception as e:
        logger.error(f"PDF generation failed: {str(e)}", exc_info=True)
        raise
