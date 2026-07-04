import streamlit as st
import pandas as pd
import plotly.express as px
from datetime import datetime

st.set_page_config(page_title="Finanzas Fáciles", page_icon="💰", layout="centered")

st.title("💰 Finanzas Fáciles")
st.subheader("Planeación financiera simple para ingresos limitados")

st.sidebar.header("Tu Perfil Financiero")
ingreso_mensual = st.sidebar.number_input("Ingreso mensual (COP)", min_value=0, value=1200000, step=50000)

tab1, tab2, tab3, tab4 = st.tabs(["📊 Dashboard", "💸 Registrar Gastos", "🎯 Metas", "💡 Recomendaciones"])

with tab1:
    st.header("Resumen Financiero")
    if ingreso_mensual > 0:
        gastos = {
            'Alimentación': 450000,
            'Transporte': 150000,
            'Servicios': 120000,
            'Vivienda': 300000,
            'Otros': 80000
        }
        total_gastos = sum(gastos.values())
        ahorro = ingreso_mensual - total_gastos
        
        col1, col2, col3 = st.columns(3)
        col1.metric("Ingreso", f"${ingreso_mensual:,.0f}")
        col2.metric("Gastos", f"${total_gastos:,.0f}")
        col3.metric("Ahorro", f"${ahorro:,.0f}", delta="Positivo" if ahorro > 0 else "Negativo")
        
        df_gastos = pd.DataFrame(list(gastos.items()), columns=['Categoría', 'Monto'])
        fig = px.pie(df_gastos, names='Categoría', values='Monto', title="Distribución de Gastos")
        st.plotly_chart(fig, use_container_width=True)

with tab2:
    st.header("Registrar Gastos")
    cat = st.selectbox("Categoría", ["Alimentación", "Transporte", "Servicios", "Vivienda", "Salud", "Educación", "Otros"])
    monto = st.number_input("Monto (COP)", min_value=0)
    if st.button("Guardar Gasto"):
        st.success(f"✅ Gasto de ${monto:,.0f} en {cat} registrado.")

with tab3:
    st.header("🎯 Simulador de Metas")
    meta = st.text_input("¿Qué quieres lograr?", "Fondo de emergencia")
    monto_meta = st.number_input("Monto objetivo (COP)", min_value=100000)
    meses = st.slider("¿En cuántos meses?", 3, 36, 12)
    if st.button("Calcular"):
        mensual = monto_meta / meses
        st.success(f"Para lograr tu meta necesitas ahorrar **${mensual:,.0f}** mensuales.")

with tab4:
    st.header("💡 Recomendaciones")
    st.info("**Regla recomendada 60/30/10** para ingresos limitados")
    st.write("• 60% Necesidades básicas")
    st.write("• 30% Gastos variables")
    st.write("• 10% Ahorro")
    
    st.subheader("Opciones de Ahorro e Inversión Asequibles")
    st.write("• Cuenta de Ahorro tradicional")
    st.write("• Fondo de Emergencia (empieza con $30.000 mensuales)")
    st.write("• Microinversiones en Nequi, Daviplata o Bancolombia")
    st.write("• Criptomonedas con montos bajos")

st.caption("Finanzas Fáciles © 2026 | Herramienta para una mejor vida financiera")
