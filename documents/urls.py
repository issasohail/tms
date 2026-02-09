from django.urls import path
from .views import (
    DocumentListView, DocumentDetailView, DocumentCreateView,
    DocumentUpdateView, DocumentDeleteView,
    LeaseDocumentListView, LeaseDocumentDetailView, LeaseDocumentCreateView,
    LeaseDocumentUpdateView, LeaseDocumentDeleteView,
)

app_name = 'documents'

urlpatterns = [
    # Document URLs
    path('documents/', DocumentListView.as_view(), name='document_list'),
    path('documents/<int:pk>/', DocumentDetailView.as_view(), name='document_detail'),
    path('documents/create/', DocumentCreateView.as_view(), name='document_create'),
    path('documents/<int:pk>/update/', DocumentUpdateView.as_view(), name='document_update'),
    path('documents/<int:pk>/delete/', DocumentDeleteView.as_view(), name='document_delete'),
    
    # Lease Document URLs
    path('lease-documents/', LeaseDocumentListView.as_view(), name='lease_document_list'),
    path('lease-documents/<int:pk>/', LeaseDocumentDetailView.as_view(), name='lease_document_detail'),
    path('lease-documents/create/', LeaseDocumentCreateView.as_view(), name='lease_document_create'),
    path('lease-documents/<int:pk>/update/', LeaseDocumentUpdateView.as_view(), name='lease_document_update'),
    path('lease-documents/<int:pk>/delete/', LeaseDocumentDeleteView.as_view(), name='lease_document_delete'),
]