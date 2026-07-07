import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from datetime import datetime, date
import json
from io import StringIO

st.set_page_config(page_title="Finanzas Fáciles", page_icon="💰", layout="wide")

# Sidebar Navigation
st.sidebar.title("💰 Finanzas Fáciles")
st.sidebar.subheader("Administración Inteligente")
page = st.sidebar.radio("Selecciona módulo", 
    ["📊 Dashboard General", "📋 Registro y Presupuesto", "📈 Seguimiento Mensual", 
     "🎯 Metas Financieras", "💳 Deudas y Créditos", "💡 Recomendaciones IA"])

# Datos persistentes simulados (en producción usar session_state + base de datos)
if 'transactions' not in st.session_state:
    st.session_state.transactions = pd.DataFrame(columns=['Fecha', 'Tipo', 'Categoría', 'Monto', 'Descripción'])

if 'goals' not in st.session_state:
    st.session_state.goals = []

# Funciones auxiliares
def load_data():
    return st.session_state.transactions

def save_transaction(tipo, cat, monto, desc):
    new_row = pd.DataFrame({
        'Fecha': [datetime.now().date()],
        'Tipo': [tipo],
        'Categoría': [cat],
        'Monto': [monto],
        'Descripción': [desc]
    })
    st.session_state.transactions = pd.concat([st.session_state.transactions, new_row], ignore_index=True)

# ====================== MÓDULOS ======================

if page == "📊 Dashboard General":
    st.title("📊 Dashboard General de Finanzas")
    
    ingreso_mensual = st.number_input("Ingreso mensual estimado (COP)", min_value=0, value=1500000, step=50000)
    
    # Métricas clave
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("Ingreso Mensual", f"${ingreso_mensual:,.0f}")
    with col2:
        st.metric("Gastos Estimados", f"${int(ingreso_mensual * 0.75):,.0f}", delta="-25%")
    with col3:
        st.metric("Ahorro Potencial", f"${int(ingreso_mensual * 0.20):,.0f}")
    with col4:
        st.metric("Nivel de Salud Financiera", "Moderado", delta="Mejorable")
    
    # Gráficos
    st.subheader("Distribución Ideal vs Actual")
    ideal_data = pd.DataFrame({
        'Categoría': ['Necesidades (60%)', 'Deseos (20%)', 'Ahorro/Inversión (20%)'],
        'Porcentaje': [60, 20, 20]
    })
    fig = px.pie(ideal_data, names='Categoría', values='Porcentaje', title="Regla 60/20/20 Recomendada")
    st.plotly_chart(fig, use_container_width=True)

elif page == "📋 Registro y Presupuesto":
    st.title("📋 Registro de Ingresos y Gastos")
    
    tab1, tab2 = st.tabs(["Nuevo Registro", "Presupuesto Mensual"])
    
    with tab1:
        col_a, col_b = st.columns(2)
        with col_a:
            tipo = st.selectbox("Tipo", ["Ingreso", "Gasto"])
            monto = st.number_input("Monto (COP)", min_value=0, value=100000)
        with col_b:
            categoria = st.selectbox("Categoría", 
                ["Vivienda", "Alimentación", "Transporte", "Manutención", "Servicios", 
                 "Entretenimiento", "Salud", "Educación", "Otros"])
            desc = st.text_input("Descripción")
        
        if st.button("Guardar Transacción"):
            save_transaction(tipo, categoria, monto if tipo == "Ingreso" else -monto, desc)
            st.success("✅ Transacción guardada correctamente!")
    
    with tab2:
        ingreso_mensual = st.number_input("Ingreso mensual", value=1500000)
        
        st.subheader("Presupuesto por Categoría")
        categorias = ["Vivienda", "Alimentación", "Transporte", "Manutención", "Servicios", "Entretenimiento", "Otros"]
        presupuestos = {}
        cols = st.columns(3)
        for i, cat in enumerate(categorias):
            with cols[i % 3]:
                presupuestos[cat] = st.number_input(f"{cat}", value=int(ingreso_mensual * 0.12))
        
        total_presup = sum(presupuestos.values())
        st.metric("Total Presupuestado", f"${total_presup:,.0f}", 
                 delta=f"{total_presup - ingreso_mensual:,.0f} COP")

elif page == "📈 Seguimiento Mensual":
    st.title("📈 Seguimiento y Análisis Histórico")
    
    df = load_data()
    if not df.empty:
        df['Fecha'] = pd.to_datetime(df['Fecha'])
        df['Mes'] = df['Fecha'].dt.to_period('M')
        
        monthly = df.groupby(['Mes', 'Tipo'])['Monto'].sum().unstack().fillna(0)
        
        st.subheader("Evolución de Ingresos vs Gastos")
        fig_line = px.line(monthly, title="Tendencia Mensual", markers=True)
        st.plotly_chart(fig_line, use_container_width=True)
        
        st.subheader("Transacciones Recientes")
        st.dataframe(df.sort_values('Fecha', ascending=False), use_container_width=True)
    else:
        st.info("Aún no tienes transacciones registradas. Ve al módulo de Registro.")

elif page == "🎯 Metas Financieras":
    st.title("🎯 Metas Financieras")
    
    st.subheader("Crear Nueva Meta")
    col1, col2 = st.columns(2)
    with col1:
        meta_nombre = st.text_input("Nombre de la meta")
        meta_monto = st.number_input("Monto objetivo (COP)", min_value=100000)
    with col2:
        meta_fecha = st.date_input("Fecha objetivo", value=date.today())
    
    if st.button("Agregar Meta"):
        st.session_state.goals.append({
            "nombre": meta_nombre, 
            "monto": meta_monto, 
            "fecha": meta_fecha,
            "ahorrado": 0
        })
        st.success("Meta creada!")
    
    st.subheader("Tus Metas Activas")
    for i, g in enumerate(st.session_state.goals):
        progreso = 45  # Simulado - puedes mejorarlo
        st.progress(progreso/100, text=f"**{g['nombre']}** - ${g['monto']:,.0f} ({progreso}%)")

elif page == "💳 Deudas y Créditos":
    st.title("💳 Gestión de Deudas")
    
    st.subheader("Registrar o Analizar Deuda")
    deuda_nombre = st.text_input("Nombre de la deuda (Tarjeta, Préstamo, etc.)")
    monto_deuda = st.number_input("Monto total adeudado", value=5000000)
    tasa = st.number_input("Tasa de interés anual (%)", value=28.0)
    
    if st.button("Calcular Impacto"):
        interes_mensual = monto_deuda * (tasa/100/12)
        st.metric("Interés mensual aproximado", f"${interes_mensual:,.0f}")
        st.warning("Prioriza pagar primero las deudas con mayor tasa (método avalancha).")

else:  # Recomendaciones IA
    st.title("💡 Recomendaciones Personalizadas con IA")
    st.success("**Acción prioritaria:** Automatiza el ahorro del 20% el mismo día que recibes ingresos.")
    st.info("Revisa mensualmente gastos en 'Entretenimiento' y 'Otros'.")
    st.error("Si tus deudas superan el 30% de tus ingresos, considera consolidación.")

# Footer
st.sidebar.caption("Finanzas Fáciles v2.0 © 2026")
st.caption("Herramienta educativa para finanzas personales en Colombia y LATAM. Datos locales en esta sesión.")

# ====================== MÓDULOS ======================

if page == "📊 Dashboard General":
    # ... (mantén tu código actual de este módulo)

elif page == "📋 Registro y Presupuesto":
    # ... (mantén tu código actual)

elif page == "📈 Seguimiento Mensual":
    # ... (mantén tu código actual)

elif page == "🎯 Metas Financieras":
    # ... (mantén tu código actual)

elif page == "💳 Deudas y Créditos":
    # ... (mantén tu código actual)

elif page == "💡 Recomendaciones IA":
    # ... (mantén tu código actual)

# ==================== NUEVO MÓDULO ====================
elif page == "📊 Simulador de Portafolio":
    st.title("📊 Simulador de Portafolio y Proyecciones")
    
    st.subheader("Configuración Mensual")
    ingreso = st.number_input("Ingreso mensual", value=5000000)
    gastos = st.number_input("Gastos mensuales", value=1500000)
    reserva_imprevistos = st.slider("Reserva imprevistos (%)", 10, 30, 20)
    
    ahorro_bruto = ingreso - gastos
    reserva = int(ahorro_bruto * (reserva_imprevistos / 100))
    ahorro_neto = ahorro_bruto - reserva
    
    st.metric("Ahorro Neto Mensual para Invertir", f"${ahorro_neto:,.0f}")
    
    st.subheader("Distribución de Inversión")
    rf = st.slider("Renta Fija (%)", 0, 100, 50)
    rv_col = st.slider("Renta Variable Colombia (%)", 0, 100, 30)
    intl = st.slider("Internacional/Dólar (%)", 0, 100, 15)
    alto = 100 - rf - rv_col - intl
    
    meses = st.slider("Meses a proyectar", 6, 60, 12)
    
    # Simulación
    capital = 0.0
    data = []
    for mes in range(1, meses + 1):
        capital += ahorro_neto
        rendimiento = (capital * 0.12 / 12)   # 12% EA promedio
        capital += rendimiento
        data.append({
            'Mes': mes,
            'Aporte': ahorro_neto,
            'Rendimiento': round(rendimiento),
            'Capital Final': round(capital)
        })
    
    df_sim = pd.DataFrame(data)
    st.dataframe(df_sim, use_container_width=True)
    
    fig = px.line(df_sim, x='Mes', y='Capital Final', title="Crecimiento Proyectado del Capital")
    st.plotly_chart(fig, use_container_width=True)
    
    st.success(f"**Capital proyectado en {meses} meses: ${capital:,.0f} COP**")
