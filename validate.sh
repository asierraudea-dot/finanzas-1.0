#!/bin/bash
# FinTrack CO — Script de validación local
# Ejecutar: bash validate.sh

echo "════════════════════════════════════"
echo "  FinTrack CO v3 — Validación"
echo "════════════════════════════════════"

ERRORS=0
WARNINGS=0

# 1. Verificar index.html existe
if [ -f "index.html" ]; then
  echo "✅ index.html encontrado"
else
  echo "❌ index.html NO encontrado"
  ERRORS=$((ERRORS+1))
fi

# 2. Verificar tamaño mínimo
SIZE=$(wc -c < index.html 2>/dev/null || echo 0)
if [ "$SIZE" -gt 50000 ]; then
  echo "✅ Tamaño del archivo: $(numfmt --to=iec $SIZE 2>/dev/null || echo ${SIZE}B)"
else
  echo "⚠️  Archivo muy pequeño ($SIZE bytes)"
  WARNINGS=$((WARNINGS+1))
fi

# 3. Verificar elementos HTML clave
declare -A checks=(
  ["sec-dashboard"]="Sección Dashboard"
  ["sec-seguimiento"]="Módulo Seguimiento"
  ["sec-renta-fija"]="Módulo Renta Fija"
  ["sec-renta-variable"]="Módulo Renta Variable"
  ["sec-inmobiliario"]="Módulo Inmobiliario"
  ["sec-dolares"]="Módulo Dólares"
  ["sec-deudas"]="Módulo Deudas"
  ["sec-simulador"]="Simulador"
  ["sec-ingresos"]="Módulo Ingresos"
  ["sec-gastos"]="Módulo Gastos"
  ["sec-ahorro"]="Módulo Ahorro"
  ["sec-mercado"]="Módulo Mercado"
)

for id in "${!checks[@]}"; do
  if grep -q "id=\"$id\"" index.html 2>/dev/null; then
    echo "✅ ${checks[$id]}"
  else
    echo "❌ Falta: ${checks[$id]} (id=$id)"
    ERRORS=$((ERRORS+1))
  fi
done

# 4. Verificar funciones JS clave
declare -a funcs=("renderDash" "addRF" "addRV" "addInmo" "addUSD" "addDeu" "addIng" "addGasto" "addMeta" "addMov" "renderSeg" "computeAlerts" "fetchMarket" "simular" "exportData")

for fn in "${funcs[@]}"; do
  if grep -q "function $fn" index.html 2>/dev/null; then
    echo "✅ función: $fn()"
  else
    echo "⚠️  No encontrada: $fn()"
    WARNINGS=$((WARNINGS+1))
  fi
done

# 5. Verificar localStorage
if grep -q "localStorage" index.html; then
  echo "✅ Persistencia localStorage habilitada"
else
  echo "⚠️  localStorage no encontrado"
  WARNINGS=$((WARNINGS+1))
fi

# 6. Verificar API calls
if grep -q "exchangerate-api" index.html; then
  echo "✅ API TRM/USD configurada"
fi
if grep -q "anthropic.com" index.html; then
  echo "✅ API Anthropic (análisis IA) configurada"
fi

# 7. Verificar workflow CI/CD
if [ -f ".github/workflows/deploy.yml" ]; then
  echo "✅ GitHub Actions workflow encontrado"
else
  echo "⚠️  Falta .github/workflows/deploy.yml"
  WARNINGS=$((WARNINGS+1))
fi

# 8. Verificar README
if [ -f "README.md" ]; then
  echo "✅ README.md encontrado"
else
  echo "⚠️  Falta README.md"
  WARNINGS=$((WARNINGS+1))
fi

echo ""
echo "════════════════════════════════════"
if [ $ERRORS -eq 0 ]; then
  echo "  RESULTADO: ✅ LISTO PARA GITHUB"
  echo "  Errores: $ERRORS | Advertencias: $WARNINGS"
  echo ""
  echo "  Próximos pasos:"
  echo "  1. git init && git add ."
  echo "  2. git commit -m 'feat: FinTrack CO v3'"
  echo "  3. git remote add origin https://github.com/TU-USUARIO/fintrack-co.git"
  echo "  4. git push -u origin main"
  echo "  5. Activar GitHub Pages → Settings → Pages → GitHub Actions"
else
  echo "  RESULTADO: ❌ REVISAR ERRORES ($ERRORS errores)"
fi
echo "════════════════════════════════════"
