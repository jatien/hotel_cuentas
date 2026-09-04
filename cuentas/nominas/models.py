from django.db import models

# Create your models here


class Empleado(models.Model):
    nif = models.CharField(max_length=20, unique=True)
    nombre = models.CharField(max_length=200)

    def __str__(self):
        return self.nombre

    class Meta:
        ordering = ['nombre']


class NominaMensual(models.Model):
    empleado = models.ForeignKey(Empleado, on_delete=models.CASCADE, related_name='nominas')
    anio = models.IntegerField()
    mes = models.IntegerField()  # 1-12
    departamento = models.CharField(max_length=100)  # puede cambiar mes a mes

    deveng = models.DecimalField(max_digits=10, decimal_places=2)
    base_din = models.DecimalField(max_digits=10, decimal_places=2)
    base_esp = models.DecimalField(max_digits=10, decimal_places=2)
    irpf_esp = models.DecimalField(max_digits=10, decimal_places=2)
    irpf_din = models.DecimalField(max_digits=10, decimal_places=2)
    embarg = models.DecimalField(max_digits=10, decimal_places=2)
    prima = models.DecimalField(max_digits=10, decimal_places=2)
    manut = models.DecimalField(max_digits=10, decimal_places=2)
    enf_acc = models.DecimalField(max_digits=10, decimal_places=2)
    bonif = models.DecimalField(max_digits=10, decimal_places=2)
    s_neto = models.DecimalField(max_digits=10, decimal_places=2)
    ss_trabaj = models.DecimalField(max_digits=10, decimal_places=2)
    ss_empr = models.DecimalField(max_digits=10, decimal_places=2)
    rlc = models.DecimalField(max_digits=10, decimal_places=2)
    cost_tot = models.DecimalField(max_digits=10, decimal_places=2)

    # antes era su propia tabla; ahora es un campo más, editable a mano
    importe_extra = models.DecimalField(max_digits=10, decimal_places=2, default=0)

    class Meta:
        unique_together = ('empleado', 'anio', 'mes')  # el "nif+mes" que pedías, vía la FK a empleado
        ordering = ['-anio', '-mes', 'empleado__nombre']

    def __str__(self):
        return f"{self.empleado.nombre} {self.mes}/{self.anio}"

    @property
    def total_con_extra(self):
        return self.cost_tot + self.importe_extra