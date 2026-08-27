from django.urls import path
from .views import assign_meter
from .views import meter_dashboard
from .views import generate_bill_view, view_bills
from .views import recharge_balance, meter_status
from smart_meter.views import meter_settings, refund_balance, toggle_power
from . import views
from . import views_reconciliation
from . import views_schedule
from . import views_credit_control
from .views_dashboard import energy_dashboard
from . import views_dashboard
from django.contrib import admin
from django.urls import path, include
from . import views_prepaid
from .views_invoice import electric_bill_preview, electric_bill_commit
from . import views_dashboard
from .views_invoice import electric_bill_preview, electric_bill_commit
from .views_dashboard import billing_summary, billing_summary_items
from . import views
from .views_invoice import (
    electric_bill_preview_by_meter,   # single-meter helper
    electric_bill_bulk_preview,       # ALL meters preview
    electric_bill_bulk_commit,        # ALL meters commit

)
app_name = 'smart_meter'

urlpatterns = [

    path("energy-systems/", views_reconciliation.energy_system_list, name="energy_system_list"),
    path("energy-systems/<int:pk>/", views_reconciliation.energy_system_detail, name="energy_system_detail"),
    path("energy-systems/<int:pk>/reassign-meter/", views_reconciliation.energy_system_reassign_meter, name="energy_system_reassign_meter"),
    path("energy-systems/<int:system_id>/inverter-statements/add/", views_reconciliation.inverter_statement_add, name="inverter_statement_add"),
    path("inverter-statements/<int:pk>/edit/", views_reconciliation.inverter_statement_edit, name="inverter_statement_edit"),
    path("inverter-statements/<int:pk>/confirm/", views_reconciliation.inverter_statement_confirm, name="inverter_statement_confirm"),
    path("inverter-statements/<int:pk>/reopen/", views_reconciliation.inverter_statement_reopen, name="inverter_statement_reopen"),
    path("utility-bills/upload/", views_reconciliation.utility_bill_upload, name="utility_bill_upload"),
    path("utility-bills/<int:pk>/", views_reconciliation.utility_bill_detail, name="utility_bill_detail"),
    path("utility-bills/<int:pk>/edit/", views_reconciliation.utility_bill_edit, name="utility_bill_edit"),
    path("utility-bills/<int:pk>/confirm/", views_reconciliation.utility_bill_confirm, name="utility_bill_confirm"),
    path("utility-bills/<int:pk>/finalize/", views_reconciliation.utility_bill_finalize, name="utility_bill_finalize"),
    path("utility-bills/<int:pk>/reopen/", views_reconciliation.utility_bill_reopen, name="utility_bill_reopen"),
    path("utility-bills/<int:bill_id>/payments/add/", views_reconciliation.utility_bill_payment_add, name="utility_bill_payment_add"),
    path("utility-bill-payments/<int:pk>/edit/", views_reconciliation.utility_bill_payment_edit, name="utility_bill_payment_edit"),
    path("utility-bill-payments/<int:pk>/confirm/", views_reconciliation.utility_bill_payment_confirm, name="utility_bill_payment_confirm"),

    path("assign/", assign_meter, name="assign_meter"),
    path("dashboard/<int:unit_id>/", meter_dashboard, name="meter_dashboard"),
    path("bills/<int:unit_id>/", view_bills, name="view_bills"),

    path("recharge/<int:unit_id>/", recharge_balance, name="recharge_balance"),
    path("settings/", meter_settings, name="meter_settings"),
    path("unit/<int:unit_id>/refund/", refund_balance, name="refund_balance"),
    path('energy-dashboard/', energy_dashboard, name='energy_dashboard'),

    path('meters/', views.meter_list, name='meter_list'),
    path('meters/schedules/', views_schedule.meter_schedule_list, name='meter_schedule_list'),
    path('meters/schedules/<int:meter_id>/update/', views_schedule.meter_schedule_update, name='meter_schedule_update'),
    path('meters/schedules/<int:meter_id>/copy/', views_schedule.meter_schedule_copy, name='meter_schedule_copy'),
    path('meters/<int:meter_id>/schedule/', views_schedule.meter_schedule_detail, name='meter_schedule_detail'),
    path('meters/add/', views.add_meter, name='add_meter'),
    path('meters/<int:pk>/', views.meter_detail, name='meter_detail'),
    path('meters/<int:pk>/credit-control/', views_credit_control.credit_control, name='credit_control'),
    path('meters/<int:pk>/edit/', views.meter_edit, name='meter_edit'),
    path('meters/<int:pk>/role/', views.meter_role_update, name='meter_role_update'),
    path('meters/<int:pk>/delete/', views.meter_delete, name='meter_delete'),
    path('check-groups/', views.meter_check_group_list, name='meter_check_group_list'),
    path('check-groups/add/', views.meter_check_group_form, name='meter_check_group_add'),
    path('check-groups/<int:pk>/', views.meter_check_group_detail, name='meter_check_group_detail'),
    path('check-groups/<int:pk>/edit/', views.meter_check_group_form, name='meter_check_group_edit'),
    path(
        'check-groups/<int:pk>/name/',
        views.meter_check_group_name_update,
        name='meter_check_group_name_update',
    ),
    path(
        'check-groups/<int:pk>/delete-manage/',
        views.meter_check_group_delete_manage,
        name='meter_check_group_delete_manage',
    ),
    path(
        'check-groups/<int:pk>/memberships/<int:membership_id>/end/',
        views.meter_check_group_membership_end,
        name='meter_check_group_membership_end',
    ),
    path(
        'check-groups/<int:pk>/memberships/<int:membership_id>/manage/',
        views.meter_check_group_membership_manage,
        name='meter_check_group_membership_manage',
    ),
    path("units/<int:unit_id>/meters/install/",
         views.install_meter_to_unit, name="install_meter_to_unit"),
    path("units/<int:unit_id>/meters/switch/",
         views.switch_meter, name="switch_meter"),
    path("installations/<int:installation_id>/close/",
         views.close_meter_installation, name="close_meter_installation"),
    path("leases/<int:lease_id>/move-unit/",
         views.move_lease_unit, name="move_lease_unit"),


    path('readings/', views.reading_list, name='reading_list'),
    path('readings/<int:pk>/edit/', views.edit_reading, name='edit_reading'),
    path('readings/<int:pk>/delete/', views.delete_reading, name='delete_reading'),
    path('meters/<int:meter_id>/readings/',
         views.meter_readings, name='meter_readings'),

    path('toggle_power/<int:meter_id>/',
         views.toggle_power, name='toggle_power'),

    path("report/daily/<int:unit_id>/",
         views.daily_report, name="smart_meter_daily"),
    path("report/monthly/<int:unit_id>/",
         views.monthly_report, name="smart_meter_monthly"),
    path("bill/generate/<int:unit_id>/", views.generate_bill_view,
         name="smart_meter_generate_bill"),
    path("live/custom/", views.live_custom, name="smart_meter_live_custom"),
    path("action/recharge/<int:meter_id>/",
         views.recharge_meter, name="smart_meter_recharge"),
    path("action/cutoff/<int:meter_id>/",
         views.cutoff_meter, name="smart_meter_cutoff"),
    path("action/restore/<int:meter_id>/",
         views.restore_meter, name="smart_meter_restore"),
    path("unknown/", views.unknown_meter_list, name="unknown_meter_list"),
    path("unknown/<int:pk>/convert/", views.unknown_meter_convert,
         name="unknown_meter_convert"),
    path("unknown/<int:pk>/ignore/", views.unknown_meter_ignore,
         name="unknown_meter_ignore"),
    path("unknown/", views.unknown_meter_list, name="unknown_meter_list"),

    path("unknown/<int:pk>/approve/", views.unknown_meter_quick_add,
         name="unknown_meter_quick_add"),  # NEW
    path('fetch-meter-data/', views.fetch_meter_data, name='fetch_meter_data'),

    path("meters/export/csv/",  views.meters_export_csv,  name="meters_export_csv"),
    path("meters/export/xlsx/", views.meters_export_xlsx,
         name="meters_export_xlsx"),

    # reading exports (keep these separate, different names)
    path("readings/export/csv/",  views.export_meter_readings_csv,
         name="meter_readings_export_csv"),
    path("readings/export/xlsx/", views.export_meter_readings_xlsx,
         name="meter_readings_export_xlsx"),
    path("reports/hourly/", views.hourly_report, name="hourly_report"),


    path('energy-dashboard/export/csv/',
         views_dashboard.energy_export_csv,  name='energy_export_csv'),
    path('energy-dashboard/export/xlsx/',
         views_dashboard.energy_export_xlsx, name='energy_export_xlsx'),
    path('energy-dashboard/export/pdf/',
         views_dashboard.energy_export_pdf,  name='energy_export_pdf'),
    path("chart/", views_dashboard.energy_chart_page, name="energy_chart_page"),

    path("control/switch/", views.meter_switch, name="meter_switch"),
    path("control/prepaid/", views_prepaid.prepaid_params, name="prepaid_params"),

    path("control/bulk/", views.bulk_power_action, name="bulk_power_action"),
    path("control/switch-lab/", views.switch_lab, name="switch_lab"),

    path(
        "meters/<int:meter_id>/status/",
        views.meter_status,
        name="meter_status"),
    path("readings/new/", views.meter_reading_create,
         name="meter_reading_create"),



    path("invoice/electric/preview/<int:lease_id>/<int:meter_id>/",
         electric_bill_preview, name="electric_bill_preview"),
    path("invoice/electric/commit/<int:lease_id>/<int:meter_id>/",
         electric_bill_commit, name="electric_bill_commit"),
    path(
        "invoice/electric/preview/by-meter/<int:meter_id>/",
        electric_bill_preview_by_meter,
        name="electric_bill_preview_by_meter",
    ),
    path("invoice/electric/preview/<int:lease_id>/<int:meter_id>/",
         electric_bill_preview, name="electric_bill_preview"),
    path("invoice/electric/commit/<int:lease_id>/<int:meter_id>/",
         electric_bill_commit, name="electric_bill_commit"),

    path("invoice/electric/preview/<int:lease_id>/<int:meter_id>/",
         electric_bill_preview, name="electric_bill_preview"),
    path("invoice/electric/commit/<int:lease_id>/<int:meter_id>/",
         electric_bill_commit, name="electric_bill_commit"),

    path("invoice/electric/preview/by-meter/<int:meter_id>/",
         electric_bill_preview_by_meter, name="electric_bill_preview_by_meter"),
    path("invoice/electric/preview/bulk/",
         electric_bill_bulk_preview, name="electric_bill_bulk_preview"),
    path("invoice/electric/commit/bulk/",
         electric_bill_bulk_commit, name="electric_bill_bulk_commit"),
    path("readings/<int:pk>/edit-inline/",
         views.meter_reading_row_edit, name="meter_reading_row_edit"),
    path("readings/<int:pk>/delete/", views.meter_reading_delete,
         name="meter_reading_delete"),

    path("reports/billing-summary/",
         views_dashboard.billing_summary, name="billing_summary"),
    path("reports/billing-summary/items/",
         views_dashboard.billing_summary_items, name="billing_summary_items"),
    path("reports/billing-summary/export-excel/",
         views_dashboard.billing_summary_export_excel, name="billing_summary_export_excel"),
    path("reports/billing-summary/export-pdf/",
         views_dashboard.billing_summary_export_pdf, name="billing_summary_export_pdf"),
    path("meters/<int:meter_id>/display/reset/",
         views.reset_meter_display_balance, name="reset_meter_display_balance"),
    path("meters/<int:meter_id>/display/set/",
         views.set_meter_display_balance,   name="set_meter_display_balance"),
    path("live-custom/data/", views.live_custom_data, name="smart_meter_live_custom_data"),
    path("live-custom/<int:meter_id>/instant/", views.instant_live_reading, name="smart_meter_instant_live_reading"),
     
]
