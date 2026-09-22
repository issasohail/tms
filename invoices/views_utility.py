
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.http import Http404
from django.views.decorators.http import require_POST
from .forms_utility import UtilityBillAccountForm
from .models import UtilityBillAccount, UtilityBillReading
from .utility_bill_fetch import fetch_sngpl, start_ptcl_captcha, finish_ptcl

def valid_provider(p):
    if p not in ("sngpl","ptcl"): raise Http404("Unknown utility provider")
    return p

def can_manage(u):
    return u.is_superuser or u.has_perm("invoices.change_utilitybillaccount") or u.has_perm("invoices.change_iescobillreading")

def can_fetch(u):
    return u.is_superuser or u.has_perm("invoices.fetch_utility_bill") or u.has_perm("invoices.change_utilitybillreading") or u.has_perm("invoices.change_iescobillreading")

@login_required
def utility_bill_list(request,provider):
    provider=valid_provider(provider)
    show_history=request.GET.get("show_history")=="1"
    qs=UtilityBillAccount.objects.filter(provider=provider).select_related("unit__property")
    pid=(request.GET.get("property") or "").strip(); uid=(request.GET.get("unit") or "").strip()
    if pid: qs=qs.filter(unit__property_id=pid)
    if uid: qs=qs.filter(unit_id=uid)
    rows=[]
    for a in qs:
        readings=list(a.readings.all().order_by("-fetched_at","-id"))
        if show_history and readings:
            rows.extend({"account":a,"reading":r} for r in readings)
        else:
            rows.append({"account":a,"reading":readings[0] if readings else None})
    from properties.models import Property, Unit
    props=Property.objects.order_by("property_name")
    units=Unit.objects.select_related("property").order_by("property__property_name","unit_number")
    if pid: units=units.filter(property_id=pid)
    return render(request,"invoices/utility_bill_dashboard.html",{
      "provider":provider,"provider_label":"SNGPL" if provider=="sngpl" else "PTCL",
      "rows":rows,"properties":props,"units":units,"selected_property":pid,"selected_unit":uid,
      "show_history":show_history,"can_manage":can_manage(request.user),"can_fetch":can_fetch(request.user)
    })

@login_required
def utility_account_form(request,provider,pk=None):
    provider=valid_provider(provider)
    if not can_manage(request.user):
        messages.error(request,"You do not have permission to edit utility accounts.")
        return redirect("invoices:utility_bill_list",provider=provider)
    obj=get_object_or_404(UtilityBillAccount,pk=pk,provider=provider) if pk else None
    if request.method=="POST":
        form=UtilityBillAccountForm(request.POST,instance=obj,provider=provider)
        if form.is_valid():
            x=form.save(commit=False); x.provider=provider; x.full_clean(); x.save()
            messages.success(request,"Utility account saved.")
            return redirect("invoices:utility_bill_list",provider=provider)
    else:
        form=UtilityBillAccountForm(instance=obj,provider=provider,initial={"provider":provider})
    return render(request,"invoices/utility_account_form.html",{"form":form,"provider":provider,"provider_label":"SNGPL" if provider=="sngpl" else "PTCL","object":obj})

@require_POST
@login_required
def utility_account_delete(request,provider,pk):
    provider=valid_provider(provider)
    if not can_manage(request.user):
        messages.error(request,"Permission denied."); return redirect("invoices:utility_bill_list",provider=provider)
    get_object_or_404(UtilityBillAccount,pk=pk,provider=provider).delete()
    messages.success(request,"Utility account deleted.")
    return redirect("invoices:utility_bill_list",provider=provider)

@require_POST
@login_required
def fetch_one(request,provider,pk):
    provider=valid_provider(provider)
    if not can_fetch(request.user):
        messages.error(request,"Permission denied."); return redirect("invoices:utility_bill_list",provider=provider)
    a=get_object_or_404(UtilityBillAccount,pk=pk,provider=provider)
    if provider=="ptcl":
        try:
            st=start_ptcl_captcha(a)
            request.session[f"ptcl_fetch_{a.pk}"]={"hidden":st["hidden"],"cookies":st["cookies"]}
            return render(request,"invoices/ptcl_captcha.html",{"account":a,"captcha_data_url":st["image"]})
        except Exception as e:
            messages.error(request,f"PTCL captcha could not be loaded: {e}")
    else:
        try:
            r=fetch_sngpl(a); messages.success(request,f"SNGPL bill fetched: {r.bill_month}.")
        except Exception as e: messages.error(request,f"SNGPL fetch failed: {e}")
    return redirect("invoices:utility_bill_list",provider=provider)

@require_POST
@login_required
def fetch_all(request,provider):
    provider=valid_provider(provider)
    if provider=="ptcl":
        messages.info(request,"PTCL requires one captcha per account."); return redirect("invoices:utility_bill_list",provider=provider)
    if not can_fetch(request.user):
        messages.error(request,"Permission denied."); return redirect("invoices:utility_bill_list",provider=provider)
    ok=bad=0
    for a in UtilityBillAccount.objects.filter(provider="sngpl",is_active=True):
        try: fetch_sngpl(a); ok+=1
        except Exception: bad+=1
    messages.success(request,f"Fetched {ok} SNGPL bill(s); {bad} failed.")
    return redirect("invoices:utility_bill_list",provider=provider)

@require_POST
@login_required
def ptcl_submit(request,pk):
    a=get_object_or_404(UtilityBillAccount,pk=pk,provider="ptcl")
    state=request.session.pop(f"ptcl_fetch_{a.pk}",None)
    if not state:
        messages.error(request,"PTCL captcha session expired. Load a new captcha.")
    else:
        try:
            r=finish_ptcl(a,request.POST.get("captcha"),state); messages.success(request,f"PTCL bill fetched: {r.bill_month}.")
        except Exception as e: messages.error(request,f"PTCL fetch failed: {e}")
    return redirect("invoices:utility_bill_list",provider="ptcl")

@login_required
def detail(request,pk):
    r=get_object_or_404(UtilityBillReading.objects.select_related("account__unit__property"),pk=pk)
    return render(request,"invoices/utility_bill_detail.html",{"reading":r})
