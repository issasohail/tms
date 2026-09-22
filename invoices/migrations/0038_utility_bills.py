
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
