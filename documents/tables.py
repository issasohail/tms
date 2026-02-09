import django_tables2 as tables
from .models import Document, LeaseDocument
from django_tables2.utils import A

class DocumentTable(tables.Table):
    title = tables.LinkColumn('documents:document_detail', args=[A('pk')])
    tenant = tables.LinkColumn('tenants:tenant_detail', args=[A('tenant.pk')], empty_values=())
    
    def render_tenant(self, record):
        if record.tenant:
            return record.tenant
        return '-'
    
    class Meta:
        model = Document
        template_name = 'django_tables2/bootstrap5.html'
        fields = ('title', 'tenant', 'category', 'upload_date')
        attrs = {'class': 'table table-striped table-hover'}

class LeaseDocumentTable(tables.Table):
    title = tables.LinkColumn('documents:lease_document_detail', args=[A('pk')])
    tenant = tables.LinkColumn('tenants:tenant_detail', args=[A('tenant.pk')])
    is_active = tables.BooleanColumn(verbose_name='Active?', yesno='✓,✗')
    
    class Meta:
        model = LeaseDocument
        template_name = 'django_tables2/bootstrap5.html'
        fields = ('title', 'tenant', 'start_date', 'end_date', 'monthly_rent', 'is_active')
        attrs = {'class': 'table table-striped table-hover'}