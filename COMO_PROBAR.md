# Cómo probar la solución

Guía rápida para ejecutar y verificar el flujo en tu máquina (Windows / PowerShell).

## Dónde correr los comandos

Abre **PowerShell** dentro de la carpeta del proyecto:
- En el Explorador de archivos, entra a la carpeta del proyecto, haz clic en la
  barra de direcciones, escribe `powershell` y Enter. La terminal abre ya ubicada.

Debes ver el prompt así:
```
PS C:\...\prueba-automatizaciones-ia-tumipay>
```

---

## Prueba A — Ejecución normal (SQLite, rápida)

```powershell
.\.venv\Scripts\Activate.ps1
python src\procesar.py
```

Al final verás el resumen:
```
Lote terminado. Total: 10
  - procesada: 8
  - requiere revisión manual: 2
  - fallida: 0
```

Ver los resultados:
```powershell
start data\salida_ejemplo.csv          # abre el CSV de salida en Excel
```

---

## Prueba B — Reproducibilidad (desde cero, "como el evaluador")

Demuestra que otra persona puede ejecutarlo solo con el README.

```powershell
Remove-Item -Recurse -Force .venv -ErrorAction SilentlyContinue
Remove-Item solicitudes.db -ErrorAction SilentlyContinue

python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
# Asegúrate de que .env tiene tu ANTHROPIC_API_KEY
python src\procesar.py
```

Si la activación falla con error de scripts, ejecuta **una vez**:
```powershell
Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned
```

---

## Prueba C — Manejo de errores (lo que más se valora)

Un CSV con casos problemáticos a propósito:

```powershell
@"
id_solicitud,fecha,canal,tipo_cliente,nombre_cliente,mensaje,prioridad_reportada
PRUEBA-1,2026-06-06,correo,cliente final,Test,,alta
PRUEBA-2,2026-06-06,whatsapp,cliente final,Test,Me clonaron la tarjeta y veo cobros raros urgente,baja
PRUEBA-3,2026-06-06,correo,cliente final,Test,hola,
"@ | Out-File -Encoding utf8 data\prueba_errores.csv

python src\procesar.py data\prueba_errores.csv
```

Resultado esperado:
- **PRUEBA-1** → requiere revisión manual (mensaje vacío).
- **PRUEBA-2** → procesada, `Riesgo / fraude`, prioridad **Alta** (aunque la
  reportó "baja").
- **PRUEBA-3** → requiere revisión manual (mensaje ambiguo).

---

## Prueba D (opcional) — Docker + PostgreSQL

Demuestra el destino de producción sin cambiar código:
```powershell
docker compose up --build
```

---

## Nota sobre errores transitorios

Si ves un aviso de `429` o `503`, **no es un bug**: es un límite/estado temporal de
la API. El programa lo reconoce, reintenta con backoff y, si tras los reintentos
sigue fallando, marca la solicitud como `fallida` sin tumbar el lote. Con el tier
de pago de Anthropic y el modelo Haiku esto es muy poco probable en lotes pequeños.
