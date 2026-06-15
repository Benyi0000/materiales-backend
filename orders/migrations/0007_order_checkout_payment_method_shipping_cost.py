from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('orders', '0006_mp_payment_data'),
    ]

    operations = [
        migrations.AddField(
            model_name='order',
            name='checkout_payment_method',
            field=models.CharField(
                blank=True,
                choices=[
                    ('mercadopago', 'MercadoPago'),
                    ('card', 'Tarjeta (simulado)'),
                    ('cash', 'Efectivo'),
                ],
                default='',
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name='order',
            name='shipping_cost',
            field=models.DecimalField(decimal_places=2, default=0, max_digits=10),
        ),
    ]
