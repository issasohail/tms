
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
