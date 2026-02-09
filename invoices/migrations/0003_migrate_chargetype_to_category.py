from django.db import migrations


def move_charge_type_to_category(apps, schema_editor):
    ItemCategory = apps.get_model('invoices', 'ItemCategory')
    InvoiceItem = apps.get_model('invoices', 'InvoiceItem')
    ChargeType = apps.get_model('invoices', 'ChargeType')

    # Make sure a matching ItemCategory exists for every ChargeType by name
    for ct in ChargeType.objects.all():
        cat, _ = ItemCategory.objects.get_or_create(
            name=ct.name, defaults={'is_active': True})
        # Only fill where category is missing
        InvoiceItem.objects.filter(
            charge_type=ct, category__isnull=True).update(category=cat)


class Migration(migrations.Migration):

    dependencies = [
        ('invoices', '0002_itemcategory_remove_invoiceitem_tax_rate_and_more'),
    ]

    operations = [
        migrations.RunPython(move_charge_type_to_category,
                             migrations.RunPython.noop),
        # Now that all rows have a category, remove the redundant field
        migrations.RemoveField(
            model_name='invoiceitem',
            name='charge_type',
        ),
    ]
