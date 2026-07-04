import streamlit as st
import pandas as pd
import plotly.express as px
from datetime import datetime

st.set_page_config(page_title="Finanzas Fáciles", page_icon="💰", layout="centered")

st.title("💰 Finanzas Fáciles")
st.subheader("Planeación financiera simple para ingresos restringidos")

# Sidebar
st.sidebar.header("📌 Tu Perfil")
ingreso_mensual = st.sidebar.number_input("Ingreso mensual (COP)", min_value=0, value=1200000, step=50000)

# Tabs
tab1, tab2, tab3, tab4 = st.tabs(["📊 Dashboard", "💸 Registrar Gastos", "🎯 Metas", "💡 Recomendaciones"])

with tab1:
    st.header("Tu Situación Financiera")
    if ingreso_mensual > 0:
        # Datos de ejemplo
        gastos = {
            'Alimentación': 450000,
            'Transporte': 150000,
            'Servicios': 120000,
            'Vivienda': 300000,
            'Otros': 80000
        }
        df_gastos = pd.DataFrame(list(gastos.items()), columns=['Categoría', 'Monto'])
        
        col1, col2 = st.columns(2)
        with col1:
            st.metric("Ingreso Mensual", f"${ingreso_mensual:,.0f}")
            st.metric("Gasto Total Estimado", f"${sum(gastos.values()):,.0f}")
        with col2:
            ahorro = ingreso_mensual - sum(gastos.values())
            st.metric("Ahorro Estimado", f"${ahorro:,.0f}", delta="Mejorable" if ahorro < ingreso_mensual*0.1 else "Bueno")
        
        fig = px.pie(df_gastos, names='Categoría', values='Monto', title="Distribución de Gastos")
        st.plotly_chart(fig, use_container_width=True)

with tab2:
    st.header("Registrar Gastos")
    categoria = st.selectbox("Categoría", ["Alimentación", "Transporte", "Servicios", "Vivienda", "Salud", "Educación", "Otros"])
    monto = st.number_input("Monto (COP)", min_value=0, value=50000)
    if st.button("Guardar Gasto"):
        st.success(f"Gasto de ${monto:,.0f} en {categoria} registrado.")

with tab3:
    st.header("🎯 Metas Financieras")
    meta = st.text_input("¿Qué quieres lograr?", "Fondo de emergencia")
    monto_meta = st.number_input("Monto objetivo (COP)", min_value=100000)
    meses = st.slider("En cuántos meses?", 3, 36, 12)
    if st.button("Simular"):
        mensual = monto_meta / meses
        st.success(f"Necesitas ahorrar **${mensual:,.0f}** mensuales para lograr tu meta.")

with tab4:
    st.header("💡 Recomendaciones Personalizadas")
    st.info("**Regla adaptada 60/30/10** para ingresos limitados")
    st.write("- 60% Necesidades básicas")
    st.write("- 30% Gastos variables")
    st.write("- 10% Ahorro o inversión")
    
    st.subheader("Opciones de Ahorro e Inversión Asequibles")
    st.write("• **Cuenta de Ahorro** - 8-12% anual")
    st.write("• **Fondo de Emergencia** - Empieza con $50.000 mensuales")
    st.write("• **Microinversiones** - Plataformas como Nu, Daviplata o Bancolombia")
    st.write("• **Criptomonedas** - Invierte poco ($20.000) en Bitcoin o stablecoins")

st.caption("Finanzas Fáciles © 2026 | Herramienta para una mejor administración financiera")
