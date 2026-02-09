from django.db import migrations, models
import django.db.models.deletion


def forwards(apps, schema_editor):
    Payment = apps.get_model('payments', 'Payment')
    PaymentMethod = apps.get_model('core', 'PaymentMethod')

    # 1) Ensure base payment methods exist
    cash, _ = PaymentMethod.objects.get_or_create(
        code='cash',
        defaults={'name': 'Cash', 'is_active': True, 'sort_order': 10}
    )
    easypaisa, _ = PaymentMethod.objects.get_or_create(
        code='easypaisa',
        defaults={'name': 'Easy Paisa', 'is_active': True, 'sort_order': 20}
    )
    bank_transfer, _ = PaymentMethod.objects.get_or_create(
        code='bank_transfer',
        defaults={'name': 'Bank Transfer', 'is_active': True, 'sort_order': 30}
    )

    alias_map = {
        'cash': cash,
        'cash ': cash,
        'easy_paisa': easypaisa,
        'easy paisa': easypaisa,
        'easypaisa': easypaisa,
        'easy paisa ': easypaisa,
        'bank transfer': bank_transfer,
        'bank_transfer': bank_transfer,
        'bank': bank_transfer,
    }

    fallback = cash  # default if nothing matches

    for p in Payment.objects.all():
        old = (getattr(p, 'payment_method', '') or '').strip().lower()
        pm = alias_map.get(old, fallback)
        p.payment_method_fk = pm
        p.save(update_fields=['payment_method_fk'])


def backwards(apps, schema_editor):
    """
    If you ever migrate backwards, reconstruct the old char field from FK code.
    """
    Payment = apps.get_model('payments', 'Payment')

    for p in Payment.objects.all():
        pm = getattr(p, 'payment_method_fk', None)
        if pm is not None:
            p.payment_method = pm.code
            p.save(update_fields=['payment_method'])


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0004_paymentmethod'),   # adjust to the actual PaymentMethod migration name
        ('payments', '0001_initial'),
    ]

    operations = [
        # 1) Add temporary FK field
        migrations.AddField(
            model_name='payment',
            name='payment_method_fk',
            field=models.ForeignKey(
                to='core.paymentmethod',
                on_delete=django.db.models.deletion.PROTECT,
                related_name='payments_fk_temp',
                null=True,
                blank=True,
            ),
        ),

        # 2) Data migration: copy char -> FK
        migrations.RunPython(forwards, backwards),

        # 3) Drop old char field
        migrations.RemoveField(
            model_name='payment',
            name='payment_method',
        ),

        # 4) Rename FK to final field name
        migrations.RenameField(
            model_name='payment',
            old_name='payment_method_fk',
            new_name='payment_method',
        ),
    ]
