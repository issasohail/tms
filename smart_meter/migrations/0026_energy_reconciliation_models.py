import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("smart_meter", "0025_meter_measurement_point"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="EnergySystem",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=80)),
                ("output_meter_includes_grid_export", models.BooleanField(blank=True, null=True)),
                ("notes", models.TextField(blank=True)),
                ("is_active", models.BooleanField(default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("output_group", models.OneToOneField(on_delete=django.db.models.deletion.PROTECT, related_name="energy_system", to="smart_meter.metercheckgroup")),
                ("grid_interface_meter", models.ForeignKey(blank=True, limit_choices_to={"measurement_point": "grid_interface", "meter_role": "check"}, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="energy_systems_as_grid_interface", to="smart_meter.meter")),
            ],
        ),
        migrations.AddField(
            model_name="metercheckgroup",
            name="superseded_by_energy_system",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="superseded_input_groups", to="smart_meter.energysystem"),
        ),
        migrations.CreateModel(
            name="EnergySystemMeterAssignment",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("role", models.CharField(choices=[("grid_interface", "Grid Interface"), ("output", "Inverter Output")], max_length=20)),
                ("start_date", models.DateField()),
                ("end_date", models.DateField(blank=True, null=True)),
                ("notes", models.TextField(blank=True)),
                ("changed_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to=settings.AUTH_USER_MODEL)),
                ("energy_system", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="meter_assignments", to="smart_meter.energysystem")),
                ("meter", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="energy_system_assignments", to="smart_meter.meter")),
            ],
            options={
                "constraints": [
                    models.CheckConstraint(condition=models.Q(("end_date__isnull", True), ("end_date__gt", models.F("start_date")), _connector="OR"), name="energy_system_assignment_end_after_start"),
                    models.UniqueConstraint(condition=models.Q(("end_date__isnull", True)), fields=("energy_system", "role"), name="one_open_energy_system_assignment_per_role"),
                ],
            },
        ),
        migrations.CreateModel(
            name="UtilityConnection",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("consumer_id", models.CharField(db_index=True, max_length=30, unique=True)),
                ("reference_no", models.CharField(blank=True, max_length=30)),
                ("dg_capacity_kw", models.DecimalField(blank=True, decimal_places=2, max_digits=6, null=True)),
                ("property_label", models.CharField(blank=True, max_length=80)),
                ("energy_system", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="utility_connection", to="smart_meter.energysystem")),
            ],
        ),
        migrations.CreateModel(
            name="UtilityBillCycle",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("bill_month", models.CharField(max_length=10)),
                ("period_start", models.DateField(blank=True, null=True)),
                ("period_end", models.DateField(blank=True, null=True)),
                ("reading_date", models.DateField(blank=True, null=True)),
                ("issue_date", models.DateField(blank=True, null=True)),
                ("due_date", models.DateField(blank=True, null=True)),
                ("import_off_peak_previous", models.DecimalField(blank=True, decimal_places=3, max_digits=12, null=True)),
                ("import_off_peak_current", models.DecimalField(blank=True, decimal_places=3, max_digits=12, null=True)),
                ("import_off_peak_kwh", models.DecimalField(decimal_places=3, default=0, max_digits=10)),
                ("import_peak_previous", models.DecimalField(blank=True, decimal_places=3, max_digits=12, null=True)),
                ("import_peak_current", models.DecimalField(blank=True, decimal_places=3, max_digits=12, null=True)),
                ("import_peak_kwh", models.DecimalField(decimal_places=3, default=0, max_digits=10)),
                ("export_off_peak_previous", models.DecimalField(blank=True, decimal_places=3, max_digits=12, null=True)),
                ("export_off_peak_current", models.DecimalField(blank=True, decimal_places=3, max_digits=12, null=True)),
                ("export_off_peak_kwh", models.DecimalField(decimal_places=3, default=0, max_digits=10)),
                ("export_peak_previous", models.DecimalField(blank=True, decimal_places=3, max_digits=12, null=True)),
                ("export_peak_current", models.DecimalField(blank=True, decimal_places=3, max_digits=12, null=True)),
                ("export_peak_kwh", models.DecimalField(decimal_places=3, default=0, max_digits=10)),
                ("total_electricity_charges", models.DecimalField(blank=True, decimal_places=2, max_digits=12, null=True)),
                ("taxes", models.DecimalField(blank=True, decimal_places=2, max_digits=12, null=True)),
                ("current_bill", models.DecimalField(blank=True, decimal_places=2, max_digits=12, null=True)),
                ("arrears", models.DecimalField(decimal_places=2, default=0, max_digits=12)),
                ("total_fpa", models.DecimalField(decimal_places=2, default=0, max_digits=12)),
                ("grand_total", models.DecimalField(blank=True, decimal_places=2, max_digits=12, null=True)),
                ("status", models.CharField(choices=[("draft", "Draft"), ("incomplete", "Incomplete"), ("reconciled", "Reconciled"), ("final", "Final")], default="draft", max_length=12)),
                ("attachment", models.FileField(upload_to="utility_bills/%Y/%m/")),
                ("extracted_raw", models.JSONField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("confirmed_at", models.DateTimeField(blank=True, null=True)),
                ("finalized_at", models.DateTimeField(blank=True, null=True)),
                ("created_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to=settings.AUTH_USER_MODEL)),
                ("updated_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="+", to=settings.AUTH_USER_MODEL)),
                ("utility_connection", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="bill_cycles", to="smart_meter.utilityconnection")),
            ],
            options={
                "constraints": [
                    models.CheckConstraint(condition=models.Q(("period_end__gt", models.F("period_start")), ("period_start__isnull", True), _connector="OR"), name="bill_cycle_period_end_after_start"),
                    models.UniqueConstraint(fields=("utility_connection", "period_start", "period_end"), name="unique_bill_cycle_per_connection_and_period"),
                ],
            },
        ),
        migrations.CreateModel(
            name="UtilityBillPayment",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("amount", models.DecimalField(decimal_places=2, max_digits=12)),
                ("paid_at", models.DateTimeField()),
                ("reference", models.CharField(blank=True, max_length=100)),
                ("proof", models.FileField(blank=True, null=True, upload_to="utility_bill_payments/%Y/%m/")),
                ("notes", models.TextField(blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("confirmed_at", models.DateTimeField(blank=True, null=True)),
                ("bill_cycle", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="payments", to="smart_meter.utilitybillcycle")),
                ("created_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to=settings.AUTH_USER_MODEL)),
                ("updated_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="+", to=settings.AUTH_USER_MODEL)),
            ],
            options={
                "constraints": [models.CheckConstraint(condition=models.Q(("amount__gt", 0)), name="utility_bill_payment_amount_positive")],
            },
        ),
        migrations.CreateModel(
            name="InverterPeriodStatement",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("period_start", models.DateField()),
                ("period_end", models.DateField()),
                ("pv_reading_start_kwh", models.DecimalField(decimal_places=3, max_digits=12)),
                ("pv_reading_end_kwh", models.DecimalField(decimal_places=3, max_digits=12)),
                ("start_screenshot", models.ImageField(blank=True, null=True, upload_to="inverter_statements/%Y/%m/")),
                ("end_screenshot", models.ImageField(blank=True, null=True, upload_to="inverter_statements/%Y/%m/")),
                ("notes", models.TextField(blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("confirmed_at", models.DateTimeField(blank=True, null=True)),
                ("created_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to=settings.AUTH_USER_MODEL)),
                ("energy_system", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="inverter_statements", to="smart_meter.energysystem")),
                ("updated_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="+", to=settings.AUTH_USER_MODEL)),
            ],
            options={
                "constraints": [
                    models.CheckConstraint(condition=models.Q(("period_end__gt", models.F("period_start"))), name="inverter_statement_end_after_start"),
                    models.CheckConstraint(condition=models.Q(("pv_reading_end_kwh__gte", models.F("pv_reading_start_kwh"))), name="inverter_statement_pv_end_not_before_start"),
                    models.UniqueConstraint(fields=("energy_system", "period_start", "period_end"), name="unique_inverter_statement_period"),
                ],
            },
        ),
        migrations.CreateModel(
            name="EnergyReconciliationAuditEvent",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("action", models.CharField(choices=[("created", "Created"), ("confirmed", "Confirmed"), ("finalized", "Finalized"), ("reopened", "Reopened"), ("edited", "Edited")], max_length=20)),
                ("reason", models.TextField(blank=True)),
                ("changed_at", models.DateTimeField(auto_now_add=True)),
                ("snapshot", models.JSONField(blank=True, null=True)),
                ("changed_by", models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, to=settings.AUTH_USER_MODEL)),
                ("inverter_statement", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name="audit_events", to="smart_meter.inverterperiodstatement")),
                ("utility_bill_cycle", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name="audit_events", to="smart_meter.utilitybillcycle")),
                ("utility_bill_payment", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name="audit_events", to="smart_meter.utilitybillpayment")),
            ],
            options={
                "constraints": [models.CheckConstraint(condition=models.Q(models.Q(("inverter_statement__isnull", True), ("utility_bill_cycle__isnull", False), ("utility_bill_payment__isnull", True)), models.Q(("inverter_statement__isnull", False), ("utility_bill_cycle__isnull", True), ("utility_bill_payment__isnull", True)), models.Q(("inverter_statement__isnull", True), ("utility_bill_cycle__isnull", True), ("utility_bill_payment__isnull", False)), _connector="OR"), name="audit_event_exactly_one_target")],
            },
        ),
    ]
