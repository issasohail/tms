from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("tenants", "0026_tenant_cnic_validity_dates"),
    ]

    operations = [
        migrations.RunSQL(
            sql='''
            ALTER TABLE tenants_tenant ADD COLUMN temporary_address_urdu LONGTEXT;
            ALTER TABLE tenants_tenant ADD COLUMN permanent_address_urdu LONGTEXT;
            UPDATE tenants_tenant SET temporary_address_urdu = '' WHERE temporary_address_urdu IS NULL;
            UPDATE tenants_tenant SET permanent_address_urdu = '' WHERE permanent_address_urdu IS NULL;
            ALTER TABLE tenants_tenant MODIFY temporary_address_urdu LONGTEXT NOT NULL;
            ALTER TABLE tenants_tenant MODIFY permanent_address_urdu LONGTEXT NOT NULL;
            ''',
            reverse_sql='''
            ALTER TABLE tenants_tenant DROP COLUMN temporary_address_urdu;
            ALTER TABLE tenants_tenant DROP COLUMN permanent_address_urdu;
            ''',
        ),
    ]
