from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("expenses", "0006_expensereceipt_comment"),
    ]

    operations = [
        migrations.AddIndex(
            model_name="expense",
            index=models.Index(
                fields=["date", "id"],
                name="expenses_date_id_idx",
            ),
        ),
    ]
