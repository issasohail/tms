from django import template
from django.utils.safestring import mark_safe
from django_tables2.export.export import TableExport

register = template.Library()

EXPORT_FORMATS = ['csv', 'xlsx']


@register.simple_tag
def export_buttons(request, table):
    export_format = request.GET.get("_export", None)
    # Skip the check and let TableExport handle it
    if export_format:
        exporter = TableExport(export_format, table)
        return exporter.response(f"payments.{export_format}")

    links = []
    for format in TableExport.EXPORT_FORMATS:
        url = request.get_full_path().split("&_export=")[
            0]  # Clean old _export params
        export_url = f"{url}&_export={format}" if "?" in url else f"{url}?_export={format}"
        links.append(
            f'<a class="btn btn-outline-secondary btn-sm me-2" href="{export_url}">Export {format.upper()}</a>'
        )
    return mark_safe(" ".join(links))
