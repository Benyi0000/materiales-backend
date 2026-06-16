from django.core.management.base import BaseCommand
from catalog.tasks import regenerate_missing_embeddings


class Command(BaseCommand):
    help = "Regenera el embedding (RAG/Gemini) de los productos que todavía no tienen uno."

    def add_arguments(self, parser):
        parser.add_argument(
            '--limit', type=int, default=None,
            help="Cantidad máxima de productos a procesar (default: todos los que falten).",
        )

    def handle(self, *args, **options):
        results = regenerate_missing_embeddings(limit=options['limit'])
        succeeded = sum(1 for r in results if r['success'])
        failed = sum(1 for r in results if not r['success'])
        for r in results:
            mark = self.style.SUCCESS('OK') if r['success'] else self.style.ERROR('FALLÓ')
            self.stdout.write(f"  [{mark}] {r['sku']} (id={r['id']})")
        self.stdout.write(self.style.SUCCESS(
            f"Procesados: {len(results)} · Éxitos: {succeeded} · Fallidos: {failed}"
        ))
