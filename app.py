import streamlit as st
import pandas as pd
import plotly.express as px

st.set_page_config(page_title="Finanzas Fáciles", page_icon="💰", layout="centered")

st.title("💰 Finanzas Fáciles")
st.subheader("Administración inteligente de tus ingresos")

# Registro inicial
st.header("📋 Registro de Ingresos y Gastos")

ingreso_mensual = st.number_input("¿Cuál es tu ingreso mensual total? (COP)", min_value=0, value=1200000, step=50000)

st.subheader("Distribución actual de tus gastos")

col1, col2 = st.columns(2)

with col1:
    vivienda = st.number_input("Vivienda (arriendo o cuota)", min_value=0, value=350000)
    alimentacion = st.number_input("Alimentación", min_value=0, value=400000)
    transporte = st.number_input("Transporte", min_value=0, value=150000)

with col2:
    manutencion = st.number_input("Manutención de niños / familia", min_value=0, value=200000)
    servicios = st.number_input("Servicios (agua, luz, internet)", min_value=0, value=120000)
    otros = st.number_input("Otros gastos", min_value=0, value=100000)

total_gastos = vivienda + alimentacion + transporte + manutencion + servicios + otros
ahorro_actual = ingreso_mensual - total_gastos

# Distribución actual
data_actual = {
    'Categoría': ['Vivienda', 'Alimentación', 'Transporte', 'Manutención', 'Servicios', 'Otros', 'Ahorro'],
    'Monto': [vivienda, alimentacion, transporte, manutencion, servicios, otros, max(0, ahorro_actual)]
}

df_actual = pd.DataFrame(data_actual)

st.subheader("📊 Tu Distribución Actual")
fig_actual = px.pie(df_actual, names='Categoría', values='Monto', title="Distribución Actual de Ingresos")
st.plotly_chart(fig_actual, use_container_width=True)

st.metric("Ahorro Actual", f"${ahorro_actual:,.0f}", delta="Negativo" if ahorro_actual < 0 else "Positivo")

# Distribución Ideal
st.subheader("🌟 Distribución Ideal Recomendada")
st.info("**Regla recomendada para ingresos limitados: 60% Necesidades - 25% Deseos - 15% Ahorro**")

ideal = {
    'Vivienda': ingreso_mensual * 0.25,
    'Alimentación': ingreso_mensual * 0.20,
    'Transporte': ingreso_mensual * 0.10,
    'Manutención': ingreso_mensual * 0.10,
    'Servicios': ingreso_mensual * 0.08,
    'Otros': ingreso_mensual * 0.07,
    'Ahorro': ingreso_mensual * 0.20
}

df_ideal = pd.DataFrame(list(ideal.items()), columns=['Categoría', 'Monto Ideal'])

fig_ideal = px.pie(df_ideal, names='Categoría', values='Monto Ideal', title="Distribución Ideal Recomendada")
st.plotly_chart(fig_ideal, use_container_width=True)

# Recomendaciones
st.subheader("💡 Recomendaciones para mejorar tu gestión")
if ahorro_actual < ingreso_mensual * 0.10:
    st.error("Estás gastando más de lo que ingresas o ahorrando muy poco. Prioriza reducir gastos variables.")
elif ahorro_actual < ingreso_mensual * 0.15:
    st.warning("Buen esfuerzo, pero puedes mejorar. Intenta ahorrar al menos el 15%.")
else:
    st.success("¡Excelente gestión! Sigue así.")

st.write("**Consejos prácticos:**")
st.write("- Reduce gastos en transporte usando transporte público o bicicleta.")
st.write("- Busca opciones más económicas para alimentación (mercados locales).")
st.write("- Crea un fondo de emergencia aunque sea con $30.000 mensuales.")

st.caption("Finanzas Fáciles © 2026 | Herramienta simple para una mejor vida financiera")
