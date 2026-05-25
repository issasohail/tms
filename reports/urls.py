from django.urls import path
from .views import (
    FinancialReportListView, FinancialReportDetailView, FinancialReportCreateView,
    generate_property_report, generate_tenant_statement, ReportListView
)
from django.urls import path
from . import views
from django.urls import path
from .views import SecurityDepositReportView
from .views import (
    MaintenanceCollectionReportView,
    RentCollectionReportView,
    TenantBalanceReportView,
    LeaseRenewalDueReportView,
    VacantUnitReportView,
    LateFeeReportView,
    MeterReportView,
)

app_name = 'reports'

urlpatterns = [
    # Financial Report URLs
    path('financial-reports/', FinancialReportListView.as_view(),
         name='financial_report_list'),
    path('financial-reports/<int:pk>/',
         FinancialReportDetailView.as_view(), name='financial_report_detail'),
    path('financial-reports/create/', FinancialReportCreateView.as_view(),
         name='financial_report_create'),

    # Generated Reports
    path('property/<int:property_id>/report/',
         generate_property_report, name='generate_property_report'),
    path('tenant/<int:tenant_id>/statement/',
         generate_tenant_statement, name='generate_tenant_statement'),
    path('reports/', ReportListView.as_view(), name='report_list'),
    path('expenses/summary/', views.expense_summary_report, name='expense_summary'),
    path('expenses/detail/', views.expense_detail_report, name='expense_detail'),
    path('pl/', views.profit_and_loss_report, name='profit_loss'),
    path(
        "security-deposits/",
        SecurityDepositReportView.as_view(),
        name="security_deposit_report",
    ),
    path(
        "maintenance-collection/",
        MaintenanceCollectionReportView.as_view(),
        name="maintenance_collection_report",
    ),
    path("rent-collection/", RentCollectionReportView.as_view(), name="rent_collection_report"),
    path("tenant-balances/", TenantBalanceReportView.as_view(), name="tenant_balance_report"),
    path("lease-renewals-due/", LeaseRenewalDueReportView.as_view(), name="lease_renewal_due_report"),
    path("vacant-units/", VacantUnitReportView.as_view(), name="vacant_unit_report"),
    path("late-fees/", LateFeeReportView.as_view(), name="late_fee_report"),
    path("meters/", MeterReportView.as_view(), name="meter_report"),
]
