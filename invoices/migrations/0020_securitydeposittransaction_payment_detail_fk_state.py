from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("payments", "0008_rename_paymentallocation_paymentdetail"),
        ("invoices", "0019_alter_invoiceitem_description_length"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.AlterField(
                    model_name="securitydeposittransaction",
                    name="allocation",
                    field=models.OneToOneField(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="security_amt",
                        to="payments.paymentdetail",
                    ),
                ),
            ],
            database_operations=[],
        ),
    ]
