#!/usr/bin/env python3
from pathlib import Path
import shutil

ROOT=Path.cwd()
INV=ROOT/'invoices'
REQ=[INV/'models.py',INV/'urls.py',INV/'templates'/'invoices'/'iesco_bill_dashboard.html']
missing=[str(x) for x in REQ if not x.exists()]
if missing:
    raise SystemExit('Run from TMS project root. Missing: '+', '.join(missing))

def backup(p):
    b=p.with_name(p.name+'.bak-utility-bills')
    if p.exists() and not b.exists(): shutil.copy2(p,b)

def write(p,s):
    p.parent.mkdir(parents=True,exist_ok=True)
    p.write_text(s,encoding='utf-8')
    print('WROTE',p)

def append_once(p,marker,s):
    t=p.read_text(encoding='utf-8')
    if marker in t: return
    backup(p); p.write_text(t+'\n\n'+s.strip()+'\n',encoding='utf-8'); print('UPDATED',p)

def replace_once(p,old,new):
    t=p.read_text(encoding='utf-8')
    if new in t: return
    if old not in t: raise RuntimeError(f'Marker not found in {p}: {old!r}')
    backup(p); p.write_text(t.replace(old,new,1),encoding='utf-8'); print('UPDATED',p)

MODELS = r'''
class UtilityBillAccount(models.Model):
    PROVIDER_SNGPL = "sngpl"
    PROVIDER_PTCL = "ptcl"
    PROVIDER_CHOICES = ((PROVIDER_SNGPL, "SNGPL"), (PROVIDER_PTCL, "PTCL"))

    provider = models.CharField(max_length=12, choices=PROVIDER_CHOICES, db_index=True)
    unit = models.ForeignKey("properties.Unit", null=True, blank=True, on_delete=models.SET_NULL, related_name="utility_bill_accounts")
    description = models.CharField(max_length=255, blank=True, default="")
    consumer_number = models.CharField(max_length=32, blank=True, default="")
    ptcl_account_id = models.CharField(max_length=32, blank=True, default="")
    ptcl_phone = models.CharField(max_length=32, blank=True, default="")
    is_active = models.BooleanField(default=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["provider", "id"]
        permissions = [("fetch_utility_bill", "Can fetch utility bills")]

    @property
    def account_number(self):
        return self.consumer_number if self.provider == self.PROVIDER_SNGPL else self.ptcl_account_id

    @property
    def location_display(self):
        if self.unit_id:
            return f"{self.unit.property.property_name} / {self.unit.unit_number}"
        return self.description or "Standalone"

    def clean(self):
        from django.core.exceptions import ValidationError
        if self.provider == self.PROVIDER_SNGPL:
            self.consumer_number = re.sub(r"\D", "", self.consumer_number or "")
            if not self.consumer_number:
                raise ValidationError({"consumer_number":"SNGPL consumer number is required."})
            self.ptcl_account_id = ""; self.ptcl_phone = ""
        else:
            self.ptcl_phone = re.sub(r"\D", "", self.ptcl_phone or "")
            self.ptcl_account_id = (self.ptcl_account_id or "").strip()
            if not self.ptcl_account_id:
                raise ValidationError({"ptcl_account_id":"PTCL Account ID is required."})
            if len(self.ptcl_phone) < 7:
                raise ValidationError({"ptcl_phone":"Enter PTCL phone including area code."})
            self.consumer_number = ""

    def __str__(self):
        return f"{self.get_provider_display()} - {self.location_display} - {self.account_number}"


class UtilityBillReading(models.Model):
    account = models.ForeignKey(UtilityBillAccount, on_delete=models.CASCADE, related_name="readings")
    bill_month = models.CharField(max_length=20, db_index=True)
    issue_date = models.CharField(max_length=20, blank=True, default="")
    due_date = models.CharField(max_length=20, blank=True, default="")
    amount_due = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    amount_after_due = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    current_bill = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    arrears = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    previous_reading = models.DecimalField(max_digits=14, decimal_places=3, null=True, blank=True)
    current_reading = models.DecimalField(max_digits=14, decimal_places=3, null=True, blank=True)
    consumption = models.DecimalField(max_digits=14, decimal_places=3, null=True, blank=True)
    consumption_unit = models.CharField(max_length=20, blank=True, default="")
    invoice_number = models.CharField(max_length=40, blank=True, default="")
    customer_name = models.CharField(max_length=255, blank=True, default="")
    address = models.TextField(blank=True, default="")
    service_details = models.JSONField(default=dict, blank=True)
    bill_history = models.JSONField(default=list, blank=True)
    raw_data = models.JSONField(default=dict, blank=True)
    fetched_at = models.DateTimeField(default=timezone.now)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-fetched_at", "-id"]
        constraints = [models.UniqueConstraint(fields=("account","bill_month"), name="uniq_utility_account_bill_month")]

    def __str__(self):
        return f"{self.account} - {self.bill_month}"
'''
append_once(INV/'models.py','class UtilityBillAccount(models.Model):',MODELS)

FORMS = r'''
from django import forms
from properties.models import Unit
from .models import UtilityBillAccount

class UtilityBillAccountForm(forms.ModelForm):
    class Meta:
        model = UtilityBillAccount
        fields = ("provider","unit","description","consumer_number","ptcl_account_id","ptcl_phone","is_active")
        widgets = {
            "provider": forms.HiddenInput(),
            "unit": forms.Select(attrs={"class":"form-select"}),
            "description": forms.TextInput(attrs={"class":"form-control"}),
            "consumer_number": forms.TextInput(attrs={"class":"form-control"}),
            "ptcl_account_id": forms.TextInput(attrs={"class":"form-control"}),
            "ptcl_phone": forms.TextInput(attrs={"class":"form-control","placeholder":"0515921709"}),
            "is_active": forms.CheckboxInput(attrs={"class":"form-check-input"}),
        }

    def __init__(self,*args,provider=None,**kwargs):
        super().__init__(*args,**kwargs)
        self.fields["unit"].queryset = Unit.objects.select_related("property").order_by("property__property_name","unit_number")
        self.fields["unit"].required=False
        if provider: self.fields["provider"].initial=provider
'''
write(INV/'forms_utility.py',FORMS)

FETCH = r'''
import base64, re
from decimal import Decimal, InvalidOperation
from urllib.parse import urljoin
import requests
from bs4 import BeautifulSoup
from django.core.exceptions import ValidationError
from django.utils import timezone
from .models import UtilityBillReading

HEADERS={"User-Agent":"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/153.0.0.0 Safari/537.36"}
SNGPL_URL="https://www.sngpl.com.pk/viewbill?artcl=artuyh709123465&cats=ct456712337&client=ANDROID&consumer={consumer}&contype=NewCon&mdids=85&pgname=PAGES_NAME&proc=viewbill&secs=ss7xa852op845"
PTCL_URL="https://dbill.ptcl.net.pk/PTCLSearchInvoice.aspx"

def money(v):
    c=re.sub(r"[^0-9.\-]","",str(v or "").replace(",",""))
    if c in ("","-",".","-."): return None
    try: return Decimal(c)
    except InvalidOperation: return None

def first(p,t,default=""):
    m=re.search(p,t,re.I|re.S)
    return m.group(1).strip() if m else default

def fetch_sngpl(account):
    consumer=re.sub(r"\D","",account.consumer_number or "")
    s=requests.Session(); s.headers.update(HEADERS)
    r=s.get(SNGPL_URL.format(consumer=consumer),timeout=35); r.raise_for_status()
    soup=BeautifulSoup(r.text,"html.parser"); text=soup.get_text(" ",strip=True)
    if consumer not in text: raise ValidationError("SNGPL did not return this consumer number.")
    month=first(rf"{re.escape(consumer)}\s+.*?([A-Za-z]{{3}}\s+\d{{4}})",text) or first(r"([A-Za-z]{3}\s+\d{4})",text)
    m=re.search(r"Dates:\s*([0-9-]+)\s*([0-9-]+).*?Reading:\s*([0-9.]+)\s*([0-9.]+)\s*([0-9.]+)",text,re.I|re.S)
    cr=pr=diff=None; crd=prd=""
    if m:
        crd,prd=m.group(1),m.group(2); cr,pr,diff=money(m.group(3)),money(m.group(4)),money(m.group(5))
    def lm(label,nextlabel=None):
        p=rf"{label}.*?(-?[\d,]+(?:\.\d+)?)"
        if nextlabel: p += rf"\s+{nextlabel}"
        return money(first(p,text))
    current_bill=lm("Current Bill","Arrears")
    arrears=lm(r"Arrears\s*/\s*Aging","Late Payment")
    total=lm("Total Amount Due")
    hm3=money(first(r"Gas Consumed HM3\s*([0-9.]+)",text))
    service={
        "gas_charges": str(lm("Gas Charges","Prov") or ""),
        "meter_rent": str(lm("Meter Rent","Fixed Charges") or ""),
        "gst": str(lm("GST","Rebate") or ""),
        "mmbtu": first(r"\*MMBTU\s*([0-9.]+)",text),
        "gcv": first(r"GCV\s*([0-9.]+)",text),
        "current_reading_date":crd,"previous_reading_date":prd,
    }
    hist=[]
    for table in soup.find_all("table"):
        rows=table.find_all("tr")
        for row in rows:
            vals=[c.get_text(" ",strip=True) for c in row.find_all(["th","td"])]
            if len(vals)>=5 and re.match(r"^[A-Za-z]{3}\s+\d{4}$",vals[0] or ""):
                hist.append({"month":vals[0],"hm3":vals[1],"current_bill":vals[2],"amount_due":vals[3],"payment":vals[4]})
    if not month: raise ValidationError("Could not identify SNGPL billing month.")
    obj,_=UtilityBillReading.objects.update_or_create(account=account,bill_month=month,defaults={
        "issue_date":first(r"Issue Date:\s*([0-9-]+)",text),
        "due_date":first(r"Due Date:?\s*([0-9-]+)",text),
        "amount_due":total,"current_bill":current_bill,"arrears":arrears,
        "previous_reading":pr,"current_reading":cr,"consumption":hm3 if hm3 is not None else diff,
        "consumption_unit":"HM3","service_details":service,"bill_history":hist,
        "raw_data":{"consumer_number":consumer},"fetched_at":timezone.now(),
    })
    return obj

def start_ptcl_captcha(account):
    s=requests.Session(); s.headers.update(HEADERS)
    r=s.get(PTCL_URL,timeout=35); r.raise_for_status(); soup=BeautifulSoup(r.text,"html.parser")
    hidden={}
    for x in soup.find_all("input",{"type":"hidden"}):
        if x.get("name"): hidden[x["name"]]=x.get("value","")
    cap=None
    for img in soup.find_all("img"):
        src=img.get("src") or ""
        if "captcha" in src.lower(): cap=urljoin(PTCL_URL,src); break
    if not cap: raise ValidationError("PTCL captcha image not found.")
    ir=s.get(cap,timeout=35); ir.raise_for_status()
    data=f"data:{ir.headers.get('Content-Type','image/jpeg')};base64,{base64.b64encode(ir.content).decode()}"
    return {"hidden":hidden,"cookies":requests.utils.dict_from_cookiejar(s.cookies),"image":data}

def finish_ptcl(account,captcha,state):
    s=requests.Session(); s.headers.update(HEADERS); s.cookies.update(state.get("cookies") or {})
    digits=re.sub(r"\D","",account.ptcl_phone or "")
    phone=digits[3:] if digits.startswith("0") and len(digits)>=10 else digits
    payload=dict(state.get("hidden") or {})
    payload.update({
        "ctl00$ContentPlaceHolder1$txtPhoneNo":phone,
        "ctl00$ContentPlaceHolder1$txtAccountID":account.ptcl_account_id,
        "ctl00$ContentPlaceHolder1$txtCaptcha":(captcha or "").strip(),
        "ctl00$ContentPlaceHolder1$btnVerify":"Search",
    })
    r=s.post(PTCL_URL,data=payload,timeout=35,allow_redirects=True); r.raise_for_status()
    text=BeautifulSoup(r.text,"html.parser").get_text(" ",strip=True)
    if "Billing Month" not in text: raise ValidationError("PTCL captcha was rejected or no bill was returned.")
    month=first(r"Billing Month\s*([A-Za-z]{3}-\d{4})",text)
    if not month: raise ValidationError("Could not identify PTCL billing month.")
    labels={
      "bundle":r"Bundle\s*Rs\.\s*([\d,.-]+)",
      "tv":r"TV\s*Rs\.\s*([\d,.-]+)",
      "vas_paper_bill":r"VAS/Paper Bill\s*Rs\.\s*([\d,.-]+)",
      "total_service_charges":r"Total Service Charges\s*Rs\.\s*([\d,.-]+)",
      "arrears":r"Arrears\s*Rs\.\s*([\d,.-]+)",
      "credit":r"Credit\s*Rs\.\s*([\d,.-]+)",
      "sales_tax":r"FED/Sales Tax\s*Rs\.\s*([\d,.-]+)",
      "withholding_tax":r"W\.H\.Tax\s*Rs\.\s*([\d,.-]+)",
      "late_payment_surcharge":r"Late Pay Surcharge\s*Rs\.\s*([\d,.-]+)",
      "grand_total":r"Grand Total\s*Rs\.\s*([\d,.-]+)",
    }
    service={}
    for k,p in labels.items():
        v=money(first(p,text)); service[k]=str(v) if v is not None else ""
    service["package_speed"]=first(r"(\d+\s*Mbps)\s+01-31",text)
    u=re.search(r"Usage:\s*([\d.]+)\s*/\s*([\d.]+)\s*GB",text,re.I)
    if u: service.update({"usage_gb":u.group(1),"usage_limit_gb":u.group(2)})
    due=money(first(r"Amount Due\s*Rs\.\s*([\d,.-]+)",text))
    after=money(first(r"Amount After Due Date\s*Rs\.\s*([\d,.-]+)",text))
    arrears=money(service.get("arrears"))
    obj,_=UtilityBillReading.objects.update_or_create(account=account,bill_month=month,defaults={
       "issue_date":first(r"Issue Date:\s*([0-9-]+)",text),
       "due_date":first(r"Due Date\s*([0-9-]+)",text),
       "amount_due":due,"amount_after_due":after,"current_bill":due,"arrears":arrears,
       "invoice_number":first(r"Invoice\s*#\s*([0-9]+)",text),
       "service_details":service,"raw_data":{"final_url":r.url},"fetched_at":timezone.now(),
    })
    return obj
'''
write(INV/'utility_bill_fetch.py',FETCH)

VIEWS = r'''
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
'''
write(INV/'views_utility.py',VIEWS)

replace_once(INV/'urls.py',"from . import views_iesco_helper","from . import views_iesco_helper\nfrom . import views_utility")
marker="    path('create/', InvoiceCreateView.as_view(), name='invoice_create'),"
urls='''    path("utility-bills/<str:provider>/", views_utility.utility_bill_list, name="utility_bill_list"),
    path("utility-bills/<str:provider>/accounts/add/", views_utility.utility_account_form, name="utility_account_add"),
    path("utility-bills/<str:provider>/accounts/<int:pk>/edit/", views_utility.utility_account_form, name="utility_account_edit"),
    path("utility-bills/<str:provider>/accounts/<int:pk>/delete/", views_utility.utility_account_delete, name="utility_account_delete"),
    path("utility-bills/<str:provider>/accounts/<int:pk>/fetch/", views_utility.fetch_one, name="utility_fetch_one"),
    path("utility-bills/<str:provider>/fetch-all/", views_utility.fetch_all, name="utility_fetch_all"),
    path("utility-bills/ptcl/accounts/<int:pk>/captcha/", views_utility.ptcl_submit, name="ptcl_captcha_submit"),
    path("utility-bills/readings/<int:pk>/", views_utility.detail, name="utility_bill_detail"),

'''+marker
replace_once(INV/'urls.py',marker,urls)

MIG = r'''
from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone

class Migration(migrations.Migration):
    dependencies=[("properties","0034_unit_iesco_bill_active"),("invoices","0037_iesco_helper_fetch_run")]
    operations=[
      migrations.CreateModel(name="UtilityBillAccount",fields=[
        ("id",models.BigAutoField(auto_created=True,primary_key=True,serialize=False,verbose_name="ID")),
        ("provider",models.CharField(choices=[("sngpl","SNGPL"),("ptcl","PTCL")],db_index=True,max_length=12)),
        ("description",models.CharField(blank=True,default="",max_length=255)),
        ("consumer_number",models.CharField(blank=True,default="",max_length=32)),
        ("ptcl_account_id",models.CharField(blank=True,default="",max_length=32)),
        ("ptcl_phone",models.CharField(blank=True,default="",max_length=32)),
        ("is_active",models.BooleanField(db_index=True,default=True)),
        ("created_at",models.DateTimeField(auto_now_add=True)),("updated_at",models.DateTimeField(auto_now=True)),
        ("unit",models.ForeignKey(blank=True,null=True,on_delete=django.db.models.deletion.SET_NULL,related_name="utility_bill_accounts",to="properties.unit")),
      ],options={"ordering":["provider","id"],"permissions":[("fetch_utility_bill","Can fetch utility bills")]}),
      migrations.CreateModel(name="UtilityBillReading",fields=[
        ("id",models.BigAutoField(auto_created=True,primary_key=True,serialize=False,verbose_name="ID")),
        ("bill_month",models.CharField(db_index=True,max_length=20)),("issue_date",models.CharField(blank=True,default="",max_length=20)),
        ("due_date",models.CharField(blank=True,default="",max_length=20)),
        ("amount_due",models.DecimalField(blank=True,decimal_places=2,max_digits=12,null=True)),
        ("amount_after_due",models.DecimalField(blank=True,decimal_places=2,max_digits=12,null=True)),
        ("current_bill",models.DecimalField(blank=True,decimal_places=2,max_digits=12,null=True)),
        ("arrears",models.DecimalField(blank=True,decimal_places=2,max_digits=12,null=True)),
        ("previous_reading",models.DecimalField(blank=True,decimal_places=3,max_digits=14,null=True)),
        ("current_reading",models.DecimalField(blank=True,decimal_places=3,max_digits=14,null=True)),
        ("consumption",models.DecimalField(blank=True,decimal_places=3,max_digits=14,null=True)),
        ("consumption_unit",models.CharField(blank=True,default="",max_length=20)),
        ("invoice_number",models.CharField(blank=True,default="",max_length=40)),
        ("customer_name",models.CharField(blank=True,default="",max_length=255)),("address",models.TextField(blank=True,default="")),
        ("service_details",models.JSONField(blank=True,default=dict)),("bill_history",models.JSONField(blank=True,default=list)),
        ("raw_data",models.JSONField(blank=True,default=dict)),("fetched_at",models.DateTimeField(default=django.utils.timezone.now)),
        ("created_at",models.DateTimeField(auto_now_add=True)),("updated_at",models.DateTimeField(auto_now=True)),
        ("account",models.ForeignKey(on_delete=django.db.models.deletion.CASCADE,related_name="readings",to="invoices.utilitybillaccount")),
      ],options={"ordering":["-fetched_at","-id"]}),
      migrations.AddConstraint(model_name="utilitybillreading",constraint=models.UniqueConstraint(fields=("account","bill_month"),name="uniq_utility_account_bill_month")),
    ]
'''
write(INV/'migrations'/'0038_utility_bills.py',MIG)

TABS=r'''<ul class="nav nav-tabs mb-3">
<li class="nav-item"><a class="nav-link {% if not provider %}active{% endif %}" href="{% url 'invoices:iesco_bill_reading_list' %}">Electricity - IESCO</a></li>
<li class="nav-item"><a class="nav-link {% if provider == 'sngpl' %}active{% endif %}" href="{% url 'invoices:utility_bill_list' 'sngpl' %}">Gas - SNGPL</a></li>
<li class="nav-item"><a class="nav-link {% if provider == 'ptcl' %}active{% endif %}" href="{% url 'invoices:utility_bill_list' 'ptcl' %}">PTCL</a></li>
</ul>'''
write(INV/'templates'/'invoices'/'_utility_tabs.html',TABS)

DASH=r'''{% extends "base.html" %}
{% block title %}{{ provider_label }} Bills{% endblock %}
{% block main_container_class %}container-fluid{% endblock %}
{% block content %}
<div style="max-width:1700px;margin:auto">
{% include "invoices/_utility_tabs.html" %}
<div class="d-flex flex-wrap justify-content-between align-items-center gap-2 mb-3">
<div><h4 class="mb-0">{{ provider_label }} Bills</h4><small class="text-muted">Current bills and history</small></div>
<div class="d-flex gap-2">
{% if can_manage %}<a class="btn btn-primary btn-sm" href="{% url 'invoices:utility_account_add' provider %}">Add Account</a>{% endif %}
{% if provider == 'sngpl' and can_fetch %}<form method="post" action="{% url 'invoices:utility_fetch_all' provider %}">{% csrf_token %}<button class="btn btn-success btn-sm">Fetch All Active</button></form>{% endif %}
<a class="btn btn-outline-secondary btn-sm" href="?{% if not show_history %}show_history=1{% endif %}">{% if show_history %}Current View{% else %}Show History{% endif %}</a>
</div></div>
<form method="get" class="card card-body py-2 mb-3"><div class="row g-2">
<div class="col-md-4"><select name="property" class="form-select form-select-sm" onchange="this.form.submit()"><option value="">All properties</option>{% for p in properties %}<option value="{{p.pk}}" {% if selected_property == p.pk|stringformat:"s" %}selected{% endif %}>{{p.property_name}}</option>{% endfor %}</select></div>
<div class="col-md-4"><select name="unit" class="form-select form-select-sm" onchange="this.form.submit()"><option value="">All units</option>{% for u in units %}<option value="{{u.pk}}" {% if selected_unit == u.pk|stringformat:"s" %}selected{% endif %}>{{u.property.property_name}} / {{u.unit_number}}</option>{% endfor %}</select></div>
{% if show_history %}<input type="hidden" name="show_history" value="1">{% endif %}</div></form>
<div class="table-responsive"><table class="table table-bordered table-hover table-sm align-middle" style="font-size:.82rem">
<thead class="table-light"><tr><th>#</th><th>Property / Unit</th><th>Active</th><th>Account</th><th>Bill Month</th>
{% if provider == 'sngpl' %}<th>Previous</th><th>Current</th><th>Consumption</th>{% else %}<th>Invoice #</th><th>Package / Usage</th>{% endif %}
<th>Current Bill</th><th>Arrears</th><th>Total Due</th><th>Due Date</th><th>Last Update</th><th>Actions</th></tr></thead>
<tbody>{% for row in rows %}{% with a=row.account r=row.reading %}<tr>
<td>{{forloop.counter}}</td><td><strong>{{a.location_display}}</strong>{% if a.description %}<br><small class="text-muted">{{a.description}}</small>{% endif %}</td>
<td>{% if a.is_active %}<span class="badge bg-success">Active</span>{% else %}<span class="badge bg-secondary">Inactive</span>{% endif %}</td>
<td>{% if provider == 'sngpl' %}{{a.consumer_number}}{% else %}{{a.ptcl_account_id}}<br><small>{{a.ptcl_phone}}</small>{% endif %}</td>
<td>{{r.bill_month|default:"-"}}</td>
{% if provider == 'sngpl' %}<td>{{r.previous_reading|default_if_none:"-"}}</td><td>{{r.current_reading|default_if_none:"-"}}</td><td>{{r.consumption|default_if_none:"-"}} {{r.consumption_unit}}</td>
{% else %}<td>{{r.invoice_number|default:"-"}}</td><td>{% if r %}{{r.service_details.package_speed|default:"-"}}{% if r.service_details.usage_gb %}<br><small>{{r.service_details.usage_gb}} / {{r.service_details.usage_limit_gb}} GB</small>{% endif %}{% else %}-{% endif %}</td>{% endif %}
<td>{% if r.current_bill != None %}Rs. {{r.current_bill|floatformat:2}}{% else %}-{% endif %}</td>
<td>{% if r.arrears != None %}Rs. {{r.arrears|floatformat:2}}{% else %}-{% endif %}</td>
<td>{% if r.amount_due != None %}<strong>Rs. {{r.amount_due|floatformat:2}}</strong>{% else %}-{% endif %}</td>
<td>{{r.due_date|default:"-"}}</td><td>{% if r %}{{r.fetched_at|date:"d M Y H:i"}}{% else %}-{% endif %}</td>
<td><div class="d-flex flex-wrap gap-1">
{% if can_fetch %}<form method="post" action="{% url 'invoices:utility_fetch_one' provider a.pk %}">{% csrf_token %}<button class="btn btn-success btn-sm">Fetch{% if provider == 'ptcl' %} / Captcha{% endif %}</button></form>{% endif %}
{% if r %}<a class="btn btn-outline-primary btn-sm" href="{% url 'invoices:utility_bill_detail' r.pk %}">View</a>{% endif %}
{% if can_manage %}<a class="btn btn-outline-secondary btn-sm" href="{% url 'invoices:utility_account_edit' provider a.pk %}">Edit</a>
<form method="post" action="{% url 'invoices:utility_account_delete' provider a.pk %}" onsubmit="return confirm('Delete account and saved history?')">{% csrf_token %}<button class="btn btn-outline-danger btn-sm">Delete</button></form>{% endif %}
</div></td></tr>{% endwith %}{% empty %}<tr><td colspan="14" class="text-center text-muted py-4">No {{provider_label}} accounts added yet.</td></tr>{% endfor %}</tbody></table></div>
</div>
{% endblock %}'''
write(INV/'templates'/'invoices'/'utility_bill_dashboard.html',DASH)

FORM=r'''{% extends "base.html" %}{% block content %}<div class="container" style="max-width:800px">{% include "invoices/_utility_tabs.html" %}<div class="card"><div class="card-header"><strong>{% if object %}Edit{% else %}Add{% endif %} {{provider_label}} Account</strong></div><div class="card-body">
<form method="post">{% csrf_token %}{{form.provider}}<div class="row g-3">
<div class="col-md-6"><label class="form-label">Unit</label>{{form.unit}}{{form.unit.errors}}</div><div class="col-md-6"><label class="form-label">Description</label>{{form.description}}{{form.description.errors}}</div>
{% if provider == 'sngpl' %}<div class="col-md-6"><label class="form-label">SNGPL Consumer Number</label>{{form.consumer_number}}{{form.consumer_number.errors}}</div>
{% else %}<div class="col-md-6"><label class="form-label">PTCL Account ID</label>{{form.ptcl_account_id}}{{form.ptcl_account_id.errors}}</div><div class="col-md-6"><label class="form-label">PTCL Phone with area code</label>{{form.ptcl_phone}}{{form.ptcl_phone.errors}}</div>{% endif %}
<div class="col-12"><div class="form-check">{{form.is_active}} <label class="form-check-label">Active</label></div></div></div>
<div class="mt-3"><button class="btn btn-primary">Save</button> <a class="btn btn-outline-secondary" href="{% url 'invoices:utility_bill_list' provider %}">Cancel</a></div></form>
</div></div></div>{% endblock %}'''
write(INV/'templates'/'invoices'/'utility_account_form.html',FORM)

CAP=r'''{% extends "base.html" %}{% block content %}<div class="container" style="max-width:650px">{% include "invoices/_utility_tabs.html" %}<div class="card"><div class="card-header"><strong>PTCL Security Verification</strong></div><div class="card-body">
<p><strong>{{account.location_display}}</strong><br><small>Account {{account.ptcl_account_id}} - {{account.ptcl_phone}}</small></p>
<div class="border rounded p-3 text-center bg-light mb-3"><img src="{{captcha_data_url}}" alt="PTCL captcha" style="max-width:100%"></div>
<form method="post" action="{% url 'invoices:ptcl_captcha_submit' account.pk %}">{% csrf_token %}<label class="form-label">Enter captcha</label><input name="captcha" class="form-control form-control-lg" required autofocus autocomplete="off"><div class="mt-3"><button class="btn btn-success">Fetch Bill</button> <a class="btn btn-outline-secondary" href="{% url 'invoices:utility_bill_list' 'ptcl' %}">Cancel</a></div></form>
</div></div></div>{% endblock %}'''
write(INV/'templates'/'invoices'/'ptcl_captcha.html',CAP)

DETAIL=r'''{% extends "base.html" %}{% block content %}<div class="container" style="max-width:1100px">{% include "invoices/_utility_tabs.html" %}<h4>{{reading.account.get_provider_display}} - {{reading.bill_month}}</h4><p class="text-muted">{{reading.account.location_display}}</p>
<div class="row g-3"><div class="col-md-6"><div class="card"><div class="card-header fw-bold">Bill Summary</div><div class="card-body"><table class="table table-sm">
<tr><th>Account</th><td>{{reading.account.account_number}}</td></tr>{% if reading.account.provider == 'ptcl' %}<tr><th>Phone</th><td>{{reading.account.ptcl_phone}}</td></tr><tr><th>Invoice #</th><td>{{reading.invoice_number}}</td></tr>{% endif %}
<tr><th>Issue Date</th><td>{{reading.issue_date}}</td></tr><tr><th>Due Date</th><td>{{reading.due_date}}</td></tr><tr><th>Current Bill</th><td>{{reading.current_bill}}</td></tr><tr><th>Arrears</th><td>{{reading.arrears}}</td></tr><tr><th>Total Due</th><td>{{reading.amount_due}}</td></tr>{% if reading.amount_after_due != None %}<tr><th>After Due</th><td>{{reading.amount_after_due}}</td></tr>{% endif %}
</table></div></div></div><div class="col-md-6"><div class="card"><div class="card-header fw-bold">Details</div><div class="card-body"><table class="table table-sm">
{% if reading.account.provider == 'sngpl' %}<tr><th>Previous Reading</th><td>{{reading.previous_reading}}</td></tr><tr><th>Current Reading</th><td>{{reading.current_reading}}</td></tr><tr><th>Consumption</th><td>{{reading.consumption}} {{reading.consumption_unit}}</td></tr>{% endif %}
{% for k,v in reading.service_details.items %}<tr><th>{{k|title}}</th><td>{{v}}</td></tr>{% endfor %}</table></div></div></div></div>
{% if reading.bill_history %}<div class="card mt-3"><div class="card-header fw-bold">SNGPL History Returned With Bill</div><div class="card-body"><div class="table-responsive"><table class="table table-sm table-bordered"><tr><th>Month</th><th>HM3</th><th>Current Bill</th><th>Amount Due</th><th>Payment</th></tr>{% for h in reading.bill_history %}<tr><td>{{h.month}}</td><td>{{h.hm3}}</td><td>{{h.current_bill}}</td><td>{{h.amount_due}}</td><td>{{h.payment}}</td></tr>{% endfor %}</table></div></div></div>{% endif %}
</div>{% endblock %}'''
write(INV/'templates'/'invoices'/'utility_bill_detail.html',DETAIL)

replace_once(INV/'templates'/'invoices'/'iesco_bill_dashboard.html','{% block content %}\n<style>','{% block content %}\n{% include "invoices/_utility_tabs.html" %}\n<style>')

print('\nPATCH APPLIED')
print('Next commands:')
print('  py manage.py migrate')
print('  py manage.py check')
print('  py manage.py test invoices.test_iesco_ingest')
