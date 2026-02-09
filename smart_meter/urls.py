from django.urls import path
from .views import assign_meter
from .views import meter_dashboard
from .views import generate_bill_view, view_bills
from .views import recharge_balance, meter_status
from smart_meter.views import meter_settings, refund_balance, toggle_power
from . import views
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

    path("assign/", assign_meter, name="assign_meter"),
    path("dashboard/<int:unit_id>/", meter_dashboard, name="meter_dashboard"),
    path("bills/<int:unit_id>/", view_bills, name="view_bills"),

    path("recharge/<int:unit_id>/", recharge_balance, name="recharge_balance"),
    path("settings/", meter_settings, name="meter_settings"),
    path("unit/<int:unit_id>/refund/", refund_balance, name="refund_balance"),
    path('energy-dashboard/', energy_dashboard, name='energy_dashboard'),

    path('meters/', views.meter_list, name='meter_list'),
    path('meters/add/', views.add_meter, name='add_meter'),
    path('meters/<int:pk>/', views.meter_detail, name='meter_detail'),
    path('meters/<int:pk>/edit/', views.meter_edit, name='meter_edit'),
    path('meters/<int:pk>/delete/', views.meter_delete, name='meter_delete'),


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
     
]
