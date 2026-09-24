# nominas/management/commands/import_nomina_mes.py
from django.core.management.base import BaseCommand
from nominas.services import importar_nomina_mensual

class Command(BaseCommand):
    help = 'Importa el desglose mensual de nóminas'

    def add_arguments(self, parser):
        parser.add_argument('csv_path', type=str)
        parser.add_argument('--anio', type=int)
        parser.add_argument('--mes', type=int)

    def handle(self, *args, **options):
        with open(options['csv_path'], 'rb') as f:
            count, anio, mes = importar_nomina_mensual(f, options.get('anio'), options.get('mes'))
        self.stdout.write(self.style.SUCCESS(f'{count} nóminas importadas para {mes}/{anio}'))