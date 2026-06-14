from django.db import migrations

PROFILE_NAME = "Chat bot + historial de sesiones"
PERMS = ["tutor.acceder", "tutor.ver_historial", "catalogo.busqueda_semantica"]


def create_profile(apps, schema_editor):
    Profile = apps.get_model("users", "Profile")
    PermissionAtom = apps.get_model("users", "PermissionAtom")
    ProfilePermission = apps.get_model("users", "ProfilePermission")
    profile, _ = Profile.objects.get_or_create(
        name=PROFILE_NAME,
        defaults={"description": "Acceso al Tutor Visual IA con historial de sesiones (plan premium superior)."},
    )
    for code in PERMS:
        atom = PermissionAtom.objects.filter(code=code).first()
        if atom:
            ProfilePermission.objects.get_or_create(
                profile=profile, permission=atom, defaults={"scope": "todos"}
            )


def remove_profile(apps, schema_editor):
    Profile = apps.get_model("users", "Profile")
    Profile.objects.filter(name=PROFILE_NAME).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("users", "0009_remove_marketing_cupones_atom"),
    ]

    operations = [
        migrations.RunPython(create_profile, remove_profile),
    ]
