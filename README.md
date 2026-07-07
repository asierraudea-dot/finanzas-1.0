[README.md](https://github.com/user-attachments/files/29760795/README.md)
# FinTrack CO v3 Pro

> Gestor financiero personal para el mercado colombiano · GitHub Pages ready

## 🚀 Demo en vivo
[Ver app](https://tu-usuario.github.io/fintrack-co)

## ✨ Funcionalidades

- **Dashboard** con flujo Ingresos → Gastos → Ahorro → Inversión
- **Indicadores de mercado** en tiempo real (TRM USD/COP, Oro, DTF, IPC)
- **Análisis IA** del mercado colombiano (requiere API Anthropic)
- **Seguimiento histórico** de inversiones con movimientos cronológicos
- **Renta fija** — CDT, TES, Bonos, FIC (CDT Nu, Lulo Bank, Bancolombia…)
- **Renta variable** — Acciones BVC, ETF, FIC, Cripto (Tyba, Trii, BTG…)
- **Inmobiliario** — Fonda SiRenta, Vikua, Ladrillo, Fiduciarias
- **Dólares / USD** — cobertura cambiaria con TRM en tiempo real
- **Deudas** — método avalancha, alertas de pago
- **Simulador** de interés compuesto con proyección año a año
- **Motor de alertas** — vencimientos CDT, pagos próximos, gastos excesivos
- **KPIs COLCAP** — ROA, ROE, P/E de acciones top
- **Exportar datos** en JSON para respaldo
- **Persistencia** en localStorage — sin base de datos externa

## 📁 Estructura del repositorio

```
fintrack-co/
├── index.html          # App completa (single file)
├── README.md           # Este archivo
├── .github/
│   └── workflows/
│       └── deploy.yml  # CI/CD automático a GitHub Pages
└── docs/
    └── screenshot.md   # Capturas de pantalla
```

## 🛠️ Instalación local

```bash
# Clonar el repositorio
git clone https://github.com/tu-usuario/fintrack-co.git
cd fintrack-co

# Abrir directamente en el navegador (no requiere servidor)
open index.html

# O con servidor local
npx serve .
python3 -m http.server 8080
```

## 🌐 Despliegue en GitHub Pages

### Opción 1 — Automático (CI/CD incluido)
1. Fork o sube este repositorio a GitHub
2. Ve a **Settings → Pages**
3. En **Source** selecciona `GitHub Actions`
4. Haz push a `main` — se despliega automáticamente

### Opción 2 — Manual
1. Ve a **Settings → Pages**
2. En **Source** selecciona `Deploy from a branch`
3. Branch: `main`, Folder: `/ (root)`
4. Guarda — en 1-2 minutos estará disponible

## 🔑 Variables de entorno (opcional)

La app usa la **API de Anthropic** para el análisis IA de mercado.  
En GitHub Pages, el proxy de Anthropic maneja la autenticación automáticamente cuando se usa desde `claude.ai`.

Para uso en servidor propio, agrega tu API key:
```javascript
// En index.html, busca la función loadMercadoIA()
headers: {
  'x-api-key': 'tu-api-key-aquí',
  'anthropic-version': '2023-06-01',
  'Content-Type': 'application/json'
}
```

## 📊 Productos financieros Colombia (referencia jul 2026)

| Producto | Canal | Tasa ref. |
|---|---|---|
| CDT Nu Colombia | App Nu | ~12.4% E.A. |
| CDT Lulo Bank | App Lulo | ~13.0% E.A. |
| CDT Bancolombia | App/Sucursal | ~11.5% E.A. |
| FIC SiRenta | Acciones y Valores | 13.4% E.A. |
| TES Renta Fija | Valores Bancolombia/Trii | Varía |
| Acciones COLCAP | Trii / BTG / Tyba | Variable |
| ETF globales | Tyba / Interactive Brokers | Variable |

## 🏛️ Metodología de inversión integrada

- **Benjamin Graham**: valor intrínseco, margen de seguridad
- **Charlie Munger**: modelos mentales, diversificación inteligente  
- **Philip Fisher**: análisis cualitativo, tesis de inversión
- **Daniel Kahneman**: control de sesgos cognitivos
- **Método avalancha** (Ramsey): pago de deudas por mayor tasa primero
- **Regla 50/30/20**: necesidades / deseos / ahorro+inversión

## ⚠️ Aviso legal

Esta aplicación es una herramienta de seguimiento personal. **No constituye asesoría financiera certificada.** Consulta siempre a un asesor financiero profesional antes de tomar decisiones de inversión. Los datos de mercado son referenciales.

## 📄 Licencia

MIT License — libre uso, modificación y distribución.

---
Desarrollado con ❤️ para el mercado financiero colombiano · 2026
