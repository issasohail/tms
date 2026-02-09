import django_tables2 as tables
from .models import FinancialReport
from django_tables2.utils import A

class FinancialReportTable(tables.Table):
    id = tables.LinkColumn('reports:financial_report_detail', args=[A('pk')])
    
    class Meta:
        model = FinancialReport
        template_name = 'django_tables2/bootstrap5.html'
        fields = ('id', 'report_type', 'start_date', 'end_date', 'generated_at')
        attrs = {'class': 'table table-striped table-hover'}