from django.db import migrations


def remove_atom(apps, schema_editor):
    """
    Elimina el permiso huérfano 'marketing.gestionar_cupones'. La gestión de
    cupones quedó unificada en 'gestion.gestionar_promociones'; este atom ya no
    se usa en ningún lado. Al borrarlo, los ProfilePermission asociados caen en
    cascada (FK on_delete=CASCADE).
    """
    PermissionAtom = apps.get_model('users', 'PermissionAtom')
    PermissionAtom.objects.filter(code='marketing.gestionar_cupones').delete()
    # Afinar la descripción del permiso de exportación (solo pedidos, con CSV)
    PermissionAtom.objects.filter(code='gestion.exportar_reportes').update(
        description='Acceder y exportar (CSV) el reporte de pedidos'
    )


def recreate_atom(apps, schema_editor):
    PermissionAtom = apps.get_model('users', 'PermissionAtom')
    PermissionAtom.objects.get_or_create(
        code='marketing.gestionar_cupones',
        defaults={'module': 'marketing', 'description': 'Crear y administrar cupones de descuento', 'scope_aplica': False},
    )


class Migration(migrations.Migration):

    dependencies = [
        ('users', '0008_gestion_atoms'),
    ]

    operations = [
        migrations.RunPython(remove_atom, recreate_atom),
    ]
