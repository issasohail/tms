from django.urls import path
from . import views
from .views import (
    ExpenseListView, ExpenseDetailView, ExpenseCreateView,
    ExpenseUpdateView, ExpenseDeleteView,
    ExpenseDistributionListView, category_add_api, category_list_api,
    distribute_expense, add_to_invoices,
)
from .views import (
    ExpenseCreateView, ExpenseUpdateView,
    receipt_delete, receipt_update, receipt_add, receipt_delete,
)
app_name = 'expenses'

urlpatterns = [
    # Expense URLs
    path('', ExpenseListView.as_view(), name='expense_list'),
    path('<int:pk>/', ExpenseDetailView.as_view(), name='expense_detail'),
    path('create/', ExpenseCreateView.as_view(), name='expense_create'),
    path('<int:pk>/update/', ExpenseUpdateView.as_view(), name='expense_update'),
    path('<int:pk>/delete/', ExpenseDeleteView.as_view(), name='expense_delete'),

    # Distribution URLs
    path('distributions/', ExpenseDistributionListView.as_view(),
         name='distribution_list'),
    path('<int:pk>/distribute/', distribute_expense, name='distribute_expense'),
    path('<int:pk>/add-to-invoices/', add_to_invoices, name='add_to_invoices'),


    path('categories/api/', category_list_api, name='category_list_api'),
    path('categories/api/add/', category_add_api,
         name='category_add_api'),
    path("<int:expense_id>/receipts/add/",
         views.receipt_add, name="receipt_add"),
    path('receipt/<int:pk>/delete/', receipt_delete, name='receipt_delete'),
    path('receipt/<int:pk>/update/', receipt_update, name='receipt_update'),

]
