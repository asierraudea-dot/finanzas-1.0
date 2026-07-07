import streamlit as st
import pandas as pd
import plotly.express as px
from datetime import datetime, date

st.set_page_config(page_title="Finanzas Fáciles", page_icon="💰", layout="wide")

# Sidebar Navigation
st.sidebar.title("💰 Finanzas Fáciles")
st.sidebar.subheader("Administración Inteligente")
page = st.sidebar.radio("Selecciona módulo", 
    ["📊 Dashboard General", "📋 Registro y Presupuesto", "📈 Seguimiento Mensual", 
     "🎯 Metas Financieras", "💳 Deudas y Créditos", "💡 Recomendaciones IA",
     "📊 Simulador de Portafolio"])

# Datos persistentes
if 'transactions' not in st.session_state:
    st.session_state.transactions = pd.DataFrame(columns=['Fecha', 'Tipo', 'Categoría', 'Monto', 'Descripción'])

if 'goals' not in st.session_state:
    st.session_state.goals = []

def save_transaction(tipo, cat, monto, desc):
    new_row = pd.DataFrame([{
        'Fecha': datetime.now().date(),
        'Tipo': tipo,
        'Categoría': cat,
        'Monto': monto,
        'Descripción': desc
    }])
    st.session_state.transactions = pd.concat([st.session_state.transactions, new_row], ignore_index=True)

# ====================== MÓDULOS ======================

if page == "📊 Dashboard General":
    st.title("📊 Dashboard General de Finanzas")
    ingreso_mensual = st.number_input("Ingreso mensual estimado (COP)", min_value=0, value=5000000, step=50000)
    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric("Ingreso Mensual", f"${ingreso_mensual:,.0f}")
    with col2:
        st.metric("Ahorro Potencial", f"${int(ingreso_mensual * 0.20):,.0f}")
    with col3:
        st.metric("Salud Financiera", "Moderada")

elif page == "📋 Registro y Presupuesto":
    st.title("📋 Registro de Ingresos y Gastos")
    tab1, tab2 = st.tabs(["Nuevo Registro", "Presupuesto Mensual"])
    with tab1:
        col_a, col_b = st.columns(2)
        with col_a:
            tipo = st.selectbox("Tipo", ["Ingreso", "Gasto"])
            monto = st.number_input("Monto (COP)", min_value=0, value=100000)
        with col_b:
            categoria = st.selectbox("Categoría", ["Vivienda", "Alimentación", "Transporte", "Manutención", "Servicios", "Entretenimiento", "Otros"])
            desc = st.text_input("Descripción")
        if st.button("Guardar Transacción"):
            save_transaction(tipo, categoria, monto if tipo == "Ingreso" else -monto, desc)
            st.success("✅ Guardado!")
    with tab2:
        st.info("Presupuesto mensual - en desarrollo")

elif page == "📈 Seguimiento Mensual":
    st.title("📈 Seguimiento Mensual")
    df = st.session_state.transactions
    if not df.empty:
        st.dataframe(df)
    else:
        st.info("No hay transacciones aún.")

elif page == "🎯 Metas Financieras":
    st.title("🎯 Metas Financieras")
    st.info("Módulo de metas - en desarrollo avanzado")

elif page == "💳 Deudas y Créditos":
    st.title("💳 Gestión de Deudas")
    st.info("Calculadora de deudas - en desarrollo")

elif page == "💡 Recomendaciones IA":
    st.title("💡 Recomendaciones")
    st.success("Automatiza tu ahorro y revisa gastos variables.")

# ==================== SIMULADOR ====================
elif page == "📊 Simulador de Portafolio":
    st.title("📊 Simulador de Portafolio y Proyecciones")
    
    st.subheader("Configuración Mensual")
    ingreso = st.number_input("Ingreso mensual", value=5000000)
    gastos = st.number_input("Gastos mensuales", value=1500000)
    reserva_pct = st.slider("Reserva imprevistos (%)", 10, 30, 20)
    
    ahorro_bruto = ingreso - gastos
    reserva = int(ahorro_bruto * (reserva_pct / 100))
    ahorro_neto = ahorro_bruto - reserva
    
    st.metric("Ahorro Neto Mensual para Invertir", f"${ahorro_neto:,.0f}")
    
    st.subheader("Distribución de Inversión")
    rf = st.slider("Renta Fija (%)", 0, 100, 50)
    rv = st.slider("Renta Variable Colombia (%)", 0, 100, 30)
    intl = st.slider("Internacional/Dólar (%)", 0, 100, 15)
    
    meses = st.slider("Meses a proyectar", 6, 60, 12)
    
    capital = 0.0
    data = []
    for mes in range(1, meses + 1):
        capital += ahorro_neto
        rendimiento = (capital * 0.12 / 12)
        capital += rendimiento
        data.append({'Mes': mes, 'Aporte': ahorro_neto, 'Rendimiento': round(rendimiento), 'Capital Final': round(capital)})
    
    df_sim = pd.DataFrame(data)
    st.dataframe(df_sim, use_container_width=True)
    
    fig = px.line(df_sim, x='Mes', y='Capital Final', title="Crecimiento Proyectado")
    st.plotly_chart(fig, use_container_width=True)
    
    st.success(f"**Capital proyectado en {meses} meses: ${capital:,.0f} COP**")

st.caption("Finanzas Fáciles v2.0 © 2026 | Simulador incluido")
