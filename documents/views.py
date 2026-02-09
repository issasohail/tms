from django.shortcuts import render, get_object_or_404, redirect
from django.views.generic import ListView, DetailView, CreateView, UpdateView, DeleteView
from django.urls import reverse_lazy
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django_tables2 import SingleTableView
from tenants.models import Tenant
from .models import Document, DocumentCategory, LeaseDocument
from .tables import DocumentTable, LeaseDocumentTable
from .forms import DocumentForm, LeaseDocumentForm

class DocumentListView(LoginRequiredMixin, SingleTableView):
    model = Document
    table_class = DocumentTable
    template_name = 'documents/document_list.html'
    context_object_name = 'documents'

    def get_queryset(self):
        queryset = super().get_queryset()
        tenant_id = self.request.GET.get('tenant')
        if tenant_id:
            queryset = queryset.filter(tenant_id=tenant_id)
        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['tenants'] = Tenant.objects.filter(is_active=True)
        return context

class DocumentDetailView(LoginRequiredMixin, DetailView):
    model = Document
    template_name = 'documents/document_detail.html'
    context_object_name = 'document'

class DocumentCreateView(LoginRequiredMixin, CreateView):
    model = Document
    form_class = DocumentForm
    template_name = 'documents/document_form.html'

    def get_success_url(self):
        return reverse_lazy('document_detail', kwargs={'pk': self.object.pk})

    def form_valid(self, form):
        response = super().form_valid(form)
        messages.success(self.request, 'Document uploaded successfully.')
        return response

class DocumentUpdateView(LoginRequiredMixin, UpdateView):
    model = Document
    form_class = DocumentForm
    template_name = 'documents/document_form.html'

    def get_success_url(self):
        return reverse_lazy('document_detail', kwargs={'pk': self.object.pk})

    def form_valid(self, form):
        response = super().form_valid(form)
        messages.success(self.request, 'Document updated successfully.')
        return response

class DocumentDeleteView(LoginRequiredMixin, DeleteView):
    model = Document
    template_name = 'documents/document_confirm_delete.html'
    success_url = reverse_lazy('document_list')

    def delete(self, request, *args, **kwargs):
        messages.success(self.request, 'Document deleted successfully.')
        return super().delete(request, *args, **kwargs)

class LeaseDocumentListView(LoginRequiredMixin, SingleTableView):
    model = LeaseDocument
    table_class = LeaseDocumentTable
    template_name = 'documents/lease_document_list.html'
    context_object_name = 'lease_documents'

    def get_queryset(self):
        queryset = super().get_queryset()
        tenant_id = self.request.GET.get('tenant')
        if tenant_id:
            queryset = queryset.filter(tenant_id=tenant_id)
        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['tenants'] = Tenant.objects.filter(is_active=True)
        return context

class LeaseDocumentDetailView(LoginRequiredMixin, DetailView):
    model = LeaseDocument
    template_name = 'documents/lease_document_detail.html'
    context_object_name = 'lease_document'

class LeaseDocumentCreateView(LoginRequiredMixin, CreateView):
    model = LeaseDocument
    form_class = LeaseDocumentForm
    template_name = 'documents/lease_document_form.html'

    def get_success_url(self):
        return reverse_lazy('lease_document_detail', kwargs={'pk': self.object.pk})

    def form_valid(self, form):
        response = super().form_valid(form)
        messages.success(self.request, 'Lease document uploaded successfully.')
        return response

class LeaseDocumentUpdateView(LoginRequiredMixin, UpdateView):
    model = LeaseDocument
    form_class = LeaseDocumentForm
    template_name = 'documents/lease_document_form.html'

    def get_success_url(self):
        return reverse_lazy('lease_document_detail', kwargs={'pk': self.object.pk})

    def form_valid(self, form):
        response = super().form_valid(form)
        messages.success(self.request, 'Lease document updated successfully.')
        return response

class LeaseDocumentDeleteView(LoginRequiredMixin, DeleteView):
    model = LeaseDocument
    template_name = 'documents/lease_document_confirm_delete.html'
    success_url = reverse_lazy('lease_document_list')

    def delete(self, request, *args, **kwargs):
        messages.success(self.request, 'Lease document deleted successfully.')
        return super().delete(request, *args, **kwargs)