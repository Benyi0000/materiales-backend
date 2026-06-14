from django.db import migrations

PROFILE = "Chat bot + historial de sesiones"
PERM = "catalogo.busqueda_semantica"


def quitar_busqueda(apps, schema_editor):
    """
    Deja las 3 opciones de plan distintas:
      - Chat bot                                  -> tutor.acceder + búsqueda
      - Chat bot + historial de sesiones          -> tutor.acceder + historial (sin búsqueda)
      - Chat bot + búsqueda semántica + historial -> los tres
    """
    Profile = apps.get_model("users", "Profile")
    PermissionAtom = apps.get_model("users", "PermissionAtom")
    ProfilePermission = apps.get_model("users", "ProfilePermission")
    p = Profile.objects.filter(name=PROFILE).first()
    atom = PermissionAtom.objects.filter(code=PERM).first()
    if p and atom:
        ProfilePermission.objects.filter(profile=p, permission=atom).delete()


def re_agregar_busqueda(apps, schema_editor):
    Profile = apps.get_model("users", "Profile")
    PermissionAtom = apps.get_model("users", "PermissionAtom")
    ProfilePermission = apps.get_model("users", "ProfilePermission")
    p = Profile.objects.filter(name=PROFILE).first()
    atom = PermissionAtom.objects.filter(code=PERM).first()
    if p and atom:
        ProfilePermission.objects.get_or_create(profile=p, permission=atom, defaults={"scope": "todos"})


class Migration(migrations.Migration):

    dependencies = [
        ("users", "0011_chatbot_busqueda_historial_profile"),
    ]

    operations = [
        migrations.RunPython(quitar_busqueda, re_agregar_busqueda),
    ]
