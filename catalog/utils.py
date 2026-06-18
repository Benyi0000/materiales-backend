import os
from django.conf import settings


def get_google_api_key() -> str:
    """
    Returns the Google API key. Priority order:
    1. SystemSetting DB record — allows runtime changes without server restart.
    2. settings.GOOGLE_API_KEY
    3. GOOGLE_API_KEY environment variable
    """
    try:
        from .models import SystemSetting
        value = SystemSetting.objects.filter(key='GOOGLE_API_KEY').values_list('value', flat=True).first()
        if value:
            return value
    except Exception:
        pass
    return getattr(settings, 'GOOGLE_API_KEY', '') or os.environ.get('GOOGLE_API_KEY', '')
