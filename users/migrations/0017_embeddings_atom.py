from django.db import migrations


NEW_ATOMS = [
    ('gestion.gestionar_embeddings', 'gestion', 'Ver y regenerar los embeddings vectoriales (IA) de los productos'),
]


def create_atoms(apps, schema_editor):
    PermissionAtom = apps.get_model('users', 'PermissionAtom')
    for code, module, desc in NEW_ATOMS:
        PermissionAtom.objects.get_or_create(
            code=code,
            defaults={'module': module, 'description': desc, 'scope_aplica': False},
        )


def remove_atoms(apps, schema_editor):
    PermissionAtom = apps.get_model('users', 'PermissionAtom')
    PermissionAtom.objects.filter(code__in=[c for c, _, _ in NEW_ATOMS]).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('users', '0016_pagos_mercadopago_to_client_profile'),
    ]

    operations = [
        migrations.RunPython(create_atoms, remove_atoms),
    ]
