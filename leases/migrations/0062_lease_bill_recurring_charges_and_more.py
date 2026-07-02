# Generated to accompany bill_recurring_charges field addition

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('leases', '0061_lease_bill_water_charges_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='lease',
            name='bill_recurring_charges',
            field=models.BooleanField(
                default=True,
                help_text='Include this lease in monthly recurring/rent billing checks. '
                          'Uncheck for leases that intentionally have no recurring charge (e.g. staff/comp units).',
            ),
        ),
        migrations.AddField(
            model_name='leaserenewal',
            name='bill_recurring_charges',
            field=models.BooleanField(
                default=True,
                help_text='Include this lease in monthly recurring/rent billing checks.',
            ),
        ),
    ]
