"""
FinTrack CO v4 — Fase 1 Completo
Self-contained: NO requiere carpeta templates
Login + CRUD + Deudas con fecha_inicio + Ahorro intereses
+ Metas con filtros + Movimientos completos por categoría
"""

from flask import (Flask, render_template_string, request, redirect,
                   url_for, session, jsonify, flash, get_flashed_messages)
from functools import wraps
import sqlite3, hashlib, os, secrets, requests, math
from datetime import datetime, date

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", secrets.token_hex(32))
DB = os.path.join(os.path.dirname(__file__), "fintrack.db")

# ══════════════════════════════════════════════════════════════
#  MOTOR DE RENDIMIENTOS
# ══════════════════════════════════════════════════════════════

def calc_rend(monto, tasa_ea, com_ea, periodo, fecha_ini=None, fecha_fin=None):
    neta = max(0.0, float(tasa_ea or 0) - float(com_ea or 0))
    ea   = neta / 100
    m    = float(monto or 0)
    N    = {"diario":365,"mensual":12,"trimestral":4,"semestral":2,"anual":1,"vencimiento":1}
    n    = N.get(periodo, 12)
    r    = {
        "tasa_neta":       round(neta, 4),
        "rend_diario":     round(m*(math.pow(1+ea,1/365)-1) if ea>0 else 0, 2),
        "rend_mensual":    round(m*(math.pow(1+ea,1/12)-1)  if ea>0 else 0, 0),
        "rend_trimestral": round(m*(math.pow(1+ea,1/4)-1)   if ea>0 else 0, 0),
        "rend_semestral":  round(m*(math.pow(1+ea,1/2)-1)   if ea>0 else 0, 0),
        "rend_anual":      round(m*ea, 0),
        "rend_periodo":    round(m*(math.pow(1+ea,1/n)-1) if ea>0 else 0, 2),
        "rend_acumulado":  0, "dias_activo": 0,
        "periodo": periodo,
        "label_periodo": {"diario":"Rend. diario","mensual":"Rend. mensual",
            "trimestral":"Rend. trimestral","semestral":"Rend. semestral",
            "anual":"Rend. anual","vencimiento":"Al vencimiento"}.get(periodo, periodo),
    }
    if fecha_ini:
        try:
            ini  = datetime.strptime(fecha_ini[:10], "%Y-%m-%d").date()
            fin  = date.today()
            if fecha_fin:
                try: fin = min(fin, datetime.strptime(fecha_fin[:10], "%Y-%m-%d").date())
                except: pass
            dias = max(0, (fin - ini).days)
            r["dias_activo"] = dias
            if dias > 0 and ea > 0:
                td = math.pow(1+ea, 1/365)-1
                r["rend_acumulado"] = round(m*(math.pow(1+td,dias)-1), 0)
        except: pass
    return r

# ══════════════════════════════════════════════════════════════
#  MOTOR DE DEUDAS — cálculo de ahorro por abono a capital
# ══════════════════════════════════════════════════════════════

def calc_deuda(saldo_inicial, saldo_actual, cuota, tasa_ea,
               cuotas_total, cuotas_pagadas, fecha_inicio, fecha_pago):
    """
    Calcula métricas completas de una deuda incluyendo:
    - Interés pagado hasta hoy
    - Proyección de fin de deuda
    - Ahorro potencial por abono extra
    - Costo total restante
    """
    si   = float(saldo_inicial or 0)
    sa   = float(saldo_actual or 0)
    c    = float(cuota or 0)
    ea   = float(tasa_ea or 0) / 100
    ct   = int(cuotas_total or 0)
    cp   = int(cuotas_pagadas or 0)
    cr   = max(0, ct - cp)
    # Tasa mensual efectiva
    tm   = math.pow(1 + ea, 1/12) - 1 if ea > 0 else 0
    # Interés mensual sobre saldo actual
    int_mes_act = round(sa * tm, 0)
    # Capital amortizado hasta ahora
    capital_pag = round(si - sa, 0)
    # Interés total pagado hasta ahora
    int_pagado  = round(c * cp - capital_pag, 0) if cp > 0 else 0
    # Costo total restante (cuotas restantes × cuota)
    costo_rest  = round(c * cr, 0)
    # Interés restante estimado
    int_rest    = round(costo_rest - sa, 0) if costo_rest > sa else 0
    # Costo total original
    costo_total = round(c * ct, 0)
    int_total   = round(costo_total - si, 0) if costo_total > si else 0

    # Fecha estimada de fin
    fin_normal = None
    if fecha_inicio and cr > 0:
        try:
            fi = datetime.strptime(fecha_inicio[:10], "%Y-%m-%d").date()
            m_total = cp + cr
            año_fin = fi.year + (fi.month + m_total - 1) // 12
            mes_fin = (fi.month + m_total - 1) % 12 + 1
            fin_normal = f"{año_fin:04d}-{mes_fin:02d}-{fi.day:02d}"
        except: pass

    # Días hasta próximo pago
    dias_pago = None
    if fecha_pago:
        try:
            fp = datetime.strptime(fecha_pago[:10], "%Y-%m-%d").date()
            dias_pago = (fp - date.today()).days
        except: pass

    return {
        "tasa_mensual":   round(tm*100, 4),
        "int_mes_actual": int_mes_act,
        "capital_pagado": capital_pag,
        "int_pagado":     max(0, int_pagado),
        "cuotas_rest":    cr,
        "costo_restante": costo_rest,
        "int_restante":   max(0, int_rest),
        "costo_total":    costo_total,
        "int_total":      max(0, int_total),
        "fin_normal":     fin_normal,
        "dias_pago":      dias_pago,
    }

def calc_ahorro_abono(saldo_actual, cuota, tasa_ea, abono_extra):
    """
    Calcula cuánto se ahorra en intereses al hacer un abono extra a capital.
    Usa amortización francesa.
    """
    sa = float(saldo_actual or 0)
    c  = float(cuota or 0)
    ea = float(tasa_ea or 0) / 100
    ae = float(abono_extra or 0)
    if ea <= 0 or c <= 0 or sa <= 0 or ae <= 0:
        return {"cuotas_eliminadas": 0, "int_ahorrado": 0, "meses_menos": 0}
    tm = math.pow(1+ea, 1/12) - 1

    def contar_cuotas(saldo):
        s = saldo; n = 0
        while s > 0 and n < 600:
            s = s*(1+tm) - c
            n += 1
        return n

    cuotas_orig = contar_cuotas(sa)
    cuotas_nuevo = contar_cuotas(max(0, sa - ae))
    eliminadas = max(0, cuotas_orig - cuotas_nuevo)
    int_ahorrado = round(eliminadas * c - ae, 0) if eliminadas * c > ae else 0
    return {
        "cuotas_eliminadas": eliminadas,
        "int_ahorrado":      max(0, int_ahorrado),
        "meses_menos":       eliminadas,
    }


# ══════════════════════════════════════════════════════════════
#  BASE DE DATOS
# ══════════════════════════════════════════════════════════════

def get_db():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with get_db() as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS usuarios (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nombre TEXT NOT NULL, email TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL, creado TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS ingresos (
            id INTEGER PRIMARY KEY AUTOINCREMENT, usuario INTEGER NOT NULL,
            cat TEXT, desc TEXT, monto REAL DEFAULT 0, fecha TEXT,
            period TEXT DEFAULT 'mensual', notas TEXT,
            creado TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (usuario) REFERENCES usuarios(id)
        );

        CREATE TABLE IF NOT EXISTS gastos (
            id INTEGER PRIMARY KEY AUTOINCREMENT, usuario INTEGER NOT NULL,
            cat TEXT, desc TEXT, monto REAL DEFAULT 0, fecha TEXT,
            tipo TEXT DEFAULT 'variable', presup REAL DEFAULT 0, notas TEXT,
            creado TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (usuario) REFERENCES usuarios(id)
        );

        -- periodo = diario|mensual|trimestral|semestral|anual|vencimiento
        CREATE TABLE IF NOT EXISTS renta_fija (
            id INTEGER PRIMARY KEY AUTOINCREMENT, usuario INTEGER NOT NULL,
            tipo TEXT, canal TEXT, monto REAL DEFAULT 0,
            tasa_ea REAL DEFAULT 0, com_ea REAL DEFAULT 0,
            tasa_neta REAL DEFAULT 0, periodo TEXT DEFAULT 'mensual',
            ini TEXT, vence TEXT, estado TEXT DEFAULT 'activo', notas TEXT,
            creado TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (usuario) REFERENCES usuarios(id)
        );

        CREATE TABLE IF NOT EXISTS renta_variable (
            id INTEGER PRIMARY KEY AUTOINCREMENT, usuario INTEGER NOT NULL,
            tipo TEXT, ticker TEXT, canal TEXT,
            cantidad REAL DEFAULT 0, precio_comp REAL DEFAULT 0,
            precio_act REAL DEFAULT 0, com_pct REAL DEFAULT 0,
            fecha TEXT, riesgo TEXT DEFAULT 'moderado', tesis TEXT,
            creado TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (usuario) REFERENCES usuarios(id)
        );

        CREATE TABLE IF NOT EXISTS inmobiliario (
            id INTEGER PRIMARY KEY AUTOINCREMENT, usuario INTEGER NOT NULL,
            tipo TEXT, nombre TEXT, canal TEXT,
            compra REAL DEFAULT 0, actual REAL DEFAULT 0,
            canon REAL DEFAULT 0, tasa_ea REAL DEFAULT 0,
            com_ea REAL DEFAULT 0, periodo TEXT DEFAULT 'mensual',
            fecha TEXT, notas TEXT,
            creado TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (usuario) REFERENCES usuarios(id)
        );

        CREATE TABLE IF NOT EXISTS dolares (
            id INTEGER PRIMARY KEY AUTOINCREMENT, usuario INTEGER NOT NULL,
            tipo TEXT, nombre TEXT, canal TEXT,
            cant_usd REAL DEFAULT 0, trm_compra REAL DEFAULT 0,
            rend_usd REAL DEFAULT 0, fecha TEXT, notas TEXT,
            creado TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (usuario) REFERENCES usuarios(id)
        );

        -- DEUDAS: modelo completo con fecha_inicio para cálculo de intereses
        CREATE TABLE IF NOT EXISTS deudas (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            usuario       INTEGER NOT NULL,
            tipo          TEXT,
            entidad       TEXT,
            fecha_inicio  TEXT,                    -- fecha en que se tomó el crédito
            fecha_pago    TEXT,                    -- próximo pago
            saldo_inicial REAL DEFAULT 0,          -- monto original del crédito
            saldo_actual  REAL DEFAULT 0,          -- saldo vigente (se reduce con abonos)
            cuota         REAL DEFAULT 0,          -- cuota mensual pactada
            cuotas_total  INTEGER DEFAULT 0,       -- total de cuotas del crédito
            cuotas_pagadas INTEGER DEFAULT 0,      -- cuotas ya pagadas
            tasa_ea       REAL DEFAULT 0,          -- tasa efectiva anual
            prior         TEXT DEFAULT 'media',    -- alta|media|baja (avalancha)
            estado        TEXT DEFAULT 'activa',   -- activa|liquidada|refinanciada
            notas         TEXT,
            creado        TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (usuario) REFERENCES usuarios(id)
        );

        -- METAS: tipo de meta para filtros
        CREATE TABLE IF NOT EXISTS metas (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            usuario    INTEGER NOT NULL,
            nombre     TEXT,
            tipo_meta  TEXT DEFAULT 'ahorro',     -- ahorro|emergencia|viaje|educacion|vivienda|retiro|otro
            objetivo   REAL DEFAULT 0,
            actual     REAL DEFAULT 0,
            mensual    REAL DEFAULT 0,
            fecha      TEXT,
            tipo_cta   TEXT,
            estado     TEXT DEFAULT 'activa',      -- activa|cumplida|cancelada|pausada
            notas      TEXT,
            creado     TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (usuario) REFERENCES usuarios(id)
        );

        -- MOVIMIENTOS: registro central de todas las operaciones
        -- tipo: aporte|retiro|rendimiento|actualizacion|compra|venta|dividendo|
        --       renovacion|liquidacion|abono_capital|pago_cuota|ajuste_saldo|
        --       cambio_condiciones|deposito_meta|retiro_meta
        CREATE TABLE IF NOT EXISTS movimientos (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            usuario         INTEGER NOT NULL,
            cat             TEXT,       -- renta_fija|renta_variable|inmobiliario|dolares|deudas|metas
            inv_id          INTEGER,
            tipo            TEXT,
            monto           REAL DEFAULT 0,
            precio          REAL,       -- precio unitario para acciones/fondos
            fecha           TEXT,
            ctx             TEXT,       -- contexto libre
            trm             REAL DEFAULT 4200,
            ahorro_interes  REAL DEFAULT 0,   -- interés ahorrado por abono a capital
            saldo_restante  REAL,             -- saldo de deuda después del movimiento
            cuotas_menos    INTEGER DEFAULT 0, -- cuotas eliminadas por abono extra
            creado          TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (usuario) REFERENCES usuarios(id)
        );
        """)
        # Migración: agregar columnas nuevas si la DB ya existe
        cols = {
            "deudas":       ["fecha_inicio TEXT","saldo_inicial REAL DEFAULT 0",
                             "cuotas_total INTEGER DEFAULT 0","cuotas_pagadas INTEGER DEFAULT 0",
                             "estado TEXT DEFAULT 'activa'","fecha_pago TEXT"],
            "metas":        ["tipo_meta TEXT DEFAULT 'ahorro'","estado TEXT DEFAULT 'activa'","notas TEXT"],
            "movimientos":  ["ahorro_interes REAL DEFAULT 0","saldo_restante REAL","cuotas_menos INTEGER DEFAULT 0"],
            "ingresos":     ["fecha_fin TEXT"],
        }
        for tabla, nuevas in cols.items():
            try:
                existing = [r[1] for r in db.execute(f"PRAGMA table_info({tabla})").fetchall()]
                for col_def in nuevas:
                    col_name = col_def.split()[0]
                    if col_name not in existing:
                        db.execute(f"ALTER TABLE {tabla} ADD COLUMN {col_def}")
            except: pass
    print("[DB] Lista")


# ══════════════════════════════════════════════════════════════
#  CSS BASE
# ══════════════════════════════════════════════════════════════

CSS = """
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
html{font-size:14px;-webkit-text-size-adjust:100%}
body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",system-ui,sans-serif;background:#f4f4f2;color:#0b0b0b;min-height:100vh}
:root{--bg:#f4f4f2;--surf:#fff;--border:rgba(0,0,0,.09);--bs:rgba(0,0,0,.15);--text:#0b0b0b;--muted:#6b6b68;--hint:#a0a09d;--green:#0f7a52;--gbg:#e8f5ee;--red:#c0392b;--rbg:#fdecea;--blue:#185fa5;--bbg:#e6f1fb;--amber:#854f0b;--abg:#faeeda;--purple:#4a3aa7;--pbg:#eeedfe;--r:10px;--r2:14px;--sh:0 1px 4px rgba(0,0,0,.08);--sw:220px}
.shell{display:flex;min-height:100vh}
.sidebar{width:var(--sw);flex-shrink:0;background:var(--surf);border-right:.5px solid var(--bs);display:flex;flex-direction:column;position:fixed;top:0;left:0;height:100vh;z-index:200;overflow-y:auto;transition:transform .25s}
.main{margin-left:var(--sw);flex:1;min-height:100vh}
.page{padding:20px 24px 32px}
.logo{padding:16px;border-bottom:.5px solid var(--border);display:flex;align-items:center;gap:10px;flex-shrink:0}
.logo-mark{width:28px;height:28px;background:#0b0b0b;border-radius:7px;display:flex;align-items:center;justify-content:center;flex-shrink:0}
.logo-mark svg{width:15px;height:15px;fill:#fff}
.logo-name{font-size:13.5px;font-weight:600}.logo-ver{font-size:10px;color:var(--hint)}
.nav-sec{font-size:9.5px;font-weight:700;color:var(--hint);letter-spacing:1.2px;text-transform:uppercase;padding:12px 14px 4px}
.nav-item{display:flex;align-items:center;gap:8px;padding:8px 12px;font-size:13px;color:var(--muted);border-radius:7px;margin:1px 6px;transition:all .12s;text-decoration:none}
.nav-item:hover{background:var(--bg);color:var(--text)}
.nav-item.active{background:#0b0b0b;color:#fff}
.sidebar-bottom{margin-top:auto;padding:14px;border-top:.5px solid var(--border)}
.user-pill{display:flex;align-items:center;gap:8px;padding:8px 10px;border-radius:8px;background:var(--bg)}
.user-avatar{width:28px;height:28px;border-radius:50%;background:#0b0b0b;color:#fff;display:flex;align-items:center;justify-content:center;font-size:12px;font-weight:700;flex-shrink:0}
.topbar{display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:20px}
.page-title{font-size:19px;font-weight:600;letter-spacing:-.4px}
.page-sub{font-size:12px;color:var(--muted);margin-top:3px}
.btn{display:inline-flex;align-items:center;gap:6px;padding:7px 14px;border-radius:8px;border:.5px solid var(--bs);background:var(--surf);color:var(--text);font-size:12.5px;font-weight:500;cursor:pointer;font-family:inherit;text-decoration:none;line-height:1.3;transition:all .12s}
.btn:hover{background:var(--bg)}
.btn-primary{background:#0b0b0b;color:#fff;border-color:#0b0b0b}.btn-primary:hover{background:#2c2c2c}
.btn-danger{background:var(--rbg);color:var(--red);border-color:transparent}
.btn-edit{background:var(--bbg);color:var(--blue);border-color:transparent}
.btn-success{background:var(--gbg);color:var(--green);border-color:transparent}
.btn-sm{padding:5px 10px;font-size:11.5px}.btn-xs{padding:3px 8px;font-size:10.5px}
.kpi-grid{display:grid;gap:12px;margin-bottom:18px}
.g4{grid-template-columns:repeat(4,1fr)}.g3{grid-template-columns:repeat(3,1fr)}.g2{grid-template-columns:repeat(2,1fr)}
.kpi{background:var(--surf);border:.5px solid var(--border);border-radius:var(--r);padding:14px;box-shadow:var(--sh)}
.kpi-label{font-size:10px;color:var(--muted);margin-bottom:7px;font-weight:600;text-transform:uppercase;letter-spacing:.6px}
.kpi-val{font-size:20px;font-weight:600;font-family:ui-monospace,monospace;letter-spacing:-.5px}
.kpi-val.sm{font-size:16px}.kpi-sub{font-size:11px;color:var(--hint);margin-top:4px}.kpi-delta{font-size:11.5px;font-weight:600;margin-top:3px}
.up{color:var(--green)}.dn{color:var(--red)}.nu{color:var(--muted)}
.card{background:var(--surf);border:.5px solid var(--border);border-radius:var(--r2);overflow:hidden;margin-bottom:16px;box-shadow:var(--sh)}
.card-header{display:flex;justify-content:space-between;align-items:center;padding:14px 18px;border-bottom:.5px solid var(--border)}
.card-title{font-size:13.5px;font-weight:600}.card-sub{font-size:11px;color:var(--muted);margin-top:2px}
.card-body{padding:18px}.card-footer{padding:12px 18px;border-top:.5px solid var(--border);background:var(--bg);font-size:11.5px;color:var(--muted)}
.table-wrap{overflow-x:auto;-webkit-overflow-scrolling:touch}
table{width:100%;border-collapse:collapse;min-width:500px}
thead th{text-align:left;padding:9px 12px;font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.8px;color:var(--hint);border-bottom:.5px solid var(--border);background:var(--bg);white-space:nowrap}
tbody tr{border-bottom:.5px solid rgba(0,0,0,.04);transition:background .1s}
tbody tr:last-child{border-bottom:none}
tbody tr:hover{background:#f9f9f7}
tbody td{padding:11px 12px;font-size:12.5px;vertical-align:middle}
.t-right{text-align:right}.mono{font-family:ui-monospace,monospace}
.tag{display:inline-block;font-size:10px;font-weight:600;padding:2px 8px;border-radius:20px}
.tag-green{background:var(--gbg);color:var(--green)}.tag-red{background:var(--rbg);color:var(--red)}
.tag-blue{background:var(--bbg);color:var(--blue)}.tag-amber{background:var(--abg);color:var(--amber)}
.tag-purple{background:var(--pbg);color:var(--purple)}.tag-gray{background:var(--bg);color:var(--muted);border:.5px solid var(--bs)}
.form-grid{display:grid;gap:14px}
.g2f{grid-template-columns:repeat(2,1fr)}.g3f{grid-template-columns:repeat(3,1fr)}.full{grid-column:1/-1}
.form-group{display:flex;flex-direction:column;gap:5px}
label{font-size:10.5px;font-weight:600;color:var(--muted);text-transform:uppercase;letter-spacing:.6px}
input,select,textarea{background:var(--bg);border:.5px solid var(--bs);color:var(--text);font-family:inherit;font-size:13px;padding:9px 12px;border-radius:8px;outline:none;width:100%;transition:border-color .15s;-webkit-appearance:none}
input:focus,select:focus{border-color:#0b0b0b;background:#fff}
.form-actions{display:flex;gap:8px;margin-top:16px;flex-wrap:wrap}
.prog{height:6px;background:rgba(0,0,0,.06);border-radius:3px;overflow:hidden;margin-top:4px}
.prog-fill{height:100%;border-radius:3px;transition:width .4s}
.alert{padding:11px 14px;border-radius:9px;font-size:12.5px;margin-bottom:12px;display:flex;gap:9px;align-items:flex-start;line-height:1.5}
.alert-warn{background:var(--abg);color:var(--amber)}.alert-ok{background:var(--gbg);color:var(--green)}
.alert-info{background:var(--bbg);color:var(--blue)}.alert-danger{background:var(--rbg);color:var(--red)}
.flujo{display:grid;grid-template-columns:1fr auto 1fr auto 1fr auto 1fr;gap:8px;align-items:center;margin-bottom:16px}
.flujo-box{background:var(--surf);border:.5px solid var(--border);border-radius:var(--r);padding:12px;text-align:center;box-shadow:var(--sh)}
.flujo-lbl{font-size:9px;color:var(--muted);font-weight:700;text-transform:uppercase;letter-spacing:.6px;margin-bottom:4px}
.flujo-val{font-size:16px;font-weight:600;font-family:ui-monospace,monospace}
.flujo-pct{font-size:10px;color:var(--hint);margin-top:3px}.arrow{color:var(--hint);font-size:16px;text-align:center}
.mkt-bar{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin-bottom:18px}
.mkt-tile{background:var(--surf);border:.5px solid var(--border);border-radius:var(--r);padding:11px 14px;box-shadow:var(--sh)}
.mkt-name{font-size:9.5px;font-weight:700;color:var(--muted);text-transform:uppercase;letter-spacing:.6px;margin-bottom:4px}
.mkt-val{font-size:15px;font-weight:600;font-family:ui-monospace,monospace}.mkt-sub{font-size:10px;color:var(--hint);margin-top:3px}
.modal-overlay{display:none;position:fixed;inset:0;background:rgba(0,0,0,.4);z-index:500}
.modal-overlay.open{display:flex;align-items:center;justify-content:center;padding:16px}
.modal{background:var(--surf);border-radius:var(--r2);width:100%;max-width:600px;max-height:92vh;overflow-y:auto;box-shadow:0 8px 40px rgba(0,0,0,.2)}
.modal-header{padding:15px 20px;border-bottom:.5px solid var(--border);display:flex;justify-content:space-between;align-items:center;position:sticky;top:0;background:var(--surf);z-index:1}
.modal-title{font-size:14px;font-weight:600}.modal-body{padding:20px}.modal-footer{padding:14px 20px;border-top:.5px solid var(--border);display:flex;gap:8px;justify-content:flex-end}
.toast{position:fixed;bottom:20px;right:20px;z-index:9999;background:var(--surf);border:.5px solid var(--bs);color:var(--text);padding:11px 18px;border-radius:11px;font-size:13px;font-weight:500;opacity:0;transform:translateY(8px);transition:all .22s;pointer-events:none;box-shadow:0 4px 24px rgba(0,0,0,.14);border-left:3px solid var(--green)}
.toast.show{opacity:1;transform:translateY(0)}
.empty{text-align:center;padding:32px 20px;color:var(--hint)}.empty-icon{font-size:28px;margin-bottom:10px}
.mob-toggle{display:none;position:fixed;top:12px;left:12px;z-index:300;background:var(--surf);border:.5px solid var(--bs);border-radius:8px;padding:7px;cursor:pointer;box-shadow:var(--sh)}
.period-badge{display:inline-block;font-size:9.5px;font-weight:700;padding:2px 7px;border-radius:20px;text-transform:uppercase;letter-spacing:.4px}
.period-diario{background:#dcfce7;color:#166534}.period-mensual{background:var(--bbg);color:var(--blue)}
.period-trimestral{background:var(--pbg);color:var(--purple)}.period-semestral{background:var(--abg);color:var(--amber)}
.period-anual,.period-vencimiento{background:var(--bg);color:var(--muted);border:.5px solid var(--bs)}
.rend-daily{background:#dcfce7;color:#166534;border-radius:6px;padding:2px 7px;font-family:ui-monospace,monospace;font-size:12px;font-weight:600}
.filter-bar{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:16px;padding:12px 16px;background:var(--surf);border:.5px solid var(--border);border-radius:var(--r)}
.filter-bar select,.filter-bar input{max-width:180px;padding:6px 10px;font-size:12px}
.ahorro-box{background:#dcfce7;border:.5px solid #86efac;border-radius:var(--r);padding:14px;margin-top:14px}
.ahorro-box .ah-title{font-size:11px;font-weight:700;color:#166534;text-transform:uppercase;letter-spacing:.5px;margin-bottom:8px}
.ahorro-box .ah-val{font-size:18px;font-weight:700;font-family:ui-monospace,monospace;color:#166534}
.ahorro-box .ah-sub{font-size:11px;color:#166534;margin-top:3px}
@media(max-width:768px){:root{--sw:0px}.sidebar{transform:translateX(-100%);width:240px}.sidebar.open{transform:translateX(0)}.main{margin-left:0}.mob-toggle{display:flex;align-items:center}.page{padding:56px 14px 24px}.g4,.g3{grid-template-columns:repeat(2,1fr)}.g3f{grid-template-columns:1fr 1fr}.mkt-bar{grid-template-columns:repeat(2,1fr)}.flujo{grid-template-columns:1fr 1fr}.arrow{display:none}}
@media(max-width:480px){.g4,.g3,.g2,.mkt-bar,.g3f,.g2f{grid-template-columns:1fr}.kpi-val{font-size:18px}}
"""

JS_BASE = """
function toggleSidebar(){document.getElementById('sidebar').classList.toggle('open');}
document.addEventListener('click',function(e){var sb=document.getElementById('sidebar');if(sb&&sb.classList.contains('open')&&!sb.contains(e.target)&&!e.target.closest('.mob-toggle'))sb.classList.remove('open');});
var COP=function(n){return '$'+Math.round(n).toLocaleString('es-CO');};
var Pct=function(n){return (+n).toFixed(2)+'%';};
var hoy=function(){return new Date().toISOString().split('T')[0];};
function toast(msg,ok){var t=document.getElementById('toast');if(!t)return;t.textContent=msg;t.style.borderLeftColor=ok===false?'var(--red)':'var(--green)';t.classList.add('show');setTimeout(function(){t.classList.remove('show');},2800);}
async function api(url,method,body){var opts={method:method||'GET',headers:{'Content-Type':'application/json'}};if(body)opts.body=JSON.stringify(body);var r=await fetch(url,opts);return r.json();}
function openModal(id){var el=document.getElementById(id);if(el)el.classList.add('open');}
function closeModal(id){var el=document.getElementById(id);if(el)el.classList.remove('open');}
function fmtFecha(f){if(!f)return'—';var d=new Date(f+'T00:00:00');return d.toLocaleDateString('es-CO',{day:'2-digit',month:'short',year:'numeric'});}
"""


# ══════════════════════════════════════════════════════════════
#  LAYOUT HELPERS
# ══════════════════════════════════════════════════════════════

def base_html(content, nombre="", active="", flashes=None):
    fhtml = ""
    if flashes:
        for cat, msg in flashes:
            cls = "alert-danger" if cat == "error" else "alert-ok"
            ico = "⚠️" if cat == "error" else "Cumplida"
            fhtml += f'<div class="alert {cls}"><span>{ico}</span><div>{msg}</div></div>'
        fhtml = f'<div style="padding:12px 24px 0">{fhtml}</div>'

    nav_items = [
        ("dashboard",      "ti-layout-dashboard",   "Dashboard"),
        ("mercado",        "ti-trending-up",         "Mercado"),
        ("---","","Flujo personal"),
        ("ingresos",       "ti-arrows-down-up",      "Ingresos"),
        ("gastos",         "ti-receipt",             "Gastos"),
        ("ahorro",         "ti-piggy-bank",          "Ahorro"),
        ("---","","Portafolio"),
        ("seguimiento",    "ti-chart-line",          "Seguimiento"),
        ("renta_fija",     "ti-building-bank",       "Renta fija"),
        ("renta_variable", "ti-chart-candle",        "Renta variable"),
        ("inmobiliario",   "ti-building",            "Inmobiliario"),
        ("dolares",        "ti-currency-dollar",     "Dólares"),
        ("rendimientos",   "ti-calendar-stats",      "Rendimientos"),
        ("---","","Pasivos"),
        ("deudas",         "ti-credit-card",         "Deudas"),
    ]
    nav_html = ""
    for item in nav_items:
        if item[0] == "---":
            nav_html += f'<div class="nav-sec">{item[2]}</div>'
        else:
            cls = "active" if item[0] == active else ""
            nav_html += (f'<a class="nav-item {cls}" href="/{item[0]}">'
                         f'<i class="ti {item[1]}" aria-hidden="true" style="font-size:15px;flex-shrink:0"></i>'
                         f'{item[2]}</a>')

    av = nombre[0].upper() if nombre else "U"
    return f"""<!DOCTYPE html>
<html lang="es">
<head><meta charset="UTF-8"/><meta name="viewport" content="width=device-width,initial-scale=1.0,maximum-scale=1.0"/>
<title>FinTrack CO — {active}</title>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/@tabler/icons-webfont@3.19.0/dist/tabler-icons.min.css"/>
<style>{CSS}</style></head>
<body>
<button class="mob-toggle" onclick="toggleSidebar()">☰</button>
<div class="shell">
<aside class="sidebar" id="sidebar">
  <div class="logo"><div class="logo-mark"><svg viewBox="0 0 20 20"><path d="M10 2L2 7v6l8 5 8-5V7L10 2z"/></svg></div>
  <div><div class="logo-name">FinTrack CO</div><div class="logo-ver">v4 Pro · 2026</div></div></div>
  <nav style="flex:1;padding:8px 0;overflow-y:auto">{nav_html}</nav>
  <div class="sidebar-bottom"><div class="user-pill">
    <div class="user-avatar">{av}</div>
    <div style="flex:1;font-size:12px;font-weight:500;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">{nombre}</div>
    <a href="/logout" style="padding:5px;border-radius:6px;background:var(--bg);text-decoration:none;font-size:12px;color:var(--muted)" title="Salir">↩</a>
  </div></div>
</aside>
<main class="main">{fhtml}{content}</main>
</div>
<div class="toast" id="toast"></div>
<script>{JS_BASE}</script>
</body></html>"""

def auth_html(body, flashes=None):
    fhtml = ""
    if flashes:
        for cat, msg in flashes:
            cls = "alert-danger" if cat == "error" else "alert-ok"
            ico = "⚠️" if cat == "error" else "Cumplida"
            fhtml += f'<div class="alert {cls}"><span>{ico}</span><div>{msg}</div></div>'
    return f"""<!DOCTYPE html><html lang="es"><head><meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1.0"/>
<title>FinTrack CO</title><style>{CSS}
.aw{{min-height:100vh;display:flex;align-items:center;justify-content:center;padding:20px;background:var(--bg)}}
.ac{{background:var(--surf);border:.5px solid var(--bs);border-radius:var(--r2);width:100%;max-width:390px;overflow:hidden;box-shadow:0 4px 24px rgba(0,0,0,.1)}}
.ah{{padding:28px 28px 0;text-align:center}}
.al{{width:44px;height:44px;background:#0b0b0b;border-radius:12px;margin:0 auto 16px;display:flex;align-items:center;justify-content:center}}
.al svg{{width:22px;height:22px;fill:#fff}}
.af{{text-align:center;padding:0 28px 24px;font-size:12.5px;color:var(--muted)}}
.af a{{color:var(--blue);text-decoration:none;font-weight:500}}
</style></head><body>
<div class="aw"><div class="ac">
  <div class="ah"><div class="al"><svg viewBox="0 0 20 20"><path d="M10 2L2 7v6l8 5 8-5V7L10 2z"/></svg></div>
  <div style="font-size:18px;font-weight:700;margin-bottom:4px">FinTrack CO</div>
  <div style="font-size:12.5px;color:var(--muted)">Tu gestor financiero personal</div></div>
  {f'<div style="padding:16px 28px 0">{fhtml}</div>' if fhtml else ''}
  {body}
</div></div></body></html>"""

# ══════════════════════════════════════════════════════════════
#  HELPERS
# ══════════════════════════════════════════════════════════════

def hash_pw(pw):  return hashlib.sha256(pw.encode()).hexdigest()
def uid():        return session["user_id"]
def today():      return date.today().isoformat()

def login_required(f):
    @wraps(f)
    def dec(*a, **k):
        if "user_id" not in session: return redirect(url_for("login"))
        return f(*a, **k)
    return dec

def db_del(t, i):
    with get_db() as db: db.execute(f"DELETE FROM {t} WHERE id=? AND usuario=?", (i, uid()))

# ══════════════════════════════════════════════════════════════
#  AUTH
# ══════════════════════════════════════════════════════════════

@app.route("/")
def root():
    return redirect(url_for("dashboard") if "user_id" in session else url_for("login"))

@app.route("/login", methods=["GET","POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email","").strip().lower()
        pw    = request.form.get("password","")
        with get_db() as db:
            u = db.execute("SELECT * FROM usuarios WHERE email=? AND password=?",
                           (email, hash_pw(pw))).fetchone()
        if u:
            session["user_id"]   = u["id"]
            session["user_name"] = u["nombre"]
            return redirect(url_for("dashboard"))
        flash("Email o contraseña incorrectos", "error")
    msgs = get_flashed_messages(with_categories=True)
    body = """<div style="padding:24px 28px 28px">
      <form method="POST" action="/login">
        <div class="form-grid" style="gap:14px">
          <div class="form-group"><label>Email</label>
            <input type="email" name="email" placeholder="tu@email.com" required autocomplete="email"/></div>
          <div class="form-group"><label>Contraseña</label>
            <input type="password" name="password" placeholder="••••••••" required autocomplete="current-password"/></div>
        </div>
        <button type="submit" class="btn btn-primary" style="width:100%;justify-content:center;margin-top:18px;padding:10px">
          Iniciar sesión</button>
      </form></div>
    <div class="af">¿No tienes cuenta? <a href="/registro">Crear cuenta gratis</a></div>"""
    return auth_html(body, msgs)

@app.route("/registro", methods=["GET","POST"])
def registro():
    if request.method == "POST":
        nombre = request.form.get("nombre","").strip()
        email  = request.form.get("email","").strip().lower()
        pw     = request.form.get("password","")
        pw2    = request.form.get("password2","")
        if pw != pw2:           flash("Las contraseñas no coinciden","error")
        elif len(pw) < 6:       flash("Contraseña mínimo 6 caracteres","error")
        else:
            try:
                with get_db() as db:
                    db.execute("INSERT INTO usuarios (nombre,email,password) VALUES (?,?,?)",
                               (nombre,email,hash_pw(pw)))
                flash("Cuenta creada. Inicia sesión.","ok")
                return redirect(url_for("login"))
            except sqlite3.IntegrityError: flash("Email ya registrado","error")
    msgs = get_flashed_messages(with_categories=True)
    body = """<div style="padding:24px 28px 28px">
      <form method="POST" action="/registro">
        <div class="form-grid" style="gap:14px">
          <div class="form-group"><label>Nombre completo</label>
            <input type="text" name="nombre" placeholder="Ana García" required autocomplete="name"/></div>
          <div class="form-group"><label>Email</label>
            <input type="email" name="email" placeholder="tu@email.com" required autocomplete="email"/></div>
          <div class="form-group"><label>Contraseña (mínimo 6 caracteres)</label>
            <input type="password" name="password" placeholder="••••••••" required autocomplete="new-password"/></div>
          <div class="form-group"><label>Confirmar contraseña</label>
            <input type="password" name="password2" placeholder="••••••••" required autocomplete="new-password"/></div>
        </div>
        <button type="submit" class="btn btn-primary" style="width:100%;justify-content:center;margin-top:18px;padding:10px">
          Crear cuenta</button>
      </form></div>
    <div class="af">¿Ya tienes cuenta? <a href="/login">Iniciar sesión</a></div>"""
    return auth_html(body, msgs)

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# ══════════════════════════════════════════════════════════════
#  DASHBOARD
# ══════════════════════════════════════════════════════════════

@app.route("/dashboard")
@login_required
def dashboard():
    content = """<div class="page">
  <div class="topbar">
    <div><div class="page-title">Dashboard</div><div class="page-sub" id="fhoy"></div></div>
    <div style="display:flex;gap:8px">
      <button class="btn btn-sm" onclick="openAlerts()" id="bell-btn">🔔 Alertas</button>
      <button class="btn btn-sm" onclick="location.reload()">↺</button>
    </div>
  </div>
  <div class="mkt-bar">
    <div class="mkt-tile"><div class="mkt-name">USD / COP</div><div class="mkt-val" id="mk-usd">…</div><div class="mkt-sub" id="mk-us">TRM</div></div>
    <div class="mkt-tile"><div class="mkt-name">Oro XAU/USD</div><div class="mkt-val" id="mk-oro">…</div><div class="mkt-sub">Por onza</div></div>
    <div class="mkt-tile"><div class="mkt-name">Tasa BR</div><div class="mkt-val">9.50%</div><div class="mkt-sub">Banco República</div></div>
    <div class="mkt-tile"><div class="mkt-name">IPC anual</div><div class="mkt-val">5.2%</div><div class="mkt-sub">Inflación CO</div></div>
  </div>
  <div class="flujo">
    <div class="flujo-box" style="border-top:2px solid var(--green)"><div class="flujo-lbl">Ingresos</div><div class="flujo-val up" id="f-ing">$0</div><div class="flujo-pct">total</div></div>
    <div class="arrow">→</div>
    <div class="flujo-box" style="border-top:2px solid var(--red)"><div class="flujo-lbl">Gastos</div><div class="flujo-val dn" id="f-gas">$0</div><div class="flujo-pct" id="f-gp">—</div></div>
    <div class="arrow">→</div>
    <div class="flujo-box" style="border-top:2px solid var(--blue)"><div class="flujo-lbl">Ahorro</div><div class="flujo-val" style="color:var(--blue)" id="f-aho">$0</div><div class="flujo-pct" id="f-ap">—</div></div>
    <div class="arrow">→</div>
    <div class="flujo-box" style="border-top:2px solid var(--purple)"><div class="flujo-lbl">Portafolio</div><div class="flujo-val" style="color:var(--purple)" id="f-port">$0</div><div class="flujo-pct">acumulado</div></div>
  </div>
  <div id="flujo-alert"></div>
  <div class="kpi-grid g4">
    <div class="kpi"><div class="kpi-label">Patrimonio neto</div><div class="kpi-val" id="k-neto">$0</div><div class="kpi-delta" id="k-neto-t">Activos − Pasivos</div></div>
    <div class="kpi"><div class="kpi-label">Portafolio total</div><div class="kpi-val" id="k-port">$0</div><div class="kpi-sub">RF+RV+Inmo+USD</div></div>
    <div class="kpi"><div class="kpi-label">Rend. hoy (diario)</div><div class="kpi-val up" id="k-rdia">$0</div><div class="kpi-sub">Cuentas activas</div></div>
    <div class="kpi"><div class="kpi-label">Rend. mensual</div><div class="kpi-val up" id="k-rmes">$0</div></div>
  </div>
  <div class="kpi-grid g4">
    <div class="kpi"><div class="kpi-label">Rend. anual proyectado</div><div class="kpi-val up" id="k-raño">$0</div></div>
    <div class="kpi"><div class="kpi-label">Rend. acumulado real</div><div class="kpi-val up" id="k-racum">$0</div><div class="kpi-sub">Desde inicio</div></div>
    <div class="kpi"><div class="kpi-label">Total deudas</div><div class="kpi-val dn" id="k-deu">$0</div><div class="kpi-sub" id="k-deu-r">Ratio: —</div></div>
    <div class="kpi"><div class="kpi-label">Cuotas / mes</div><div class="kpi-val dn" id="k-deu-c">$0</div></div>
  </div>
  <div id="alertas-panel"></div>
  <div id="modal-alertas" class="modal-overlay" onclick="if(event.target===this)closeModal('modal-alertas')">
    <div class="modal" style="max-width:420px">
      <div class="modal-header"><div class="modal-title">🔔 Alertas activas</div>
        <button class="btn btn-sm" onclick="closeModal('modal-alertas')">✕</button></div>
      <div class="modal-body" id="modal-alertas-body"></div>
    </div>
  </div>
</div>
<script>
var TRM=4200,RESUMEN=null;
document.getElementById('fhoy').textContent=new Date().toLocaleDateString('es-CO',{weekday:'long',year:'numeric',month:'long',day:'numeric'});
async function loadDash(){
  try{var m=await api('/api/market');TRM=m.usd_cop||4200;
    document.getElementById('mk-usd').textContent=TRM.toLocaleString('es-CO');
    document.getElementById('mk-us').textContent='Actualizado hoy';
    if(m.gold_usd)document.getElementById('mk-oro').textContent='$'+Math.round(m.gold_usd).toLocaleString('en-US');
  }catch(e){}
  try{var r=await api('/api/resumen?trm='+TRM);RESUMEN=r;
    document.getElementById('f-ing').textContent=COP(r.ingresos);
    document.getElementById('f-gas').textContent=COP(r.gastos);
    document.getElementById('f-aho').textContent=COP(r.ahorro);
    document.getElementById('f-port').textContent=COP(r.portafolio);
    if(r.ingresos>0){document.getElementById('f-gp').textContent=(r.gastos/r.ingresos*100).toFixed(1)+'% del ingreso';document.getElementById('f-ap').textContent=(r.ahorro_mensual/r.ingresos*100).toFixed(1)+'% del ingreso';}
    var fa=document.getElementById('flujo-alert');
    if(r.ingresos>0){var pG=r.gastos/r.ingresos*100;
      if(pG>80)fa.innerHTML='<div class="alert alert-danger">🔴 <div><b>Gastos críticos ('+pG.toFixed(1)+'%)</b> Reduce gastos variables urgentemente.</div></div>';
      else if(pG>60)fa.innerHTML='<div class="alert alert-warn">⚠️ <div><b>Gastos elevados ('+pG.toFixed(1)+'%)</b> Sigue la regla 50/30/20.</div></div>';
      else fa.innerHTML='<div class="alert alert-ok">✅ <div><b>Gastos saludables ('+pG.toFixed(1)+'%)</b></div></div>';}
    var rn=r.rendimientos||{};
    document.getElementById('k-neto').textContent=COP(r.neto);
    document.getElementById('k-neto-t').className='kpi-delta '+(r.neto>=0?'up':'dn');
    document.getElementById('k-neto-t').textContent=r.neto>=0?'Activos > Pasivos ✅':'⚠️ Pasivos > Activos';
    document.getElementById('k-port').textContent=COP(r.portafolio);
    document.getElementById('k-rdia').textContent=COP(rn.total_diario||0);
    document.getElementById('k-rmes').textContent=COP(rn.total_mensual||0);
    document.getElementById('k-raño').textContent=COP(rn.total_anual||0);
    document.getElementById('k-racum').textContent=COP(rn.rf_acumulado||0);
    document.getElementById('k-deu').textContent=COP(r.deudas);
    document.getElementById('k-deu-c').textContent=COP(r.deudas_cuota||0);
    document.getElementById('k-deu-r').textContent='Ratio: '+(r.portafolio>0?(r.deudas/r.portafolio*100).toFixed(1):0)+'%';
    var ap=document.getElementById('alertas-panel');
    if(r.alertas&&r.alertas.length){ap.innerHTML=r.alertas.map(function(a){return'<div class="alert '+(a.tipo==='critical'?'alert-danger':a.tipo==='warning'?'alert-warn':'alert-info')+'"><span>'+(a.tipo==='critical'?'🔴':'⚠️')+'</span><div>'+a.msg+'</div></div>';}).join('');
      var crits=r.alertas.filter(function(a){return a.tipo==='critical'||a.tipo==='warning';}).length;
      if(crits>0)document.getElementById('bell-btn').textContent=crits+' alertas';}
  }catch(e){console.error(e);}
}
function openAlerts(){if(!RESUMEN)return;var body=document.getElementById('modal-alertas-body');var al=RESUMEN.alertas||[];
  if(!al.length){body.innerHTML='<div class="empty"><div class="empty-icon">✅</div>Sin alertas</div>';return;}
  body.innerHTML=al.map(function(a){return'<div style="background:var(--bg);border-radius:9px;padding:12px 14px;margin-bottom:8px;border-left:3px solid '+(a.tipo==='critical'?'var(--red)':a.tipo==='warning'?'var(--amber)':'var(--blue)')+'"><div style="font-size:12.5px;font-weight:600;margin-bottom:3px">'+(a.tipo==='critical'?'🔴 Crítico':'⚠️ Aviso')+'</div><div style="font-size:12px;color:var(--muted)">'+a.msg+'</div></div>';}).join('');
  openModal('modal-alertas');}
loadDash();
</script>"""
    return base_html(content, session["user_name"], "dashboard")


# ══════════════════════════════════════════════════════════════
#  PÁGINA GENÉRICA
# ══════════════════════════════════════════════════════════════

def generic_page(titulo, active):
    c = f"""<div class="page">
  <div class="topbar"><div><div class="page-title">{titulo}</div></div></div>
  <div class="alert alert-info"><div>Sección <b>{titulo}</b> — en desarrollo. Las funciones principales están en Renta Fija, Deudas y Metas de Ahorro.</div></div>
  <div class="card"><div class="card-body"><div class="empty"><div class="empty-icon">📊</div>Próximamente</div></div></div>
</div>"""
    return base_html(c, session["user_name"], active)

@app.route("/mercado")
@login_required
def mercado():
    c = """<div class="page">
  <div class="topbar"><div><div class="page-title">Mercado</div><div class="page-sub">Indicadores en tiempo real</div></div>
    <div style="display:flex;gap:8px"><button class="btn btn-sm" onclick="cargar()">Actualizar</button></div></div>
  <div class="kpi-grid g4">
    <div class="kpi"><div class="kpi-label">USD/COP (TRM)</div><div class="kpi-val sm" id="m-usd">…</div><div class="kpi-sub" id="m-us">Tiempo real</div></div>
    <div class="kpi"><div class="kpi-label">Oro XAU/USD</div><div class="kpi-val sm" id="m-oro">…</div><div class="kpi-sub">Por onza</div></div>
    <div class="kpi"><div class="kpi-label">Tasa Banco Rep.</div><div class="kpi-val sm">9.50% E.A.</div></div>
    <div class="kpi"><div class="kpi-label">DTF</div><div class="kpi-val sm">11.28% E.A.</div><div class="kpi-sub">Ref. CDT</div></div>
  </div>
  <div class="card"><div class="card-header"><div><div class="card-title">Top acciones COLCAP</div><div class="card-sub">ROA · ROE · P/E · Yield · Beta — referencial</div></div></div>
    <div class="table-wrap"><table><thead><tr><th>Empresa</th><th>Ticker</th><th>Sector</th><th class="t-right">ROA</th><th class="t-right">ROE</th><th class="t-right">P/E</th><th class="t-right">Yield</th><th class="t-right">Beta</th><th>Perfil</th></tr></thead>
    <tbody>
      <tr><td><b>Bancolombia</b></td><td><span class="tag tag-gray">PFBCOL</span></td><td style="color:var(--muted)">Financiero</td><td class="mono t-right up">1.8%</td><td class="mono t-right up">14.2%</td><td class="mono t-right">7.1x</td><td class="mono t-right up">4.5%</td><td class="mono t-right">0.72</td><td><span class="tag tag-green">Sólido</span></td></tr>
      <tr><td><b>Ecopetrol</b></td><td><span class="tag tag-gray">ECOPETROL</span></td><td style="color:var(--muted)">Energía</td><td class="mono t-right up">5.2%</td><td class="mono t-right up">18.1%</td><td class="mono t-right">5.3x</td><td class="mono t-right up">12.1%</td><td class="mono t-right">1.45</td><td><span class="tag tag-amber">Volátil</span></td></tr>
      <tr><td><b>GEB</b></td><td><span class="tag tag-gray">GEB</span></td><td style="color:var(--muted)">Utilities</td><td class="mono t-right up">3.1%</td><td class="mono t-right up">11.4%</td><td class="mono t-right">12.4x</td><td class="mono t-right up">5.8%</td><td class="mono t-right">0.55</td><td><span class="tag tag-green">Defensivo</span></td></tr>
      <tr><td><b>Nutresa</b></td><td><span class="tag tag-gray">NUTRESA</span></td><td style="color:var(--muted)">Consumo</td><td class="mono t-right up">4.1%</td><td class="mono t-right up">12.6%</td><td class="mono t-right">13.1x</td><td class="mono t-right up">1.8%</td><td class="mono t-right">0.62</td><td><span class="tag tag-green">Sólido</span></td></tr>
      <tr><td><b>ISA</b></td><td><span class="tag tag-gray">ISA</span></td><td style="color:var(--muted)">Infraestructura</td><td class="mono t-right up">3.8%</td><td class="mono t-right up">15.2%</td><td class="mono t-right">11.2x</td><td class="mono t-right up">3.4%</td><td class="mono t-right">0.68</td><td><span class="tag tag-green">Sólido</span></td></tr>
    </tbody></table></div>
    <div class="card-footer">* Referencial. No constituye asesoría de inversión.</div>
  </div>
</div>
<script>
async function cargar(){
  try{var m=await api('/api/market');
    if(m.usd_cop){document.getElementById('m-usd').textContent=Math.round(m.usd_cop).toLocaleString('es-CO');document.getElementById('m-us').textContent='Actualizado '+new Date().toLocaleTimeString('es-CO',{hour:'2-digit',minute:'2-digit'});}
    if(m.gold_usd)document.getElementById('m-oro').textContent='$'+Math.round(m.gold_usd).toLocaleString('en-US');
  }catch(e){}
}
cargar();
</script>"""
    return base_html(c, session["user_name"], "mercado")

@app.route("/ingresos")
@login_required
def ingresos():
    c = """<div class="page">
  <div class="topbar">
    <div><div class="page-title">Ingresos</div><div class="page-sub">Salario · Freelance · Arriendos · Dividendos · Fijos a término</div></div>
    <div><button class="btn btn-primary btn-sm" onclick="resetIng();openModal('modal-ing')">+ Nuevo ingreso</button></div>
  </div>

  <div class="kpi-grid g4">
    <div class="kpi"><div class="kpi-label">Total registrado</div><div class="kpi-val up" id="ing-tot">$0</div></div>
    <div class="kpi"><div class="kpi-label">Ingresos mensuales</div><div class="kpi-val" id="ing-men">$0</div><div class="kpi-sub">Recurrentes activos</div></div>
    <div class="kpi"><div class="kpi-label">Proyección anual</div><div class="kpi-val" id="ing-anual">$0</div><div class="kpi-sub">Mensual × 12</div></div>
    <div class="kpi"><div class="kpi-label">Registros</div><div class="kpi-val" id="ing-cnt">0</div></div>
  </div>

  <!-- Flujo de caja anual -->
  <div class="card" id="flujo-card" style="display:none">
    <div class="card-header"><div><div class="card-title">Flujo de caja anual proyectado</div>
      <div class="card-sub">Ingresos a término fijo con fecha inicio y fin</div></div></div>
    <div class="card-body">
      <div style="display:grid;grid-template-columns:repeat(6,1fr);gap:8px" id="flujo-meses"></div>
    </div>
  </div>

  <div class="card">
    <div class="card-header">
      <div class="card-title">Mis ingresos</div>
      <div style="display:flex;gap:8px">
        <select id="f-ing-period" onchange="filtrarIng()" style="font-size:11px;padding:4px 8px;border-radius:var(--r);border:.5px solid var(--border-s);background:var(--bg);color:var(--text)">
          <option value="">Todos los períodos</option>
          <option value="mensual">Mensual</option>
          <option value="quincenal">Quincenal</option>
          <option value="único">Único / Esporádico</option>
          <option value="término fijo">Término fijo</option>
          <option value="anual">Anual</option>
        </select>
      </div>
    </div>
    <div class="table-wrap"><table>
      <thead><tr>
        <th>Categoría</th><th>Descripción</th>
        <th class="t-right">Monto</th><th>Período</th>
        <th>Fecha inicio</th><th>Fecha fin</th>
        <th>Estado</th><th>Notas</th><th>Acciones</th>
      </tr></thead>
      <tbody id="tabla-ing"><tr><td colspan="9"><div class="empty">Cargando…</div></td></tr></tbody>
    </table></div>
  </div>
</div>

<!-- MODAL NUEVO/EDITAR INGRESO -->
<div id="modal-ing" class="modal-overlay" onclick="if(event.target===this){resetIng();closeModal('modal-ing')}">
  <div class="modal" style="max-width:520px">
    <div class="modal-header"><div class="modal-title" id="ing-title">Nuevo ingreso</div>
      <button class="btn btn-sm" onclick="resetIng();closeModal('modal-ing')">✕</button></div>
    <div class="modal-body">
      <input type="hidden" id="ing-id"/>
      <div class="form-grid g2f">
        <div class="form-group"><label>Categoría</label><select id="ing-cat">
          <option>Salario / Nómina</option><option>Freelance / Honorarios</option>
          <option>Arriendo recibido</option><option>Dividendos</option>
          <option>Pensión / Jubilación</option><option>Transferencia familiar</option>
          <option>Rendimiento inversión</option><option>Comisiones</option>
          <option>Venta activo</option><option>Prima / Bono</option>
          <option>Otro</option>
        </select></div>
        <div class="form-group"><label>Período / Frecuencia</label><select id="ing-period" onchange="onChangePeriod()">
          <option value="mensual">Mensual — recurrente</option>
          <option value="quincenal">Quincenal</option>
          <option value="semanal">Semanal</option>
          <option value="término fijo">Término fijo (con fecha fin)</option>
          <option value="anual">Anual</option>
          <option value="único">Único / Esporádico</option>
        </select></div>
        <div class="form-group full"><label>Descripción</label>
          <input type="text" id="ing-desc" placeholder="Empresa XYZ, proyecto freelance, contrato…"/></div>
        <div class="form-group"><label>Monto por período ($)</label>
          <input type="number" id="ing-monto" placeholder="6000000"/></div>
        <div class="form-group"><label>Fecha inicio</label>
          <input type="date" id="ing-fecha"/></div>
        <div class="form-group" id="row-fecha-fin"><label>Fecha fin <span style="color:var(--blue);font-size:10px">(término fijo)</span></label>
          <input type="date" id="ing-fecha-fin" placeholder="Dejar vacío si es indefinido"/></div>
        <div class="form-group full"><label>Notas</label>
          <input type="text" id="ing-notas" placeholder="NIT, contrato, observaciones…"/></div>
      </div>
      <!-- Preview flujo si es término fijo -->
      <div id="ing-preview" style="display:none;margin-top:12px;background:var(--bg);border-radius:var(--r);padding:14px">
        <div style="font-size:10px;font-weight:700;color:var(--muted);text-transform:uppercase;letter-spacing:.6px;margin-bottom:8px">Flujo proyectado</div>
        <div style="display:grid;grid-template-columns:repeat(3,1fr);gap:8px;text-align:center">
          <div><div style="font-size:10px;color:var(--muted)">Duración</div><div class="mono" id="prev-dur" style="font-size:15px;font-weight:600;color:var(--blue)"></div></div>
          <div><div style="font-size:10px;color:var(--muted)">Total estimado</div><div class="mono up" id="prev-tot" style="font-size:15px;font-weight:600"></div></div>
          <div><div style="font-size:10px;color:var(--muted)">Ingreso anual</div><div class="mono" id="prev-anual" style="font-size:15px;font-weight:600"></div></div>
        </div>
      </div>
    </div>
    <div class="modal-footer">
      <button class="btn" onclick="resetIng();closeModal('modal-ing')">Cancelar</button>
      <button class="btn btn-primary" onclick="guardarIng()">Guardar</button>
    </div>
  </div>
</div>

<script>
var TODOS_ING = [];

function onChangePeriod(){
  var p = document.getElementById('ing-period').value;
  var rowFin = document.getElementById('row-fecha-fin');
  rowFin.style.display = (p==='término fijo' || p==='anual') ? 'flex' : 'none';
  calcPreviewIng();
}

function calcPreviewIng(){
  var p = document.getElementById('ing-period').value;
  var monto = parseFloat(document.getElementById('ing-monto').value)||0;
  var fi = document.getElementById('ing-fecha').value;
  var ff = document.getElementById('ing-fecha-fin').value;
  var prev = document.getElementById('ing-preview');
  if(p!=='término fijo'||!monto||!fi||!ff){prev.style.display='none';return;}
  var d1=new Date(fi),d2=new Date(ff);
  var meses=Math.max(0,Math.round((d2-d1)/(1000*60*60*24*30)));
  var total=monto*meses;
  var anual=monto*12;
  document.getElementById('prev-dur').textContent=meses+' meses';
  document.getElementById('prev-tot').textContent=COP(total);
  document.getElementById('prev-anual').textContent=COP(anual);
  prev.style.display='block';
}

function resetIng(){
  ['ing-cat','ing-period'].forEach(function(id){var el=document.getElementById(id);if(el)el.selectedIndex=0;});
  ['ing-desc','ing-monto','ing-fecha','ing-fecha-fin','ing-notas','ing-id'].forEach(function(id){var el=document.getElementById(id);if(el)el.value='';});
  document.getElementById('ing-fecha').value=hoy();
  document.getElementById('ing-title').textContent='Nuevo ingreso';
  document.getElementById('ing-preview').style.display='none';
  document.getElementById('row-fecha-fin').style.display='none';
}

function filtrarIng(){
  var p=document.getElementById('f-ing-period').value;
  var rows=p?TODOS_ING.filter(function(r){return r.period===p;}):TODOS_ING;
  renderTablaIng(rows);
}

function estadoIngreso(r){
  if(!r.fecha_fin) return {txt:'Activo',cls:'tag-green'};
  var hoy2=new Date();var fin=new Date(r.fecha_fin);
  if(fin<hoy2) return {txt:'Finalizado',cls:'tag-gray'};
  var dias=Math.ceil((fin-hoy2)/(1000*60*60*24));
  if(dias<=30) return {txt:'Vence en '+dias+'d',cls:'tag-amber'};
  return {txt:'Activo',cls:'tag-green'};
}

function renderTablaIng(rows){
  var t=document.getElementById('tabla-ing');
  if(!rows.length){t.innerHTML='<tr><td colspan="9"><div class="empty">Sin ingresos con esos filtros</div></td></tr>';return;}
  t.innerHTML=rows.map(function(r){
    var est=estadoIngreso(r);
    return'<tr>'
      +'<td><span class="tag tag-green">'+r.cat+'</span></td>'
      +'<td style="font-weight:500">'+r.desc+'</td>'
      +'<td class="mono t-right up" style="font-weight:600">'+COP(r.monto)+'</td>'
      +'<td><span class="tag tag-gray">'+r.period+'</span></td>'
      +'<td style="color:var(--muted)">'+fmtFecha(r.fecha)+'</td>'
      +'<td style="color:var(--muted)">'+(r.fecha_fin?fmtFecha(r.fecha_fin):'Indefinido')+'</td>'
      +'<td><span class="tag '+est.cls+'">'+est.txt+'</span></td>'
      +'<td style="color:var(--muted);font-size:11.5px">'+(r.notas||'')+'</td>'
      +'<td><div style="display:flex;gap:4px">'
        +'<button class="btn btn-edit btn-xs" onclick="editarIng('+r.id+')">✏️</button>'
        +'<button class="btn btn-danger btn-xs" onclick="elimIng('+r.id+')">🗑</button>'
      +'</div></td></tr>';
  }).join('');
}

function calcularFlujoCaja(rows){
  // Solo ingresos con fecha_fin definida (término fijo)
  var conFin=rows.filter(function(r){return r.fecha_fin;});
  if(!conFin.length){document.getElementById('flujo-card').style.display='none';return;}
  document.getElementById('flujo-card').style.display='block';
  // Calcular por mes (próximos 12 meses)
  var hoyD=new Date();
  var meses=[];
  for(var i=0;i<12;i++){
    var m=new Date(hoyD.getFullYear(),hoyD.getMonth()+i,1);
    var label=m.toLocaleDateString('es-CO',{month:'short',year:'2-digit'});
    var total=0;
    conFin.forEach(function(r){
      var fi=new Date(r.fecha);var ff=new Date(r.fecha_fin);
      if(m>=fi&&m<=ff){total+=parseFloat(r.monto)||0;}
    });
    // Agregar mensuales sin fecha_fin
    rows.filter(function(r){return !r.fecha_fin&&r.period==='mensual';}).forEach(function(r){
      total+=parseFloat(r.monto)||0;
    });
    meses.push({label:label,total:total});
  }
  var maxVal=Math.max.apply(null,meses.map(function(m){return m.total;}));
  document.getElementById('flujo-meses').innerHTML=meses.map(function(m){
    var pct=maxVal>0?Math.round(m.total/maxVal*100):0;
    return'<div style="text-align:center">'
      +'<div style="font-size:11px;font-weight:600;color:var(--green);margin-bottom:4px">'+COP(m.total)+'</div>'
      +'<div style="height:60px;display:flex;align-items:flex-end;justify-content:center">'
        +'<div style="width:28px;background:var(--green);border-radius:3px 3px 0 0;height:'+pct+'%;min-height:2px;opacity:.75"></div>'
      +'</div>'
      +'<div style="font-size:10px;color:var(--muted);margin-top:3px">'+m.label+'</div>'
    +'</div>';
  }).join('');
}

async function cargarIng(){
  var rows=await api('/api/ingresos');
  TODOS_ING=rows;
  var tot=0,men=0;
  rows.forEach(function(r){
    tot+=parseFloat(r.monto)||0;
    if(r.period==='mensual'&&estadoIngreso(r).txt!=='Finalizado')men+=parseFloat(r.monto)||0;
  });
  document.getElementById('ing-tot').textContent=COP(tot);
  document.getElementById('ing-men').textContent=COP(men);
  document.getElementById('ing-anual').textContent=COP(men*12);
  document.getElementById('ing-cnt').textContent=rows.length;
  renderTablaIng(rows);
  calcularFlujoCaja(rows);
}

async function editarIng(id){
  var r=await api('/api/ingresos/'+id);
  document.getElementById('ing-id').value=id;
  document.getElementById('ing-cat').value=r.cat||'';
  document.getElementById('ing-period').value=r.period||'mensual';
  document.getElementById('ing-desc').value=r.desc||'';
  document.getElementById('ing-monto').value=r.monto||'';
  document.getElementById('ing-fecha').value=r.fecha||'';
  document.getElementById('ing-fecha-fin').value=r.fecha_fin||'';
  document.getElementById('ing-notas').value=r.notas||'';
  document.getElementById('ing-title').textContent='Editar ingreso';
  onChangePeriod();
  openModal('modal-ing');
}

async function guardarIng(){
  var id=document.getElementById('ing-id').value;
  var d={
    cat:    document.getElementById('ing-cat').value,
    period: document.getElementById('ing-period').value,
    desc:   document.getElementById('ing-desc').value,
    monto:  parseFloat(document.getElementById('ing-monto').value)||0,
    fecha:  document.getElementById('ing-fecha').value||hoy(),
    fecha_fin: document.getElementById('ing-fecha-fin').value||null,
    notas:  document.getElementById('ing-notas').value,
  };
  if(!d.monto){toast('Ingresa el monto',false);return;}
  if(!d.desc){toast('Agrega una descripción',false);return;}
  var r=id?await api('/api/ingresos/'+id,'PUT',d):await api('/api/ingresos','POST',d);
  if(r.ok||r.id){toast(id?'Ingreso actualizado ✅':'Ingreso guardado ✅');resetIng();closeModal('modal-ing');cargarIng();}
  else toast('Error al guardar',false);
}

async function elimIng(id){
  if(!confirm('¿Eliminar este ingreso?'))return;
  await api('/api/ingresos/'+id,'DELETE');
  toast('Eliminado');cargarIng();
}

resetIng();cargarIng();
</script>"""
    return base_html(c, session["user_name"], "ingresos")

@app.route("/gastos")
@login_required
def gastos():
    c = """<div class="page">
  <div class="topbar">
    <div><div class="page-title">Gastos</div><div class="page-sub">Fijos y variables — control de presupuesto</div></div>
    <div><button class="btn btn-primary btn-sm" onclick="resetGas();openModal('modal-gas')">+ Nuevo gasto</button></div>
  </div>
  <div class="kpi-grid g3">
    <div class="kpi"><div class="kpi-label">Total gastos</div><div class="kpi-val dn" id="gas-tot">$0</div></div>
    <div class="kpi"><div class="kpi-label">Gastos fijos</div><div class="kpi-val dn" id="gas-fijo">$0</div></div>
    <div class="kpi"><div class="kpi-label">Gastos variables</div><div class="kpi-val" id="gas-var">$0</div></div>
  </div>
  <div class="card"><div class="card-header"><div class="card-title">Mis gastos</div></div>
    <div class="table-wrap"><table>
      <thead><tr><th>Categoría</th><th>Descripción</th><th class="t-right">Monto</th><th>Tipo</th><th class="t-right">Presupuesto</th><th>Fecha</th><th>Acciones</th></tr></thead>
      <tbody id="tabla-gas"><tr><td colspan="7"><div class="empty">Cargando…</div></td></tr></tbody>
    </table></div>
  </div>
</div>
<div id="modal-gas" class="modal-overlay" onclick="if(event.target===this){resetGas();closeModal('modal-gas')}">
  <div class="modal" style="max-width:480px">
    <div class="modal-header"><div class="modal-title" id="gas-title">Nuevo gasto</div>
      <button class="btn btn-sm" onclick="resetGas();closeModal('modal-gas')">✕</button></div>
    <div class="modal-body">
      <input type="hidden" id="gas-id"/>
      <div class="form-grid g2f">
        <div class="form-group"><label>Categoría</label><select id="gas-cat">
          <option>Vivienda / Arriendo</option><option>Alimentación</option>
          <option>Transporte</option><option>Salud</option><option>Educación</option>
          <option>Entretenimiento</option><option>Ropa</option><option>Servicios (luz/agua/internet)</option>
          <option>Seguros</option><option>Deudas / Cuotas</option><option>Otro</option>
        </select></div>
        <div class="form-group"><label>Tipo</label><select id="gas-tipo">
          <option value="fijo">Fijo (mensual recurrente)</option>
          <option value="variable">Variable</option>
        </select></div>
        <div class="form-group full"><label>Descripción</label>
          <input type="text" id="gas-desc" placeholder="Netflix, mercado semanal, gasolina…"/></div>
        <div class="form-group"><label>Monto ($)</label>
          <input type="number" id="gas-monto" placeholder="150000"/></div>
        <div class="form-group"><label>Presupuesto ($)</label>
          <input type="number" id="gas-presup" placeholder="200000"/></div>
        <div class="form-group"><label>Fecha</label><input type="date" id="gas-fecha"/></div>
        <div class="form-group full"><label>Notas</label>
          <input type="text" id="gas-notas" placeholder=""/></div>
      </div>
    </div>
    <div class="modal-footer">
      <button class="btn" onclick="resetGas();closeModal('modal-gas')">Cancelar</button>
      <button class="btn btn-primary" onclick="guardarGas()">Guardar</button>
    </div>
  </div>
</div>
<script>
function resetGas(){['gas-cat','gas-tipo'].forEach(function(id){var el=document.getElementById(id);if(el)el.selectedIndex=0;});['gas-desc','gas-monto','gas-presup','gas-fecha','gas-notas','gas-id'].forEach(function(id){var el=document.getElementById(id);if(el)el.value='';});document.getElementById('gas-fecha').value=hoy();document.getElementById('gas-title').textContent='Nuevo gasto';}
async function cargarGas(){
  var rows=await api('/api/gastos');
  var t=document.getElementById('tabla-gas');
  var tot=0,fijo=0,vari=0;
  if(!rows.length){t.innerHTML='<tr><td colspan="7"><div class="empty">Sin gastos registrados</div></td></tr>';['gas-tot','gas-fijo','gas-var'].forEach(function(id){document.getElementById(id).textContent='$0';});return;}
  rows.forEach(function(r){tot+=r.monto||0;if(r.tipo==='fijo')fijo+=r.monto||0;else vari+=r.monto||0;});
  document.getElementById('gas-tot').textContent=COP(tot);
  document.getElementById('gas-fijo').textContent=COP(fijo);
  document.getElementById('gas-var').textContent=COP(vari);
  t.innerHTML=rows.map(function(r){
    var pct=r.presup>0?Math.min(100,r.monto/r.presup*100):0;
    var cls=pct>100?'tag-red':pct>80?'tag-amber':'tag-green';
    return'<tr><td><span class="tag tag-gray">'+r.cat+'</span></td><td>'+r.desc+'</td>'
      +'<td class="mono t-right dn">'+COP(r.monto)+'</td>'
      +'<td><span class="tag '+(r.tipo==='fijo'?'tag-blue':'tag-gray')+'">'+r.tipo+'</span></td>'
      +'<td class="t-right">'+(r.presup>0?'<span class="tag '+cls+'">'+pct.toFixed(0)+'% de '+COP(r.presup)+'</span>':'—')+'</td>'
      +'<td style="color:var(--muted)">'+fmtFecha(r.fecha)+'</td>'
      +'<td><div style="display:flex;gap:4px"><button class="btn btn-edit btn-xs" onclick="editarGas('+r.id+')">✏️</button><button class="btn btn-danger btn-xs" onclick="elimGas('+r.id+')">🗑</button></div></td></tr>';
  }).join('');
}
async function editarGas(id){var r=await api('/api/gastos/'+id);document.getElementById('gas-id').value=id;document.getElementById('gas-cat').value=r.cat||'';document.getElementById('gas-tipo').value=r.tipo||'variable';document.getElementById('gas-desc').value=r.desc||'';document.getElementById('gas-monto').value=r.monto||'';document.getElementById('gas-presup').value=r.presup||'';document.getElementById('gas-fecha').value=r.fecha||'';document.getElementById('gas-notas').value=r.notas||'';document.getElementById('gas-title').textContent='Editar gasto';openModal('modal-gas');}
async function guardarGas(){var id=document.getElementById('gas-id').value;var d={cat:document.getElementById('gas-cat').value,tipo:document.getElementById('gas-tipo').value,desc:document.getElementById('gas-desc').value,monto:parseFloat(document.getElementById('gas-monto').value)||0,presup:parseFloat(document.getElementById('gas-presup').value)||0,fecha:document.getElementById('gas-fecha').value,notas:document.getElementById('gas-notas').value};if(!d.monto){toast('Ingresa el monto',false);return;}var r=id?await api('/api/gastos/'+id,'PUT',d):await api('/api/gastos','POST',d);if(r.ok||r.id){toast('Guardado ✅');resetGas();closeModal('modal-gas');cargarGas();}else toast('Error',false);}
async function elimGas(id){if(!confirm('¿Eliminar?'))return;await api('/api/gastos/'+id,'DELETE');toast('Eliminado');cargarGas();}
resetGas();cargarGas();
</script>"""
    return base_html(c, session["user_name"], "gastos")

@app.route("/seguimiento")
@login_required
def seguimiento():
    c = """<div class="page">
  <div class="topbar">
    <div><div class="page-title">Seguimiento</div><div class="page-sub">Historial de movimientos por inversión</div></div>
  </div>
  <div class="kpi-grid g3">
    <div class="kpi"><div class="kpi-label">Total movimientos</div><div class="kpi-val" id="seg-cnt">0</div></div>
    <div class="kpi"><div class="kpi-label">Total aportes</div><div class="kpi-val up" id="seg-ap">$0</div></div>
    <div class="kpi"><div class="kpi-label">Total retiros</div><div class="kpi-val dn" id="seg-ret">$0</div></div>
  </div>
  <div class="card"><div class="card-header"><div class="card-title">Historial de movimientos</div></div>
    <div class="table-wrap"><table>
      <thead><tr><th>Fecha</th><th>Categoría</th><th>Tipo</th><th class="t-right">Monto</th><th class="t-right">Precio</th><th>Contexto</th><th>Acciones</th></tr></thead>
      <tbody id="tabla-seg"><tr><td colspan="7"><div class="empty">Cargando…</div></td></tr></tbody>
    </table></div>
  </div>
</div>
<script>
async function cargarSeg(){
  var rows=await api('/api/movimientos');
  var t=document.getElementById('tabla-seg');
  var cnt=0,ap=0,ret=0;
  if(!rows.length){t.innerHTML='<tr><td colspan="7"><div class="empty">Sin movimientos registrados</div></td></tr>';return;}
  rows.forEach(function(r){cnt++;if(r.tipo==='aporte'||r.tipo==='deposito_meta')ap+=r.monto||0;if(r.tipo==='retiro'||r.tipo==='retiro_meta')ret+=r.monto||0;});
  document.getElementById('seg-cnt').textContent=cnt;
  document.getElementById('seg-ap').textContent=COP(ap);
  document.getElementById('seg-ret').textContent=COP(ret);
  t.innerHTML=rows.map(function(r){
    var tipoClss={'aporte':'tag-green','retiro':'tag-red','actualizacion':'tag-blue','pago_cuota':'tag-amber','abono_capital':'tag-green','deposito_meta':'tag-green','retiro_meta':'tag-red'}[r.tipo]||'tag-gray';
    return'<tr><td style="color:var(--muted)">'+fmtFecha(r.fecha)+'</td>'
      +'<td><span class="tag tag-gray">'+(r.cat||'—')+'</span></td>'
      +'<td><span class="tag '+tipoClss+'">'+(r.tipo||'—')+'</span></td>'
      +'<td class="mono t-right">'+COP(r.monto)+'</td>'
      +'<td class="mono t-right">'+(r.precio?COP(r.precio):'—')+'</td>'
      +'<td style="color:var(--muted);font-size:11.5px">'+(r.ctx||'')+'</td>'
      +'<td><button class="btn btn-danger btn-xs" onclick="elimSeg('+r.id+')">🗑</button></td></tr>';
  }).join('');
}
async function elimSeg(id){if(!confirm('¿Eliminar movimiento?'))return;await api('/api/movimientos/'+id,'DELETE');toast('Eliminado');cargarSeg();}
cargarSeg();
</script>"""
    return base_html(c, session["user_name"], "seguimiento")

@app.route("/renta_variable")
@login_required
def renta_variable():
    c = """<div class="page">
  <div class="topbar">
    <div><div class="page-title">Renta Variable</div><div class="page-sub">Acciones · ETFs · Crypto — precios en tiempo real</div></div>
    <div style="display:flex;gap:8px;align-items:center">
      <div id="precio-status" style="display:none;font-size:11px;padding:3px 10px;border-radius:20px;background:var(--gbg);color:var(--green);font-weight:600"></div>
      <button class="btn btn-sm" onclick="actualizarPrecios()" id="btn-actualizar">↺ Actualizar precios</button>
      <button class="btn btn-primary btn-sm" onclick="resetRV();openModal('modal-rv')">+ Nueva posición</button>
    </div>
  </div>
  <div class="kpi-grid g4">
    <div class="kpi"><div class="kpi-label">Valor actual (COP)</div><div class="kpi-val" id="rv-val">$0</div><div class="kpi-sub">Al precio del día</div></div>
    <div class="kpi"><div class="kpi-label">Costo total</div><div class="kpi-val" id="rv-cost">$0</div><div class="kpi-sub">Lo invertido</div></div>
    <div class="kpi"><div class="kpi-label">G/P total</div><div class="kpi-val" id="rv-gp">$0</div><div class="kpi-sub" id="rv-gp-pct">0%</div></div>
    <div class="kpi"><div class="kpi-label">Variación hoy</div><div class="kpi-val" id="rv-dia">—</div><div class="kpi-sub" id="rv-dia-s">pendiente actualizar</div></div>
  </div>
  <div class="filter-bar">
    <select id="f-rv-broker" onchange="filtrarRV()">
      <option value="">Todos los brokers</option>
      <option>Trii</option><option>XTB</option><option>Interactive Brokers</option>
      <option>Binance</option><option>Valores Bancolombia</option><option>Tyba</option><option>Otro</option>
    </select>
    <select id="f-rv-tipo" onchange="filtrarRV()">
      <option value="">Todos los tipos</option>
      <option>Acción CO</option><option>Acción USA</option>
      <option>ETF</option><option>Crypto</option><option>Fondo</option>
    </select>
    <select id="f-rv-estado" onchange="filtrarRV()">
      <option value="">Todos</option>
      <option value="pos">Solo positivas</option>
      <option value="neg">Solo negativas</option>
    </select>
    <button class="btn btn-sm" onclick="limpiarFRV()">✕ Limpiar</button>
  </div>
  <div class="card">
    <div class="card-header">
      <div><div class="card-title">Mis posiciones</div>
        <div class="card-sub">Yahoo Finance (acciones/ETFs) · CoinGecko (crypto) · TRM en tiempo real</div>
      </div>
    </div>
    <div class="table-wrap"><table>
      <thead><tr>
        <th>Activo</th><th>Broker</th><th class="t-right">Cantidad</th>
        <th class="t-right">P. compra</th><th class="t-right">P. actual</th>
        <th class="t-right">Var. hoy</th><th class="t-right">Valor COP</th>
        <th class="t-right">G/P</th><th class="t-right">Retorno</th>
        <th>Riesgo</th><th>Acciones</th>
      </tr></thead>
      <tbody id="tabla-rv"><tr><td colspan="11"><div class="empty">Cargando…</div></td></tr></tbody>
    </table></div>
    <div class="card-footer" id="rv-footer">Carga las posiciones para ver precios de mercado</div>
  </div>
</div>
<div id="modal-rv" class="modal-overlay" onclick="if(event.target===this){resetRV();closeModal('modal-rv')}">
  <div class="modal">
    <div class="modal-header"><div class="modal-title" id="rv-title">Nueva posición</div>
      <button class="btn btn-sm" onclick="resetRV();closeModal('modal-rv')">✕</button></div>
    <div class="modal-body">
      <input type="hidden" id="rv-id"/>
      <div class="form-grid g2f">
        <div class="form-group"><label>Tipo de activo</label><select id="rv-tipo">
          <option>Acción CO</option><option>Acción USA</option>
          <option>ETF</option><option>Crypto</option><option>Fondo</option><option>Otro</option>
        </select></div>
        <div class="form-group"><label>Ticker / Símbolo</label>
          <input type="text" id="rv-ticker" placeholder="PFBCOL.CL · VOO · bitcoin"/></div>
        <div class="form-group"><label>Broker / Canal</label><select id="rv-canal">
          <option>Trii</option><option>XTB</option><option>Interactive Brokers</option>
          <option>Binance</option><option>Valores Bancolombia</option>
          <option>Tyba</option><option>Acciones y Valores</option><option>Otro</option>
        </select></div>
        <div class="form-group"><label>Riesgo</label><select id="rv-riesgo">
          <option value="bajo">Bajo</option><option value="moderado" selected>Moderado</option>
          <option value="alto">Alto</option><option value="muy alto">Muy alto</option>
        </select></div>
        <div class="form-group"><label>Cantidad / Unidades</label>
          <input type="number" id="rv-cantidad" step="0.0001" placeholder="100 acciones · 0.025 BTC"/></div>
        <div class="form-group"><label>Precio de compra (COP)</label>
          <input type="number" id="rv-pcomp" step="1" placeholder="38000 COP"/></div>
        <div class="form-group"><label>Precio actual (COP)</label>
          <input type="number" id="rv-pact" step="1" placeholder="Se actualiza automático"/></div>
        <div class="form-group"><label>Comisión (%)</label>
          <input type="number" id="rv-com" step="0.01" value="0" placeholder="0.3"/></div>
        <div class="form-group"><label>Fecha de compra</label><input type="date" id="rv-fecha"/></div>
        <div class="form-group full"><label>Tesis de inversión</label>
          <input type="text" id="rv-tesis" placeholder="¿Por qué compraste esta posición?"/></div>
      </div>
      <div class="alert alert-info" style="margin-top:12px">
        <div>Tickers soportados: <b>PFBCOL.CL</b>, <b>ECOPETROL.CL</b>, <b>GEB.CL</b> (CO) · <b>VOO</b>, <b>QQQ</b>, <b>AAPL</b> (USA/ETF) · <b>bitcoin</b>, <b>ethereum</b> (crypto — nombre en inglés)</div>
      </div>
    </div>
    <div class="modal-footer">
      <button class="btn" onclick="resetRV();closeModal('modal-rv')">Cancelar</button>
      <button class="btn btn-primary" onclick="guardarRV()">Guardar</button>
    </div>
  </div>
</div>
<script>
var TRM_RV=4200,TODAS_POS=[];
var RIESGO_CLS={bajo:'tag-green',moderado:'tag-blue',alto:'tag-amber','muy alto':'tag-red'};

async function getPrecioMercado(ticker){
  try{var r=await fetch('/api/precio_mercado?ticker='+encodeURIComponent(ticker));return await r.json();}
  catch(e){return null;}
}

function resetRV(){
  ['rv-tipo','rv-canal','rv-riesgo'].forEach(function(id){var el=document.getElementById(id);if(el)el.selectedIndex=0;});
  ['rv-ticker','rv-cantidad','rv-pcomp','rv-pact','rv-com','rv-fecha','rv-tesis','rv-id'].forEach(function(id){var el=document.getElementById(id);if(el)el.value='';});
  document.getElementById('rv-com').value='0';
  document.getElementById('rv-fecha').value=hoy();
  document.getElementById('rv-title').textContent='Nueva posición';
}

function limpiarFRV(){
  ['f-rv-broker','f-rv-tipo','f-rv-estado'].forEach(function(id){document.getElementById(id).selectedIndex=0;});
  renderRV(TODAS_POS);
}

function filtrarRV(){
  var br=document.getElementById('f-rv-broker').value;
  var ti=document.getElementById('f-rv-tipo').value;
  var es=document.getElementById('f-rv-estado').value;
  renderRV(TODAS_POS.filter(function(r){
    if(br&&r.canal!==br)return false;
    if(ti&&r.tipo!==ti)return false;
    if(es==='pos'&&(r.ganancia||0)<=0)return false;
    if(es==='neg'&&(r.ganancia||0)>=0)return false;
    return true;
  }));
}

function renderRV(rows){
  var t=document.getElementById('tabla-rv');
  if(!rows.length){t.innerHTML='<tr><td colspan="11"><div class="empty">Sin posiciones con esos filtros</div></td></tr>';return;}
  t.innerHTML=rows.map(function(r){
    var gp=r.ganancia||0;
    var varH=r.var_dia_pct!=null
      ?'<span class="'+(r.var_dia_pct>=0?'up':'dn')'">'+(r.var_dia_pct>=0?'+':'')+r.var_dia_pct.toFixed(2)+'%</span>'
      :'<span style="color:var(--hint)">—</span>';
    return '<tr>'
      +'<td><div style="font-weight:600;font-size:13px">'+r.ticker+'</div>'
        +'<div style="font-size:10px;color:var(--muted)">'+( r.nombre_mercado||r.tipo)+'</div>'
        +'<span class="tag tag-gray" style="font-size:9px">'+r.tipo+'</span></td>'
      +'<td><span style="font-size:10px;font-weight:600;padding:2px 6px;border-radius:3px;background:var(--bg);border:.5px solid var(--border-s)">'+r.canal+'</span></td>'
      +'<td class="mono t-right">'+r.cantidad+'</td>'
      +'<td class="mono t-right">'+COP(r.precio_comp)+'</td>'
      +'<td class="mono t-right" style="font-weight:600'+(r.precio_act_ok?';color:var(--green)':'')'">'+ COP(r.precio_act)+'</td>'
      +'<td class="t-right">'+varH+'</td>'
      +'<td class="mono t-right" style="font-weight:600">'+COP(r.valor_actual||0)+'</td>'
      +'<td class="mono t-right"><span class="'+(gp>=0?'up':'dn')+'" style="font-weight:600">'+COP(gp)+'</span></td>'
      +'<td class="mono t-right"><span class="'+(gp>=0?'up':'dn')'">'+ Pct(r.retorno_pct||0)+'</span></td>'
      +'<td><span class="tag '+(RIESGO_CLS[r.riesgo]||'tag-gray')+'">'+r.riesgo+'</span></td>'
      +'<td><div style="display:flex;gap:4px">'
        +'<button class="btn btn-edit btn-xs" onclick="editarRV('+r.id+')">✏️</button>'
        +'<button class="btn btn-danger btn-xs" onclick="elimRV('+r.id+')">🗑</button>'
      +'</div></td></tr>';
  }).join('');
}

function calcKPIs(rows){
  var totV=0,totC=0,totVar=0,nVar=0;
  rows.forEach(function(r){
    totV+=r.valor_actual||0;totC+=r.costo_total||0;
    if(r.var_dia_cop!=null){totVar+=r.var_dia_cop;nVar++;}
  });
  var gp=totV-totC;
  document.getElementById('rv-val').textContent=COP(totV);
  document.getElementById('rv-cost').textContent=COP(totC);
  document.getElementById('rv-gp').textContent=COP(gp);
  document.getElementById('rv-gp').className='kpi-val '+(gp>=0?'up':'dn');
  document.getElementById('rv-gp-pct').textContent=Pct(totC>0?gp/totC*100:0);
  if(nVar>0){
    document.getElementById('rv-dia').textContent=(totVar>=0?'+':'')+COP(totVar);
    document.getElementById('rv-dia').className='kpi-val '+(totVar>=0?'up':'dn');
    document.getElementById('rv-dia-s').textContent=nVar+' posición(es) actualizadas hoy';
  }
}

async function cargarRV(){
  try{var m=await api('/api/market');TRM_RV=m.usd_cop||4200;}catch(e){}
  var rows=await api('/api/renta_variable');
  if(!rows.length){
    document.getElementById('tabla-rv').innerHTML='<tr><td colspan="11"><div class="empty"><div class="empty-icon">📊</div>Sin posiciones registradas.</div></td></tr>';
    return;
  }
  rows.forEach(function(r){r.var_dia_pct=null;r.var_dia_cop=null;r.precio_act_ok=false;r.nombre_mercado='';});
  TODAS_POS=rows;
  calcKPIs(rows);
  renderRV(rows);
}

async function actualizarPrecios(){
  var btn=document.getElementById('btn-actualizar');
  var status=document.getElementById('precio-status');
  btn.textContent='Actualizando…';btn.disabled=true;
  status.style.display='none';
  var ok=0,err=[];
  for(var i=0;i<TODAS_POS.length;i++){
    var r=TODAS_POS[i];
    if(!r.ticker)continue;
    var ticker=r.tipo==='Crypto'?r.ticker.toLowerCase():r.ticker;
    var d=await getPrecioMercado(ticker);
    if(d&&d.precio&&!d.error){
      var pCOP=d.moneda==='USD'?Math.round(d.precio*TRM_RV):Math.round(d.precio);
      var pAnterior=r.precio_act||r.precio_comp;
      r.precio_act=pCOP;
      r.precio_act_ok=true;
      r.nombre_mercado=d.nombre||ticker;
      r.var_dia_pct=d.cambio_pct!=null?parseFloat(d.cambio_pct.toFixed(2)):null;
      r.var_dia_cop=r.var_dia_pct!=null?Math.round(r.cantidad*pAnterior*(r.var_dia_pct/100)):null;
      r.costo_total=Math.round(r.cantidad*r.precio_comp);
      r.valor_actual=Math.round(r.cantidad*pCOP);
      r.ganancia=r.valor_actual-r.costo_total;
      r.retorno_pct=r.costo_total>0?(r.ganancia/r.costo_total*100):0;
      await api('/api/renta_variable/'+r.id,'PUT',{
        tipo:r.tipo,ticker:r.ticker,canal:r.canal,cantidad:r.cantidad,
        precio_comp:r.precio_comp,precio_act:pCOP,
        com_pct:r.com_pct||0,fecha:r.fecha,riesgo:r.riesgo,tesis:r.tesis
      });
      ok++;
    }else{err.push(r.ticker);}
  }
  calcKPIs(TODAS_POS);
  filtrarRV();
  btn.textContent='↺ Actualizar precios';btn.disabled=false;
  status.style.display='block';
  if(err.length){
    status.textContent=ok+' actualizados · No encontrados: '+err.join(', ');
    status.style.background='var(--abg)';status.style.color='var(--amber)';
  }else{
    status.textContent=ok+' precios actualizados ✓';
    status.style.background='var(--gbg)';status.style.color='var(--green)';
  }
  document.getElementById('rv-footer').textContent='Actualizado '+new Date().toLocaleTimeString('es-CO')+' · Yahoo Finance · CoinGecko · TRM $'+TRM_RV.toLocaleString('es-CO');
}

async function editarRV(id){
  var r=await api('/api/renta_variable/'+id);
  document.getElementById('rv-id').value=id;
  document.getElementById('rv-tipo').value=r.tipo||'Acción CO';
  document.getElementById('rv-ticker').value=r.ticker||'';;
  document.getElementById('rv-canal').value=r.canal||'Trii';
  document.getElementById('rv-riesgo').value=r.riesgo||'moderado';
  document.getElementById('rv-cantidad').value=r.cantidad||'';;
  document.getElementById('rv-pcomp').value=r.precio_comp||'';;
  document.getElementById('rv-pact').value=r.precio_act||'';;
  document.getElementById('rv-com').value=r.com_pct||0;
  document.getElementById('rv-fecha').value=r.fecha||'';;
  document.getElementById('rv-tesis').value=r.tesis||'';;
  document.getElementById('rv-title').textContent='Editar posición';
  openModal('modal-rv');
}

async function guardarRV(){
  var id=document.getElementById('rv-id').value;
  var tipo=document.getElementById('rv-tipo').value;
  var ticker=document.getElementById('rv-ticker').value.trim();
  if(tipo==='Crypto')ticker=ticker.toLowerCase();
  else ticker=ticker.toUpperCase();
  var d={tipo:tipo,ticker:ticker,canal:document.getElementById('rv-canal').value,
    riesgo:document.getElementById('rv-riesgo').value,
    cantidad:parseFloat(document.getElementById('rv-cantidad').value)||0,
    precio_comp:parseFloat(document.getElementById('rv-pcomp').value)||0,
    precio_act:parseFloat(document.getElementById('rv-pact').value)||parseFloat(document.getElementById('rv-pcomp').value)||0,
    com_pct:parseFloat(document.getElementById('rv-com').value)||0,
    fecha:document.getElementById('rv-fecha').value,
    tesis:document.getElementById('rv-tesis').value};
  if(!d.cantidad){toast('Ingresa la cantidad',false);return;}
  if(!d.precio_comp){toast('Ingresa el precio de compra',false);return;}
  var r=id?await api('/api/renta_variable/'+id,'PUT',d):await api('/api/renta_variable','POST',d);
  if(r.ok||r.id){toast(id?'Posición actualizada ✅':'Posición guardada ✅');resetRV();closeModal('modal-rv');cargarRV();}
  else toast('Error al guardar',false);
}

async function elimRV(id){
  if(!confirm('¿Eliminar esta posición?'))return;
  await api('/api/renta_variable/'+id,'DELETE');
  toast('Eliminada');cargarRV();
}

resetRV();cargarRV();
</script>"""
    return base_html(c, session["user_name"], "renta_variable")

@app.route("/inmobiliario")
@login_required
def inmobiliario():
    c = """<div class="page">
  <div class="topbar">
    <div><div class="page-title">Inmobiliario</div><div class="page-sub">Propiedades · FICs inmobiliarios · Crowdfunding</div></div>
    <div><button class="btn btn-primary btn-sm" onclick="resetInmo();openModal('modal-inmo')">+ Nuevo activo</button></div>
  </div>
  <div class="kpi-grid g4">
    <div class="kpi"><div class="kpi-label">Valor total</div><div class="kpi-val" id="inmo-val">$0</div></div>
    <div class="kpi"><div class="kpi-label">Canon mensual</div><div class="kpi-val up" id="inmo-can">$0</div></div>
    <div class="kpi"><div class="kpi-label">Valorización</div><div class="kpi-val" id="inmo-valz">$0</div></div>
    <div class="kpi"><div class="kpi-label">Renta anual</div><div class="kpi-val up" id="inmo-ran">$0</div></div>
  </div>
  <div class="card"><div class="card-header"><div class="card-title">Mis activos inmobiliarios</div></div>
    <div class="table-wrap"><table>
      <thead><tr><th>Tipo</th><th>Nombre</th><th>Canal</th><th class="t-right">Compra</th><th class="t-right">Valor actual</th><th class="t-right">Valorización</th><th class="t-right">Canon/mes</th><th class="t-right">Renta anual</th><th>Período</th><th>Tasa E.A.</th><th>Acciones</th></tr></thead>
      <tbody id="tabla-inmo"><tr><td colspan="11"><div class="empty">Cargando…</div></td></tr></tbody>
    </table></div>
  </div>
</div>
<div id="modal-inmo" class="modal-overlay" onclick="if(event.target===this){resetInmo();closeModal('modal-inmo')}">
  <div class="modal">
    <div class="modal-header"><div class="modal-title" id="inmo-title">Nuevo activo inmobiliario</div>
      <button class="btn btn-sm" onclick="resetInmo();closeModal('modal-inmo')">✕</button></div>
    <div class="modal-body">
      <input type="hidden" id="inmo-id"/>
      <div class="form-grid g2f">
        <div class="form-group"><label>Tipo</label><select id="inmo-tipo">
          <option>Apartamento arrendado</option><option>Casa arrendada</option>
          <option>Local comercial</option><option>Bodega</option>
          <option>FIC inmobiliario</option><option>Crowdfunding inmobiliario</option>
          <option>Lote / Terreno</option><option>Otro</option>
        </select></div>
        <div class="form-group"><label>Canal / Plataforma</label><select id="inmo-canal">
          <option>Directo (propietario)</option><option>La Haus</option>
          <option>Habi</option><option>Tributi</option>
          <option>FIC Valores Bancolombia</option><option>Otro</option>
        </select></div>
        <div class="form-group full"><label>Nombre / Descripción</label>
          <input type="text" id="inmo-nombre" placeholder="Apto Laureles, FIC inmobiliario…"/></div>
        <div class="form-group"><label>Valor de compra ($)</label>
          <input type="number" id="inmo-compra" placeholder="200000000"/></div>
        <div class="form-group"><label>Valor actual ($)</label>
          <input type="number" id="inmo-actual" placeholder="230000000"/></div>
        <div class="form-group"><label>Canon / rendimiento mensual ($)</label>
          <input type="number" id="inmo-canon" placeholder="1500000"/></div>
        <div class="form-group"><label>Tasa E.A. (%)</label>
          <input type="number" id="inmo-tasa" step="0.1" placeholder="8.5"/></div>
        <div class="form-group"><label>Comisión E.A. (%)</label>
          <input type="number" id="inmo-com" step="0.1" value="0" placeholder="0"/></div>
        <div class="form-group"><label>Período pago</label><select id="inmo-periodo">
          <option value="mensual">Mensual</option><option value="trimestral">Trimestral</option>
          <option value="semestral">Semestral</option><option value="anual">Anual</option>
        </select></div>
        <div class="form-group"><label>Fecha de adquisición</label><input type="date" id="inmo-fecha"/></div>
        <div class="form-group full"><label>Notas</label>
          <input type="text" id="inmo-notas" placeholder="Dirección, estado del activo…"/></div>
      </div>
    </div>
    <div class="modal-footer">
      <button class="btn" onclick="resetInmo();closeModal('modal-inmo')">Cancelar</button>
      <button class="btn btn-primary" onclick="guardarInmo()">Guardar</button>
    </div>
  </div>
</div>
<script>
function resetInmo(){['inmo-tipo','inmo-canal','inmo-periodo'].forEach(function(id){var el=document.getElementById(id);if(el)el.selectedIndex=0;});['inmo-nombre','inmo-compra','inmo-actual','inmo-canon','inmo-tasa','inmo-com','inmo-fecha','inmo-notas','inmo-id'].forEach(function(id){var el=document.getElementById(id);if(el)el.value='';});document.getElementById('inmo-com').value='0';document.getElementById('inmo-fecha').value=hoy();document.getElementById('inmo-title').textContent='Nuevo activo inmobiliario';}
async function cargarInmo(){
  var rows=await api('/api/inmobiliario');
  var t=document.getElementById('tabla-inmo');
  if(!rows.length){t.innerHTML='<tr><td colspan="11"><div class="empty">Sin activos registrados</div></td></tr>';['inmo-val','inmo-can','inmo-valz','inmo-ran'].forEach(function(id){document.getElementById(id).textContent='$0';});return;}
  var totV=0,totCan=0,totVz=0,totRan=0;
  rows.forEach(function(r){totV+=r.actual||0;totCan+=r.canon||0;totVz+=(r.valorizacion||0);totRan+=r.renta_anual||0;});
  document.getElementById('inmo-val').textContent=COP(totV);
  document.getElementById('inmo-can').textContent=COP(totCan);
  document.getElementById('inmo-valz').textContent=COP(totVz);
  document.getElementById('inmo-valz').className='kpi-val '+(totVz>=0?'up':'dn');
  document.getElementById('inmo-ran').textContent=COP(totRan);
  t.innerHTML=rows.map(function(r){
    var vz=r.valorizacion||0;
    return'<tr><td><span class="tag tag-gray">'+r.tipo+'</span></td>'
      +'<td><b>'+(r.nombre||r.tipo)+'</b></td>'
      +'<td style="color:var(--muted)">'+r.canal+'</td>'
      +'<td class="mono t-right">'+COP(r.compra)+'</td>'
      +'<td class="mono t-right">'+COP(r.actual)+'</td>'
      +'<td class="mono t-right '+(vz>=0?'up':'dn')+'">'+COP(vz)+'</td>'
      +'<td class="mono t-right up">'+COP(r.canon)+'</td>'
      +'<td class="mono t-right up">'+COP(r.renta_anual||r.canon*12)+'</td>'
      +'<td><span class="period-badge period-'+(r.periodo||'mensual')+'">'+r.periodo+'</span></td>'
      +'<td class="mono">'+Pct(r.tasa_ea||0)+'</td>'
      +'<td><div style="display:flex;gap:4px"><button class="btn btn-edit btn-xs" onclick="editarInmo('+r.id+')">✏️</button><button class="btn btn-danger btn-xs" onclick="elimInmo('+r.id+')">🗑</button></div></td></tr>';
  }).join('');
}
async function editarInmo(id){var r=await api('/api/inmobiliario/'+id);document.getElementById('inmo-id').value=id;document.getElementById('inmo-tipo').value=r.tipo||'';document.getElementById('inmo-canal').value=r.canal||'';document.getElementById('inmo-nombre').value=r.nombre||'';document.getElementById('inmo-compra').value=r.compra||'';document.getElementById('inmo-actual').value=r.actual||'';document.getElementById('inmo-canon').value=r.canon||'';document.getElementById('inmo-tasa').value=r.tasa_ea||'';document.getElementById('inmo-com').value=r.com_ea||0;document.getElementById('inmo-periodo').value=r.periodo||'mensual';document.getElementById('inmo-fecha').value=r.fecha||'';document.getElementById('inmo-notas').value=r.notas||'';document.getElementById('inmo-title').textContent='Editar activo';openModal('modal-inmo');}
async function guardarInmo(){var id=document.getElementById('inmo-id').value;var d={tipo:document.getElementById('inmo-tipo').value,canal:document.getElementById('inmo-canal').value,nombre:document.getElementById('inmo-nombre').value,compra:parseFloat(document.getElementById('inmo-compra').value)||0,actual:parseFloat(document.getElementById('inmo-actual').value)||0,canon:parseFloat(document.getElementById('inmo-canon').value)||0,tasa_ea:parseFloat(document.getElementById('inmo-tasa').value)||0,com_ea:parseFloat(document.getElementById('inmo-com').value)||0,periodo:document.getElementById('inmo-periodo').value,fecha:document.getElementById('inmo-fecha').value,notas:document.getElementById('inmo-notas').value};if(!d.compra){toast('Ingresa el valor de compra',false);return;}var r=id?await api('/api/inmobiliario/'+id,'PUT',d):await api('/api/inmobiliario','POST',d);if(r.ok||r.id){toast('Guardado ✅');resetInmo();closeModal('modal-inmo');cargarInmo();}else toast('Error',false);}
async function elimInmo(id){if(!confirm('¿Eliminar activo?'))return;await api('/api/inmobiliario/'+id,'DELETE');toast('Eliminado');cargarInmo();}
resetInmo();cargarInmo();
</script>"""
    return base_html(c, session["user_name"], "inmobiliario")

@app.route("/dolares")
@login_required
def dolares():
    c = """<div class="page">
  <div class="topbar">
    <div><div class="page-title">Dólares / USD</div><div class="page-sub">Posición en dólares · Ganancia cambiaria</div></div>
    <div><button class="btn btn-primary btn-sm" onclick="resetUSD();openModal('modal-usd')">+ Nueva posición</button></div>
  </div>
  <div class="kpi-grid g4">
    <div class="kpi"><div class="kpi-label">Total USD</div><div class="kpi-val" id="usd-tot">$0 USD</div></div>
    <div class="kpi"><div class="kpi-label">Valor en COP (actual)</div><div class="kpi-val" id="usd-cop-act">$0</div></div>
    <div class="kpi"><div class="kpi-label">Costo en COP</div><div class="kpi-val" id="usd-cop-cos">$0</div></div>
    <div class="kpi"><div class="kpi-label">G/P cambiaria</div><div class="kpi-val" id="usd-gp">$0</div></div>
  </div>
  <div class="kpi-grid g2">
    <div class="kpi"><div class="kpi-label">TRM actual</div><div class="kpi-val sm" id="usd-trm">$4,200</div></div>
    <div class="kpi"><div class="kpi-label">TRM promedio de compra</div><div class="kpi-val sm" id="usd-trm-avg">$0</div></div>
  </div>
  <div class="card"><div class="card-header"><div class="card-title">Mis posiciones en USD</div></div>
    <div class="table-wrap"><table>
      <thead><tr><th>Tipo</th><th>Nombre</th><th>Canal</th><th class="t-right">Cant. USD</th><th class="t-right">TRM compra</th><th class="t-right">COP compra</th><th class="t-right">COP actual</th><th class="t-right">G/P camb.</th><th class="t-right">Rend. USD</th><th>Fecha</th><th>Acciones</th></tr></thead>
      <tbody id="tabla-usd"><tr><td colspan="11"><div class="empty">Cargando…</div></td></tr></tbody>
    </table></div>
  </div>
</div>
<div id="modal-usd" class="modal-overlay" onclick="if(event.target===this){resetUSD();closeModal('modal-usd')}">
  <div class="modal" style="max-width:480px">
    <div class="modal-header"><div class="modal-title" id="usd-modal-title">Nueva posición en USD</div>
      <button class="btn btn-sm" onclick="resetUSD();closeModal('modal-usd')">✕</button></div>
    <div class="modal-body">
      <input type="hidden" id="usd-id"/>
      <div class="form-grid g2f">
        <div class="form-group"><label>Tipo</label><select id="usd-tipo">
          <option>Cuenta USD</option><option>ETF en USD</option>
          <option>Efectivo USD</option><option>Remesa recibida</option><option>Otro</option>
        </select></div>
        <div class="form-group"><label>Canal</label><select id="usd-canal">
          <option>Nu Colombia USD</option><option>Remitly / Wise</option>
          <option>Broker internacional</option><option>Efectivo</option><option>Otro</option>
        </select></div>
        <div class="form-group full"><label>Nombre / Descripción</label>
          <input type="text" id="usd-nombre" placeholder="Cuenta Nu USD, ETF VOO…"/></div>
        <div class="form-group"><label>Cantidad USD</label>
          <input type="number" id="usd-cant" step="0.01" placeholder="500"/></div>
        <div class="form-group"><label>TRM de compra (COP/USD)</label>
          <input type="number" id="usd-trm-c" placeholder="4100"/></div>
        <div class="form-group"><label>Rendimiento USD (%)</label>
          <input type="number" id="usd-rend" step="0.01" placeholder="0" value="0"/></div>
        <div class="form-group"><label>Fecha</label><input type="date" id="usd-fecha"/></div>
        <div class="form-group full"><label>Notas</label>
          <input type="text" id="usd-notas" placeholder=""/></div>
      </div>
    </div>
    <div class="modal-footer">
      <button class="btn" onclick="resetUSD();closeModal('modal-usd')">Cancelar</button>
      <button class="btn btn-primary" onclick="guardarUSD()">Guardar</button>
    </div>
  </div>
</div>
<script>
var TRM_ACT=4200;
function resetUSD(){['usd-tipo','usd-canal'].forEach(function(id){var el=document.getElementById(id);if(el)el.selectedIndex=0;});['usd-nombre','usd-cant','usd-trm-c','usd-rend','usd-fecha','usd-notas','usd-id'].forEach(function(id){var el=document.getElementById(id);if(el)el.value='';});document.getElementById('usd-rend').value='0';document.getElementById('usd-trm-c').value=TRM_ACT;document.getElementById('usd-fecha').value=hoy();document.getElementById('usd-modal-title').textContent='Nueva posición en USD';}
async function cargarUSD(){
  try{var m=await api('/api/market');TRM_ACT=m.usd_cop||4200;document.getElementById('usd-trm').textContent='$'+TRM_ACT.toLocaleString('es-CO');}catch(e){}
  var rows=await api('/api/dolares?trm='+TRM_ACT);
  var t=document.getElementById('tabla-usd');
  if(!rows.length){t.innerHTML='<tr><td colspan="11"><div class="empty">Sin posiciones en USD</div></td></tr>';return;}
  var totUSD=0,totCopAct=0,totCopCos=0,totGP=0,wTrm=0;
  rows.forEach(function(r){totUSD+=r.cant_usd||0;totCopAct+=r.cop_actual||0;totCopCos+=r.cop_compra||0;totGP+=r.gp_cambiaria||0;wTrm+=r.trm_compra*(r.cant_usd||0);});
  document.getElementById('usd-tot').textContent=totUSD.toFixed(2)+' USD';
  document.getElementById('usd-cop-act').textContent=COP(totCopAct);
  document.getElementById('usd-cop-cos').textContent=COP(totCopCos);
  document.getElementById('usd-gp').textContent=COP(totGP);
  document.getElementById('usd-gp').className='kpi-val '+(totGP>=0?'up':'dn');
  document.getElementById('usd-trm-avg').textContent='$'+(totUSD>0?Math.round(wTrm/totUSD).toLocaleString('es-CO'):'0');
  t.innerHTML=rows.map(function(r){
    var gp=r.gp_cambiaria||0;
    return'<tr><td><span class="tag tag-gray">'+r.tipo+'</span></td>'
      +'<td><b>'+(r.nombre||r.tipo)+'</b></td>'
      +'<td style="color:var(--muted)">'+r.canal+'</td>'
      +'<td class="mono t-right">'+r.cant_usd.toFixed(2)+' USD</td>'
      +'<td class="mono t-right">$'+Math.round(r.trm_compra).toLocaleString('es-CO')+'</td>'
      +'<td class="mono t-right">'+COP(r.cop_compra)+'</td>'
      +'<td class="mono t-right">'+COP(r.cop_actual)+'</td>'
      +'<td class="mono t-right '+(gp>=0?'up':'dn')+'">'+COP(gp)+'</td>'
      +'<td class="mono t-right">'+(r.rend_usd?Pct(r.rend_usd):'—')+'</td>'
      +'<td style="color:var(--muted)">'+fmtFecha(r.fecha)+'</td>'
      +'<td><div style="display:flex;gap:4px"><button class="btn btn-edit btn-xs" onclick="editarUSD('+r.id+')">✏️</button><button class="btn btn-danger btn-xs" onclick="elimUSD('+r.id+')">🗑</button></div></td></tr>';
  }).join('');
}
async function editarUSD(id){var r=await api('/api/dolares/'+id);document.getElementById('usd-id').value=id;document.getElementById('usd-tipo').value=r.tipo||'';document.getElementById('usd-canal').value=r.canal||'';document.getElementById('usd-nombre').value=r.nombre||'';document.getElementById('usd-cant').value=r.cant_usd||'';document.getElementById('usd-trm-c').value=r.trm_compra||'';document.getElementById('usd-rend').value=r.rend_usd||0;document.getElementById('usd-fecha').value=r.fecha||'';document.getElementById('usd-notas').value=r.notas||'';document.getElementById('usd-modal-title').textContent='Editar posición USD';openModal('modal-usd');}
async function guardarUSD(){var id=document.getElementById('usd-id').value;var d={tipo:document.getElementById('usd-tipo').value,canal:document.getElementById('usd-canal').value,nombre:document.getElementById('usd-nombre').value,cant_usd:parseFloat(document.getElementById('usd-cant').value)||0,trm_compra:parseFloat(document.getElementById('usd-trm-c').value)||TRM_ACT,rend_usd:parseFloat(document.getElementById('usd-rend').value)||0,fecha:document.getElementById('usd-fecha').value,notas:document.getElementById('usd-notas').value};if(!d.cant_usd){toast('Ingresa la cantidad en USD',false);return;}var r=id?await api('/api/dolares/'+id,'PUT',d):await api('/api/dolares','POST',d);if(r.ok||r.id){toast('Guardado ✅');resetUSD();closeModal('modal-usd');cargarUSD();}else toast('Error',false);}
async function elimUSD(id){if(!confirm('¿Eliminar posición?'))return;await api('/api/dolares/'+id,'DELETE');toast('Eliminada');cargarUSD();}
resetUSD();cargarUSD();
</script>"""
    return base_html(c, session["user_name"], "dolares")


# ══════════════════════════════════════════════════════════════
#  RENTA FIJA (con rendimientos y CRUD)
# ══════════════════════════════════════════════════════════════

@app.route("/renta_fija")
@login_required
def renta_fija():
    c = """<div class="page">
  <div class="topbar">
    <div><div class="page-title">Renta Fija</div><div class="page-sub">CDT · TES · Cuentas remuneradas · Fiducias</div></div>
    <div><button class="btn btn-primary btn-sm" onclick="resetRF();openModal('modal-rf')">Nueva inversión</button></div>
  </div>
  <div class="alert alert-info"><div><b>Períodos:</b> <b style="color:#166534">Diario</b> → Nu, Lulo, Nequi cajita · <b>Mensual</b> → CDT mensual, SiRenta · <b>Vencimiento</b> → CDT acumulado</div></div>
  <div class="kpi-grid g3">
    <div class="kpi"><div class="kpi-label">Total RF activo</div><div class="kpi-val" id="rf-tot">$0</div></div>
    <div class="kpi"><div class="kpi-label">Rend. diario total</div><div class="kpi-val up" id="rf-dia">$0</div></div>
    <div class="kpi"><div class="kpi-label">Rend. anual proyectado</div><div class="kpi-val up" id="rf-año">$0</div></div>
  </div>
  <div class="card"><div class="card-header"><div class="card-title">Mis inversiones de renta fija</div></div>
    <div class="table-wrap"><table>
      <thead><tr><th>Producto</th><th>Canal</th><th>Monto</th><th>Tasa E.A.</th><th>Tasa neta</th><th>Período</th><th style="background:#f0fdf4">Rend. diario</th><th>Rend. período</th><th>Rend. anual</th><th>Acumulado</th><th>Vence</th><th>Estado</th><th>Acciones</th></tr></thead>
      <tbody id="tabla-rf"><tr><td colspan="13"><div class="empty"><div class="empty-icon">🏦</div>Sin inversiones</div></td></tr></tbody>
    </table></div>
  </div>
</div>

<div id="modal-rf" class="modal-overlay" onclick="if(event.target===this){resetRF();closeModal('modal-rf')}">
  <div class="modal">
    <div class="modal-header"><div class="modal-title" id="rf-title">Nueva inversión de renta fija</div>
      <button class="btn btn-sm" onclick="resetRF();closeModal('modal-rf')">✕</button></div>
    <div class="modal-body">
      <input type="hidden" id="rf-id"/>
      <div class="form-grid g2f">
        <div class="form-group"><label>Tipo de producto</label><select id="rf-tipo">
          <option>Cuenta remunerada Nu Colombia</option><option>Cuenta remunerada Lulo Bank</option>
          <option>Cajita Nequi / Daviplata</option><option>Cajita Ualá</option>
          <option>CDT Bancolombia</option><option>CDT Davivienda</option><option>CDT Nu Colombia</option>
          <option>CDT BBVA</option><option>TES Renta Fija</option><option>TES UVR</option>
          <option>Bono Corporativo</option><option>FIC Mercado Monetario</option>
          <option>Fiducia mercado monetario</option><option>Fondo SiRenta</option><option>Otro</option>
        </select></div>
        <div class="form-group"><label>Canal</label><select id="rf-canal">
          <option>Nu Colombia</option><option>Lulo Bank</option><option>Nequi</option>
          <option>Daviplata</option><option>Ualá</option><option>App Bancolombia</option>
          <option>App Davivienda</option><option>Tyba</option><option>MejorCDT</option>
          <option>Valores Bancolombia</option><option>Trii</option><option>Acciones y Valores</option><option>Otro</option>
        </select></div>
        <div class="form-group"><label>Monto invertido ($)</label><input type="number" id="rf-monto" placeholder="1100000" oninput="previewRF()"/></div>
        <div class="form-group"><label>Tasa E.A. bruta (%)</label><input type="number" id="rf-tasa" step="0.01" placeholder="12.4" oninput="previewRF()"/></div>
        <div class="form-group"><label>Comisión E.A. (%)</label><input type="number" id="rf-com" step="0.01" value="0" oninput="previewRF()"/></div>
        <div class="form-group"><label>Período de pago</label><select id="rf-periodo" onchange="previewRF()">
          <option value="diario">Diario (Nu, Lulo, Nequi cajita)</option>
          <option value="mensual" selected>Mensual (CDT mensual, SiRenta)</option>
          <option value="trimestral">Trimestral</option>
          <option value="semestral">Semestral (TES)</option>
          <option value="anual">Anual</option>
          <option value="vencimiento">Al vencimiento (CDT acumulado)</option>
        </select></div>
        <div class="form-group"><label>Fecha inicio</label><input type="date" id="rf-ini"/></div>
        <div class="form-group"><label>Fecha vencimiento</label><input type="date" id="rf-vence"/></div>
        <div class="form-group"><label>Estado</label><select id="rf-estado">
          <option value="activo">Activo</option><option value="vencido">Vencido / Cobrado</option><option value="pendiente">Pendiente</option>
        </select></div>
        <div class="form-group full"><label>Notas</label><input type="text" id="rf-notas" placeholder="Renovación automática, condiciones…"/></div>
      </div>
      <div id="rf-prev" style="display:none;margin-top:14px;background:var(--bg);border-radius:var(--r);padding:14px">
        <div style="font-size:10px;font-weight:700;color:var(--muted);text-transform:uppercase;letter-spacing:.6px;margin-bottom:10px">Vista previa de rendimientos</div>
        <div style="display:grid;grid-template-columns:repeat(4,1fr);gap:10px;text-align:center">
          <div><div style="font-size:10px;color:var(--muted)">Diario</div><div class="mono up" id="pr-d" style="font-size:14px;font-weight:600"></div></div>
          <div><div style="font-size:10px;color:var(--muted)">Mensual</div><div class="mono up" id="pr-m" style="font-size:14px;font-weight:600"></div></div>
          <div><div style="font-size:10px;color:var(--muted)">Anual</div><div class="mono up" id="pr-a" style="font-size:14px;font-weight:600"></div></div>
          <div><div style="font-size:10px;color:var(--muted)">Tasa neta</div><div class="mono" id="pr-n" style="font-size:14px;font-weight:600;color:var(--blue)"></div></div>
        </div>
      </div>
    </div>
    <div class="modal-footer">
      <button class="btn" onclick="resetRF();closeModal('modal-rf')">Cancelar</button>
      <button class="btn btn-primary" onclick="guardarRF()">Guardar</button>
    </div>
  </div>
</div>

<script>
var PCLS={diario:'period-diario',mensual:'period-mensual',trimestral:'period-trimestral',semestral:'period-semestral',anual:'period-anual',vencimiento:'period-vencimiento'};
function previewRF(){
  var m=parseFloat(document.getElementById('rf-monto').value)||0;
  var t=parseFloat(document.getElementById('rf-tasa').value)||0;
  var co=parseFloat(document.getElementById('rf-com').value)||0;
  var n=Math.max(0,t-co);var ea=n/100;
  if(!m||!ea){document.getElementById('rf-prev').style.display='none';return;}
  document.getElementById('pr-d').textContent='+'+COP(m*(Math.pow(1+ea,1/365)-1));
  document.getElementById('pr-m').textContent='+'+COP(m*(Math.pow(1+ea,1/12)-1));
  document.getElementById('pr-a').textContent='+'+COP(m*ea);
  document.getElementById('pr-n').textContent=Pct(n);
  document.getElementById('rf-prev').style.display='block';
}
function resetRF(){
  ['rf-tipo','rf-canal','rf-periodo','rf-estado'].forEach(function(id){var el=document.getElementById(id);if(el)el.selectedIndex=0;});
  ['rf-monto','rf-tasa','rf-com','rf-ini','rf-vence','rf-notas','rf-id'].forEach(function(id){var el=document.getElementById(id);if(el)el.value='';});
  document.getElementById('rf-com').value='0';
  document.getElementById('rf-ini').value=hoy();
  document.getElementById('rf-title').textContent='Nueva inversión de renta fija';
  document.getElementById('rf-prev').style.display='none';
}
async function cargarRF(){
  var rows=await api('/api/renta_fija');
  var t=document.getElementById('tabla-rf');
  if(!rows.length){t.innerHTML='<tr><td colspan="13"><div class="empty"><div class="empty-icon">🏦</div>Sin inversiones. Usa + Nueva inversión.</div></td></tr>';
    ['rf-tot','rf-dia','rf-año'].forEach(function(id){document.getElementById(id).textContent='$0';});return;}
  var totM=0,totD=0,totA=0;
  t.innerHTML=rows.map(function(r){
    totM+=r.monto||0;totD+=r.rend_diario||0;totA+=r.rend_anual||0;
    var dv=r.dias_para_vencer;
    var vH=r.vence?'<span class="tag '+(dv<0?'tag-red':dv<=30?'tag-amber':'tag-green')+'">'+(dv<0?'Vencido':r.vence)+'</span>':'—';
    return '<tr><td><b>'+r.tipo+'</b>'+(r.notas?'<br><span style="font-size:10px;color:var(--muted)">'+r.notas+'</span>':'')+'</td>'
      +'<td style="color:var(--muted)">'+r.canal+'</td>'
      +'<td class="mono">'+COP(r.monto)+'</td>'
      +'<td class="mono up">'+Pct(r.tasa_ea)+'</td>'
      +'<td class="mono" style="color:var(--blue)">'+Pct(r.tasa_neta)+'</td>'
      +'<td><span class="period-badge '+(PCLS[r.periodo]||'')+'">'+r.periodo+'</span></td>'
      +'<td class="t-right"><span class="rend-daily">+'+COP(r.rend_diario)+'</span></td>'
      +'<td class="mono t-right up">+'+COP(r.rend_periodo)+'</td>'
      +'<td class="mono t-right" style="color:var(--muted)">+'+COP(r.rend_anual)+'</td>'
      +'<td class="mono t-right" style="color:var(--purple)">+'+COP(r.rend_acumulado)+'</td>'
      +'<td>'+vH+'</td>'
      +'<td><span class="tag '+(r.estado==='activo'?'tag-green':r.estado==='vencido'?'tag-red':'tag-amber')+'">'+r.estado+'</span></td>'
      +'<td><div style="display:flex;gap:4px">'
        +'<button class="btn btn-edit btn-xs" onclick="editarRF('+r.id+')">✏️</button>'
        +'<button class="btn btn-danger btn-xs" onclick="elimRF('+r.id+')">🗑</button>'
      +'</div></td></tr>';
  }).join('');
  document.getElementById('rf-tot').textContent=COP(totM);
  document.getElementById('rf-dia').textContent='+'+COP(totD);
  document.getElementById('rf-año').textContent='+'+COP(totA);
}
async function editarRF(id){
  var r=await api('/api/renta_fija/'+id);
  document.getElementById('rf-id').value=id;
  document.getElementById('rf-tipo').value=r.tipo||'';
  document.getElementById('rf-canal').value=r.canal||'';
  document.getElementById('rf-monto').value=r.monto||'';
  document.getElementById('rf-tasa').value=r.tasa_ea||'';
  document.getElementById('rf-com').value=r.com_ea||0;
  document.getElementById('rf-periodo').value=r.periodo||'mensual';
  document.getElementById('rf-ini').value=r.ini||'';
  document.getElementById('rf-vence').value=r.vence||'';
  document.getElementById('rf-estado').value=r.estado||'activo';
  document.getElementById('rf-notas').value=r.notas||'';
  document.getElementById('rf-title').textContent='Editar inversión';
  previewRF();openModal('modal-rf');
}
async function guardarRF(){
  var id=document.getElementById('rf-id').value;
  var d={tipo:document.getElementById('rf-tipo').value,canal:document.getElementById('rf-canal').value,
    monto:parseFloat(document.getElementById('rf-monto').value)||0,
    tasa_ea:parseFloat(document.getElementById('rf-tasa').value)||0,
    com_ea:parseFloat(document.getElementById('rf-com').value)||0,
    periodo:document.getElementById('rf-periodo').value,
    ini:document.getElementById('rf-ini').value,vence:document.getElementById('rf-vence').value,
    estado:document.getElementById('rf-estado').value,notas:document.getElementById('rf-notas').value};
  if(!d.monto){toast('Ingresa el monto',false);return;}
  var r=id?await api('/api/renta_fija/'+id,'PUT',d):await api('/api/renta_fija','POST',d);
  if(r.ok||r.id){toast(id?'Actualizada ✅':'Guardada ✅');resetRF();closeModal('modal-rf');cargarRF();}
  else toast('Error',false);
}
async function elimRF(id){if(!confirm('¿Eliminar?'))return;await api('/api/renta_fija/'+id,'DELETE');toast('Eliminada');cargarRF();}
resetRF();cargarRF();
</script>"""
    return base_html(c, session["user_name"], "renta_fija")


# ══════════════════════════════════════════════════════════════
#  AHORRO — METAS con filtros por tipo/estado/progreso
# ══════════════════════════════════════════════════════════════

@app.route("/ahorro")
@login_required
def ahorro():
    c = """<div class="page">
  <div class="topbar">
    <div><div class="page-title">Metas de Ahorro</div><div class="page-sub">Emergencia · Viaje · Educación · Vivienda · Retiro</div></div>
    <div><button class="btn btn-primary btn-sm" onclick="resetMeta();openModal('modal-meta')">Nueva meta</button></div>
  </div>

  <div class="kpi-grid g4">
    <div class="kpi"><div class="kpi-label">Total ahorrado</div><div class="kpi-val" id="m-tot">$0</div></div>
    <div class="kpi"><div class="kpi-label">Metas activas</div><div class="kpi-val" id="m-act">0</div></div>
    <div class="kpi"><div class="kpi-label">Metas cumplidas</div><div class="kpi-val up" id="m-cum">0</div></div>
    <div class="kpi"><div class="kpi-label">Ahorro mensual total</div><div class="kpi-val" id="m-men">$0</div></div>
  </div>

  <!-- Filtros -->
  <div class="filter-bar">
    <select id="f-tipo-meta" onchange="cargarMetas()">
      <option value="">Todos los tipos</option>
      <option value="ahorro">Ahorro general</option>
      <option value="emergencia">Fondo emergencia</option>
      <option value="viaje">Viaje</option>
      <option value="educacion">Educación</option>
      <option value="vivienda">Vivienda</option>
      <option value="retiro">Retiro / pensión</option>
      <option value="otro">Otro</option>
    </select>
    <select id="f-estado-meta" onchange="cargarMetas()">
      <option value="">Todos los estados</option>
      <option value="activa">Activas</option>
      <option value="cumplida">Cumplidas</option>
      <option value="pausada">Pausadas</option>
      <option value="cancelada">Canceladas</option>
    </select>
    <select id="f-prog-meta" onchange="cargarMetas()">
      <option value="">Cualquier progreso</option>
      <option value="0">Sin avance (0%)</option>
      <option value="25">Más de 25%</option>
      <option value="50">Más de 50%</option>
      <option value="75">Más de 75%</option>
      <option value="100">Cumplidas (100%)</option>
    </select>
    <input type="text" id="f-busq-meta" placeholder="Buscar por nombre…" oninput="cargarMetas()" style="flex:1;max-width:200px"/>
    <button class="btn btn-sm" onclick="limpiarFiltros()">✕ Limpiar</button>
  </div>

  <div id="metas-container"></div>
</div>

<!-- MODAL NUEVA/EDITAR META -->
<div id="modal-meta" class="modal-overlay" onclick="if(event.target===this){resetMeta();closeModal('modal-meta')}">
  <div class="modal">
    <div class="modal-header"><div class="modal-title" id="meta-title">Nueva meta de ahorro</div>
      <button class="btn btn-sm" onclick="resetMeta();closeModal('modal-meta')">✕</button></div>
    <div class="modal-body">
      <input type="hidden" id="meta-id"/>
      <div class="form-grid g2f">
        <div class="form-group"><label>Nombre de la meta</label><input type="text" id="meta-nom" placeholder="Fondo emergencia / Viaje a Europa…"/></div>
        <div class="form-group"><label>Tipo de meta</label><select id="meta-tipo">
          <option value="emergencia">Fondo de emergencia</option>
          <option value="viaje">Viaje</option>
          <option value="educacion">Educación</option>
          <option value="vivienda">Vivienda</option>
          <option value="retiro">Retiro / pensión</option>
          <option value="ahorro">Ahorro general</option>
          <option value="otro">Otro</option>
        </select></div>
        <div class="form-group"><label>Objetivo (COP $)</label><input type="number" id="meta-obj" placeholder="15000000"/></div>
        <div class="form-group"><label>Ahorrado actual ($)</label><input type="number" id="meta-act" placeholder="5000000"/></div>
        <div class="form-group"><label>Ahorro mensual destinado ($)</label><input type="number" id="meta-men" placeholder="500000"/></div>
        <div class="form-group"><label>Fecha límite</label><input type="date" id="meta-fecha"/></div>
        <div class="form-group"><label>Tipo de cuenta / instrumento</label><select id="meta-cta">
          <option>Cuenta de ahorros</option><option>CDT corto plazo</option>
          <option>Cajita Nu / Nequi</option><option>FIC mercado monetario</option>
          <option>Efectivo</option><option>Otro</option>
        </select></div>
        <div class="form-group"><label>Estado</label><select id="meta-estado">
          <option value="activa">Activa</option><option value="pausada">Pausada</option>
          <option value="cumplida">Cumplida ✅</option><option value="cancelada">Cancelada</option>
        </select></div>
        <div class="form-group full"><label>Notas</label><input type="text" id="meta-notas" placeholder="Estrategia, banco, observaciones…"/></div>
      </div>
    </div>
    <div class="modal-footer">
      <button class="btn" onclick="resetMeta();closeModal('modal-meta')">Cancelar</button>
      <button class="btn btn-primary" onclick="guardarMeta()">Guardar</button>
    </div>
  </div>
</div>

<!-- MODAL MOVIMIENTO META -->
<div id="modal-mov-meta" class="modal-overlay" onclick="if(event.target===this)closeModal('modal-mov-meta')">
  <div class="modal" style="max-width:420px">
    <div class="modal-header"><div class="modal-title" id="mov-meta-title">Registrar movimiento</div>
      <button class="btn btn-sm" onclick="closeModal('modal-mov-meta')">✕</button></div>
    <div class="modal-body">
      <input type="hidden" id="mov-meta-id"/>
      <div class="form-grid" style="gap:14px">
        <div class="form-group"><label>Tipo de movimiento</label><select id="mov-meta-tipo">
          <option value="deposito_meta">Depósito / Abono a la meta</option>
          <option value="retiro_meta">Retiro parcial</option>
          <option value="ajuste_meta">Corrección / ajuste de saldo</option>
        </select></div>
        <div class="form-group"><label>Monto (COP $)</label><input type="number" id="mov-meta-monto" placeholder="500000"/></div>
        <div class="form-group"><label>Fecha</label><input type="date" id="mov-meta-fecha"/></div>
        <div class="form-group"><label>Notas / contexto</label><input type="text" id="mov-meta-ctx" placeholder="Descripción del movimiento…"/></div>
      </div>
    </div>
    <div class="modal-footer">
      <button class="btn" onclick="closeModal('modal-mov-meta')">Cancelar</button>
      <button class="btn btn-primary" onclick="guardarMovMeta()">Guardar</button>
    </div>
  </div>
</div>

<script>
var TIPO_META_ICONS={};
var TIPO_META_LABELS={emergencia:'Fondo emergencia',viaje:'Viaje',educacion:'Educación',vivienda:'Vivienda',retiro:'Retiro/Pensión',ahorro:'Ahorro general',otro:'Otro'};
var ESTADO_META_COLORS={activa:'tag-blue',cumplida:'tag-green',pausada:'tag-amber',cancelada:'tag-red'};

function limpiarFiltros(){
  ['f-tipo-meta','f-estado-meta','f-prog-meta'].forEach(function(id){document.getElementById(id).selectedIndex=0;});
  document.getElementById('f-busq-meta').value='';
  cargarMetas();
}

async function cargarMetas(){
  var tipo=document.getElementById('f-tipo-meta').value;
  var estado=document.getElementById('f-estado-meta').value;
  var prog=parseInt(document.getElementById('f-prog-meta').value)||0;
  var busq=document.getElementById('f-busq-meta').value.toLowerCase();

  var params=[];
  if(tipo)params.push('tipo='+encodeURIComponent(tipo));
  if(estado)params.push('estado='+encodeURIComponent(estado));
  var url='/api/metas'+(params.length?'?'+params.join('&'):'');
  var rows=await api(url);

  // Filtros del lado cliente
  if(prog>0)rows=rows.filter(function(r){return r.pct>=prog;});
  if(busq)rows=rows.filter(function(r){return(r.nombre||'').toLowerCase().includes(busq);});

  // KPIs
  var totA=0,activas=0,cumplidas=0,totM=0;
  rows.forEach(function(r){totA+=r.actual||0;totM+=r.mensual||0;if(r.estado==='activa')activas++;if(r.estado==='cumplida')cumplidas++;});
  document.getElementById('m-tot').textContent=COP(totA);
  document.getElementById('m-act').textContent=activas;
  document.getElementById('m-cum').textContent=cumplidas;
  document.getElementById('m-men').textContent=COP(totM);

  var cont=document.getElementById('metas-container');
  if(!rows.length){cont.innerHTML='<div class="empty"><div class="empty-icon">🏧</div>Sin metas con esos filtros. <a href="#" onclick="limpiarFiltros();return false">Limpiar filtros</a></div>';return;}

  cont.innerHTML=rows.map(function(r){
    var pct=Math.min(100,r.pct||0);
    var colProg=pct>=100?'var(--green)':pct>=60?'var(--blue)':'var(--amber)';
    var mr=r.mensual>0&&r.actual<r.objetivo?Math.ceil((r.objetivo-r.actual)/r.mensual):null;
    var icon='';
    var label=TIPO_META_LABELS[r.tipo_meta]||r.tipo_meta;
    return '<div class="card" style="margin-bottom:12px"><div class="card-body">'
      +'<div style="display:flex;align-items:flex-start;gap:14px">'
        
        +'<div style="flex:1">'
          +'<div style="display:flex;align-items:center;gap:8px;margin-bottom:4px">'
            +'<div style="font-size:14px;font-weight:600">'+r.nombre+'</div>'
            +'<span class="tag '+(ESTADO_META_COLORS[r.estado]||'tag-gray')+'">'+r.estado+'</span>'
            +'<span class="tag tag-gray">'+label+'</span>'
          +'</div>'
          +'<div style="display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin:12px 0">'
            +'<div><div style="font-size:9.5px;color:var(--muted);margin-bottom:3px;font-weight:600;text-transform:uppercase">Ahorrado</div><div class="mono" style="font-size:15px;font-weight:600;color:var(--blue)">'+COP(r.actual)+'</div></div>'
            +'<div><div style="font-size:9.5px;color:var(--muted);margin-bottom:3px;font-weight:600;text-transform:uppercase">Objetivo</div><div class="mono" style="font-size:15px;font-weight:600">'+COP(r.objetivo)+'</div></div>'
            +'<div><div style="font-size:9.5px;color:var(--muted);margin-bottom:3px;font-weight:600;text-transform:uppercase">Mensual</div><div class="mono up" style="font-size:15px;font-weight:600">'+COP(r.mensual)+'</div></div>'
            +'<div><div style="font-size:9.5px;color:var(--muted);margin-bottom:3px;font-weight:600;text-transform:uppercase">Progreso</div><div class="mono" style="font-size:15px;font-weight:600;color:'+colProg+'">'+pct.toFixed(0)+'%</div></div>'
          +'</div>'
          +'<div class="prog" style="height:8px;margin-bottom:8px"><div class="prog-fill" style="width:'+Math.min(100,pct)+'%;background:'+colProg+'"></div></div>'
          +'<div style="display:flex;gap:8px;flex-wrap:wrap;font-size:11.5px;color:var(--muted)">'
            +(r.fecha?'<span>Fecha límite: '+fmtFecha(r.fecha)+'</span>':'')
            +(mr?'<span>Faltan ~'+mr+' meses</span>':'')
            +(r.tipo_cta?'<span>'+r.tipo_cta+'</span>':'')
            +(r.notas?'<span>'+r.notas+'</span>':'')
          +'</div>'
        +'</div>'
        +'<div style="display:flex;flex-direction:column;gap:6px">'
          +'<button class="btn btn-success btn-xs" onclick="abrirMovMeta('+r.id+',\''+r.nombre.replace(/'/g,"\\'")+'\')" title="Registrar depósito o retiro">💸 Movimiento</button>'
          +'<button class="btn btn-edit btn-xs" onclick="editarMeta('+r.id+')" title="Editar">✏️</button>'
          +(r.estado!=='cumplida'?'<button class="btn btn-xs" style="background:var(--gbg);color:var(--green);border-color:transparent" onclick="marcarCumplida('+r.id+')" title="Marcar cumplida">✅</button>':'')
          +'<button class="btn btn-danger btn-xs" onclick="elimMeta('+r.id+')" title="Eliminar">🗑</button>'
        +'</div>'
      +'</div>'
    +'</div></div>';
  }).join('');
}

function abrirMovMeta(id,nombre){
  document.getElementById('mov-meta-id').value=id;
  document.getElementById('mov-meta-title').textContent='Movimiento: '+nombre;
  document.getElementById('mov-meta-tipo').selectedIndex=0;
  document.getElementById('mov-meta-monto').value='';
  document.getElementById('mov-meta-fecha').value=hoy();
  document.getElementById('mov-meta-ctx').value='';
  openModal('modal-mov-meta');
}

async function guardarMovMeta(){
  var id=parseInt(document.getElementById('mov-meta-id').value);
  var tipo=document.getElementById('mov-meta-tipo').value;
  var monto=parseFloat(document.getElementById('mov-meta-monto').value)||0;
  var fecha=document.getElementById('mov-meta-fecha').value||hoy();
  var ctx=document.getElementById('mov-meta-ctx').value;
  if(!monto){toast('Ingresa el monto',false);return;}
  var r=await api('/api/movimientos','POST',{cat:'metas',inv_id:id,tipo:tipo,monto:monto,fecha:fecha,ctx:ctx});
  if(r.ok||r.id){toast('Movimiento registrado ✅');closeModal('modal-mov-meta');cargarMetas();}
  else toast('Error',false);
}

async function marcarCumplida(id){
  if(!confirm('¿Marcar esta meta como cumplida?'))return;
  await api('/api/metas/'+id,'PUT',{estado:'cumplida'});
  toast('¡Meta cumplida! 🎉');cargarMetas();
}

function resetMeta(){
  ['meta-tipo','meta-cta','meta-estado'].forEach(function(id){var el=document.getElementById(id);if(el)el.selectedIndex=0;});
  ['meta-nom','meta-obj','meta-act','meta-men','meta-fecha','meta-notas','meta-id'].forEach(function(id){var el=document.getElementById(id);if(el)el.value='';});
  document.getElementById('meta-title').textContent='Nueva meta de ahorro';
}

async function editarMeta(id){
  var r=await api('/api/metas/'+id);
  document.getElementById('meta-id').value=id;
  document.getElementById('meta-nom').value=r.nombre||'';
  document.getElementById('meta-tipo').value=r.tipo_meta||'ahorro';
  document.getElementById('meta-obj').value=r.objetivo||'';
  document.getElementById('meta-act').value=r.actual||'';
  document.getElementById('meta-men').value=r.mensual||'';
  document.getElementById('meta-fecha').value=r.fecha||'';
  document.getElementById('meta-cta').value=r.tipo_cta||'';
  document.getElementById('meta-estado').value=r.estado||'activa';
  document.getElementById('meta-notas').value=r.notas||'';
  document.getElementById('meta-title').textContent='Editar meta';
  openModal('modal-meta');
}

async function guardarMeta(){
  var id=document.getElementById('meta-id').value;
  var d={nombre:document.getElementById('meta-nom').value,
    tipo_meta:document.getElementById('meta-tipo').value,
    objetivo:parseFloat(document.getElementById('meta-obj').value)||0,
    actual:parseFloat(document.getElementById('meta-act').value)||0,
    mensual:parseFloat(document.getElementById('meta-men').value)||0,
    fecha:document.getElementById('meta-fecha').value,
    tipo_cta:document.getElementById('meta-cta').value,
    estado:document.getElementById('meta-estado').value,
    notas:document.getElementById('meta-notas').value};
  if(!d.objetivo){toast('Define el objetivo',false);return;}
  var r=id?await api('/api/metas/'+id,'PUT',d):await api('/api/metas','POST',d);
  if(r.ok||r.id){toast(id?'Meta actualizada ✅':'Meta guardada ✅');resetMeta();closeModal('modal-meta');cargarMetas();}
  else toast('Error',false);
}

async function elimMeta(id){if(!confirm('¿Eliminar esta meta?'))return;await api('/api/metas/'+id,'DELETE');toast('Eliminada');cargarMetas();}
cargarMetas();
</script>"""
    return base_html(c, session["user_name"], "ahorro")


# ══════════════════════════════════════════════════════════════
#  DEUDAS — con fecha_inicio, abono capital, ahorro intereses
# ══════════════════════════════════════════════════════════════

@app.route("/deudas")
@login_required
def deudas():
    c = """<div class="page">
  <div class="topbar">
    <div><div class="page-title">Gestión de Deudas</div><div class="page-sub">Método avalancha · Abonos a capital · Ahorro en intereses</div></div>
    <div><button class="btn btn-primary btn-sm" onclick="resetDeuda();openModal('modal-deu')">Nueva deuda</button></div>
  </div>

  <div class="kpi-grid g4">
    <div class="kpi"><div class="kpi-label">Total deudas activas</div><div class="kpi-val dn" id="d-tot">$0</div></div>
    <div class="kpi"><div class="kpi-label">Cuotas / mes</div><div class="kpi-val dn" id="d-cuot">$0</div></div>
    <div class="kpi"><div class="kpi-label">Interés total restante</div><div class="kpi-val dn" id="d-int">$0</div></div>
    <div class="kpi"><div class="kpi-label">Ahorro intereses YTD</div><div class="kpi-val up" id="d-aho">$0</div><div class="kpi-sub">Por abonos a capital</div></div>
  </div>

  <div class="alert alert-info"><div><b>Método avalancha:</b> paga primero la deuda con mayor tasa. <b>Abono a capital</b> reduce el saldo sin contar como cuota — calcula automáticamente cuántos intereses te ahorras.</div></div>

  <div id="deudas-container"></div>
</div>

<!-- MODAL NUEVA/EDITAR DEUDA -->
<div id="modal-deu" class="modal-overlay" onclick="if(event.target===this){resetDeuda();closeModal('modal-deu')}">
  <div class="modal">
    <div class="modal-header"><div class="modal-title" id="deu-title">Nueva deuda</div>
      <button class="btn btn-sm" onclick="resetDeuda();closeModal('modal-deu')">✕</button></div>
    <div class="modal-body">
      <input type="hidden" id="deu-id"/>
      <div class="form-grid g2f">
        <div class="form-group"><label>Tipo de deuda</label><select id="deu-tipo">
          <option>Tarjeta de crédito</option><option>Crédito de consumo</option>
          <option>Crédito hipotecario</option><option>Leasing habitacional</option>
          <option>Crédito vehículo</option><option>Libranza</option><option>Microcrédito</option>
          <option>ICETEX</option><option>Deuda familiar / informal</option><option>Otro</option>
        </select></div>
        <div class="form-group"><label>Entidad / Acreedor</label><input type="text" id="deu-entidad" placeholder="Bancolombia, Falabella, familiar…"/></div>
        <div class="form-group"><label>Fecha inicio del crédito</label><input type="date" id="deu-inicio"/></div>
        <div class="form-group"><label>Fecha próximo pago</label><input type="date" id="deu-pago"/></div>
        <div class="form-group"><label>Monto original del crédito ($)</label><input type="number" id="deu-saldo-ini" placeholder="10000000" oninput="calcDeuPreview()"/></div>
        <div class="form-group"><label>Saldo actual vigente ($)</label><input type="number" id="deu-saldo-act" placeholder="8500000" oninput="calcDeuPreview()"/></div>
        <div class="form-group"><label>Cuota mensual pactada ($)</label><input type="number" id="deu-cuota" placeholder="450000" oninput="calcDeuPreview()"/></div>
        <div class="form-group"><label>Total cuotas del crédito</label><input type="number" id="deu-cuotas-tot" placeholder="36" oninput="calcDeuPreview()"/></div>
        <div class="form-group"><label>Cuotas ya pagadas</label><input type="number" id="deu-cuotas-pag" placeholder="12" oninput="calcDeuPreview()"/></div>
        <div class="form-group"><label>Tasa interés E.A. (%)</label><input type="number" id="deu-tasa" step="0.1" placeholder="28.5" oninput="calcDeuPreview()"/></div>
        <div class="form-group"><label>Prioridad (avalancha)</label><select id="deu-prior">
          <option value="alta">Alta — mayor tasa</option>
          <option value="media" selected>Media</option>
          <option value="baja">Baja — menor tasa</option>
        </select></div>
        <div class="form-group"><label>Estado</label><select id="deu-estado">
          <option value="activa">Activa</option>
          <option value="liquidada">Liquidada ✅</option>
          <option value="refinanciada">Refinanciada</option>
        </select></div>
        <div class="form-group full"><label>Notas</label><input type="text" id="deu-notas" placeholder="Estrategia, condiciones especiales, refinanciación…"/></div>
      </div>
      <!-- Preview métricas -->
      <div id="deu-prev" style="display:none;margin-top:14px">
        <div style="display:grid;grid-template-columns:repeat(3,1fr);gap:10px">
          <div style="background:var(--rbg);border-radius:var(--r);padding:12px;text-align:center">
            <div style="font-size:10px;color:var(--red);font-weight:600;text-transform:uppercase;margin-bottom:4px">Costo total</div>
            <div class="mono" id="prev-costo" style="font-size:15px;font-weight:700;color:var(--red)"></div>
          </div>
          <div style="background:var(--rbg);border-radius:var(--r);padding:12px;text-align:center">
            <div style="font-size:10px;color:var(--red);font-weight:600;text-transform:uppercase;margin-bottom:4px">Interés total</div>
            <div class="mono" id="prev-int" style="font-size:15px;font-weight:700;color:var(--red)"></div>
          </div>
          <div style="background:var(--bbg);border-radius:var(--r);padding:12px;text-align:center">
            <div style="font-size:10px;color:var(--blue);font-weight:600;text-transform:uppercase;margin-bottom:4px">Fin estimado</div>
            <div id="prev-fin" style="font-size:13px;font-weight:700;color:var(--blue)"></div>
          </div>
        </div>
      </div>
    </div>
    <div class="modal-footer">
      <button class="btn" onclick="resetDeuda();closeModal('modal-deu')">Cancelar</button>
      <button class="btn btn-primary" onclick="guardarDeuda()">Guardar</button>
    </div>
  </div>
</div>

<!-- MODAL MOVIMIENTO DEUDA -->
<div id="modal-mov-deu" class="modal-overlay" onclick="if(event.target===this)closeModal('modal-mov-deu')">
  <div class="modal" style="max-width:500px">
    <div class="modal-header"><div class="modal-title" id="mov-deu-title">Registrar movimiento</div>
      <button class="btn btn-sm" onclick="closeModal('modal-mov-deu')">✕</button></div>
    <div class="modal-body">
      <input type="hidden" id="mov-deu-id"/>
      <input type="hidden" id="mov-deu-sa"/>
      <input type="hidden" id="mov-deu-tasa"/>
      <input type="hidden" id="mov-deu-cuota"/>
      <div class="form-grid" style="gap:14px">
        <div class="form-group"><label>Tipo de movimiento</label><select id="mov-deu-tipo" onchange="onChangeTipoMov()">
          <option value="pago_cuota">Pago cuota normal</option>
          <option value="abono_capital">Abono extra a capital</option>
          <option value="ajuste_saldo">Ajuste de saldo (refinanciación)</option>
          <option value="cambio_condiciones">Cambio de tasa / cuota</option>
          <option value="liquidacion">Liquidación / cancelación total</option>
        </select></div>
        <div class="form-group"><label>Monto (COP $)</label><input type="number" id="mov-deu-monto" placeholder="500000" oninput="calcAhorroAbono()"/></div>
        <div class="form-group" id="row-nueva-tasa" style="display:none"><label>Nueva tasa E.A. (%) — si cambió</label><input type="number" id="mov-deu-nueva-tasa" step="0.1" placeholder="25.0"/></div>
        <div class="form-group" id="row-nueva-cuota" style="display:none"><label>Nueva cuota mensual ($)</label><input type="number" id="mov-deu-nueva-cuota" placeholder="420000"/></div>
        <div class="form-group"><label>Fecha del movimiento</label><input type="date" id="mov-deu-fecha"/></div>
        <div class="form-group"><label>Notas / contexto</label><input type="text" id="mov-deu-ctx" placeholder="Banco, comprobante, detalles…"/></div>
      </div>
      <!-- Cálculo de ahorro en intereses para abono a capital -->
      <div id="ahorro-box" class="ahorro-box" style="display:none">
        <div class="ah-title">💚 Ahorro estimado por este abono a capital</div>
        <div class="ah-val" id="ahorro-int">$0</div>
        <div class="ah-sub" id="ahorro-meses"></div>
      </div>
    </div>
    <div class="modal-footer">
      <button class="btn" onclick="closeModal('modal-mov-deu')">Cancelar</button>
      <button class="btn btn-primary" onclick="guardarMovDeu()">Registrar</button>
    </div>
  </div>
</div>

<script>
function calcDeuPreview(){
  var si=parseFloat(document.getElementById('deu-saldo-ini').value)||0;
  var sa=parseFloat(document.getElementById('deu-saldo-act').value)||si;
  var c=parseFloat(document.getElementById('deu-cuota').value)||0;
  var ct=parseInt(document.getElementById('deu-cuotas-tot').value)||0;
  var cp=parseInt(document.getElementById('deu-cuotas-pag').value)||0;
  var cr=Math.max(0,ct-cp);
  if(!c||!ct){document.getElementById('deu-prev').style.display='none';return;}
  var costo=c*ct;var intTot=Math.max(0,costo-si);
  document.getElementById('prev-costo').textContent=COP(costo);
  document.getElementById('prev-int').textContent=COP(intTot);
  var ini=document.getElementById('deu-inicio').value;
  if(ini&&cr>0){var d=new Date(ini);d.setMonth(d.getMonth()+ct);document.getElementById('prev-fin').textContent=d.toLocaleDateString('es-CO',{month:'short',year:'numeric'});}
  else document.getElementById('prev-fin').textContent='—';
  document.getElementById('deu-prev').style.display='block';
}

function onChangeTipoMov(){
  var tipo=document.getElementById('mov-deu-tipo').value;
  document.getElementById('row-nueva-tasa').style.display=(tipo==='cambio_condiciones'?'flex':'none');
  document.getElementById('row-nueva-cuota').style.display=(tipo==='cambio_condiciones'?'flex':'none');
  document.getElementById('ahorro-box').style.display='none';
  if(tipo==='abono_capital')calcAhorroAbono();
}

function calcAhorroAbono(){
  var tipo=document.getElementById('mov-deu-tipo').value;
  if(tipo!=='abono_capital'){document.getElementById('ahorro-box').style.display='none';return;}
  var sa=parseFloat(document.getElementById('mov-deu-sa').value)||0;
  var cuota=parseFloat(document.getElementById('mov-deu-cuota').value)||0;
  var tasa=parseFloat(document.getElementById('mov-deu-tasa').value)||0;
  var abono=parseFloat(document.getElementById('mov-deu-monto').value)||0;
  if(!sa||!cuota||!tasa||!abono){document.getElementById('ahorro-box').style.display='none';return;}
  var ea=tasa/100;var tm=Math.pow(1+ea,1/12)-1;
  function contarCuotas(s){var n=0;while(s>0&&n<600){s=s*(1+tm)-cuota;n++;}return n;}
  var orig=contarCuotas(sa);var nuevo=contarCuotas(Math.max(0,sa-abono));
  var elim=Math.max(0,orig-nuevo);
  var ahorro=Math.max(0,elim*cuota-abono);
  document.getElementById('ahorro-box').style.display='block';
  document.getElementById('ahorro-int').textContent=COP(ahorro);
  document.getElementById('ahorro-meses').textContent=elim>0?'Eliminas ~'+elim+' cuotas · Terminas '+elim+' meses antes':'Con este abono completas la deuda';
}

function abrirMovDeu(id,nombre,sa,tasa,cuota){
  document.getElementById('mov-deu-id').value=id;
  document.getElementById('mov-deu-sa').value=sa;
  document.getElementById('mov-deu-tasa').value=tasa;
  document.getElementById('mov-deu-cuota').value=cuota;
  document.getElementById('mov-deu-title').textContent='Movimiento: '+nombre;
  document.getElementById('mov-deu-tipo').selectedIndex=0;
  document.getElementById('mov-deu-monto').value='';
  document.getElementById('mov-deu-fecha').value=hoy();
  document.getElementById('mov-deu-ctx').value='';
  document.getElementById('ahorro-box').style.display='none';
  document.getElementById('row-nueva-tasa').style.display='none';
  document.getElementById('row-nueva-cuota').style.display='none';
  openModal('modal-mov-deu');
}

async function guardarMovDeu(){
  var id=parseInt(document.getElementById('mov-deu-id').value);
  var tipo=document.getElementById('mov-deu-tipo').value;
  var monto=parseFloat(document.getElementById('mov-deu-monto').value)||0;
  var fecha=document.getElementById('mov-deu-fecha').value||hoy();
  var ctx=document.getElementById('mov-deu-ctx').value;
  var sa=parseFloat(document.getElementById('mov-deu-sa').value)||0;
  var cuota=parseFloat(document.getElementById('mov-deu-cuota').value)||0;
  var tasa=parseFloat(document.getElementById('mov-deu-tasa').value)||0;
  if(!monto&&tipo!=='liquidacion'){toast('Ingresa el monto',false);return;}
  // Calcular ahorro en intereses para abono a capital
  var ahorro_int=0,cuotas_menos=0;
  if(tipo==='abono_capital'&&monto>0){
    var ea=tasa/100;var tm=Math.pow(1+ea,1/12)-1;
    function cc(s){var n=0;while(s>0&&n<600){s=s*(1+tm)-cuota;n++;}return n;}
    var elim=Math.max(0,cc(sa)-cc(Math.max(0,sa-monto)));
    cuotas_menos=elim;ahorro_int=Math.max(0,elim*cuota-monto);
  }
  var extra={};
  if(tipo==='cambio_condiciones'){
    var nt=parseFloat(document.getElementById('mov-deu-nueva-tasa').value)||0;
    var nc=parseFloat(document.getElementById('mov-deu-nueva-cuota').value)||0;
    extra={nueva_tasa:nt,nueva_cuota:nc};
  }
  var r=await api('/api/movimientos','POST',Object.assign({cat:'deudas',inv_id:id,tipo:tipo,monto:monto,fecha:fecha,ctx:ctx,ahorro_interes:ahorro_int,cuotas_menos:cuotas_menos},extra));
  if(r.ok||r.id){
    var msg='Registrado ✅';
    if(ahorro_int>0)msg='Registrado ✅ — Ahorras '+COP(ahorro_int)+' en intereses';
    toast(msg);closeModal('modal-mov-deu');cargarDeudas();
  } else toast('Error',false);
}

async function cargarDeudas(){
  var rows=await api('/api/deudas');
  var totS=0,totC=0,totI=0,totAho=0;
  rows.forEach(function(r){if(r.estado==='activa'){totS+=r.saldo_actual||0;totC+=r.cuota||0;totI+=r.int_restante||0;}totAho+=r.ahorro_acumulado||0;});
  document.getElementById('d-tot').textContent=COP(totS);
  document.getElementById('d-cuot').textContent=COP(totC);
  document.getElementById('d-int').textContent=COP(totI);
  document.getElementById('d-aho').textContent=COP(totAho);

  var cont=document.getElementById('deudas-container');
  if(!rows.length){cont.innerHTML='<div class="empty"><div class="empty-icon">💳</div>Sin deudas registradas. ¡Excelente!</div>';return;}

  cont.innerHTML=rows.map(function(r){
    var prioCls={alta:'tag-red',media:'tag-amber',baja:'tag-green'}[r.prior]||'tag-gray';
    var estadoCls={activa:'tag-blue',liquidada:'tag-green',refinanciada:'tag-amber'}[r.estado]||'tag-gray';
    var pct=r.saldo_inicial>0?Math.min(100,(1-r.saldo_actual/r.saldo_inicial)*100):0;
    var dp=r.dias_pago;
    var pagoHtml=dp!==null&&dp!==undefined?'<span class="tag '+(dp<0?'tag-red':dp<=5?'tag-amber':'tag-gray')+'">Pago '+(dp<0?'vencido hace '+Math.abs(dp)+'d':dp===0?'hoy':dp+'d')+'</span>':'';
    return '<div class="card" style="margin-bottom:12px"><div class="card-body">'
      +'<div style="display:flex;align-items:flex-start;gap:12px">'
        
        +'<div style="flex:1">'
          +'<div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-bottom:10px">'
            +'<b style="font-size:14px">'+r.entidad+' — '+r.tipo+'</b>'
            +'<span class="tag '+estadoCls+'">'+r.estado+'</span>'
            +'<span class="tag '+prioCls+'">Prioridad '+r.prior+'</span>'
            +pagoHtml
          +'</div>'
          +'<div style="display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin-bottom:12px">'
            +'<div><div style="font-size:9.5px;color:var(--muted);font-weight:600;text-transform:uppercase;margin-bottom:3px">Saldo actual</div><div class="mono dn" style="font-size:15px;font-weight:700">'+COP(r.saldo_actual)+'</div></div>'
            +'<div><div style="font-size:9.5px;color:var(--muted);font-weight:600;text-transform:uppercase;margin-bottom:3px">Cuota/mes</div><div class="mono dn" style="font-size:15px;font-weight:700">'+COP(r.cuota)+'</div></div>'
            +'<div><div style="font-size:9.5px;color:var(--muted);font-weight:600;text-transform:uppercase;margin-bottom:3px">Tasa E.A.</div><div class="mono dn" style="font-size:15px;font-weight:700">'+Pct(r.tasa_ea)+'</div></div>'
            +'<div><div style="font-size:9.5px;color:var(--muted);font-weight:600;text-transform:uppercase;margin-bottom:3px">Tasa mensual</div><div class="mono dn" style="font-size:14px;font-weight:700">'+Pct(r.tasa_mensual||0)+'</div></div>'
          +'</div>'
          +'<div style="display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin-bottom:12px">'
            +'<div><div style="font-size:9.5px;color:var(--muted);font-weight:600;text-transform:uppercase;margin-bottom:3px">Cuotas rest.</div><div class="mono" style="font-size:14px;font-weight:600">'+r.cuotas_rest+'</div></div>'
            +'<div><div style="font-size:9.5px;color:var(--muted);font-weight:600;text-transform:uppercase;margin-bottom:3px">Int. restante</div><div class="mono dn" style="font-size:14px;font-weight:600">'+COP(r.int_restante||0)+'</div></div>'
            +'<div><div style="font-size:9.5px;color:var(--muted);font-weight:600;text-transform:uppercase;margin-bottom:3px">Int. pagado</div><div class="mono" style="font-size:14px;font-weight:600;color:var(--muted)">'+COP(r.int_pagado||0)+'</div></div>'
            +'<div><div style="font-size:9.5px;color:var(--green);font-weight:600;text-transform:uppercase;margin-bottom:3px">Ahorro YTD</div><div class="mono up" style="font-size:14px;font-weight:700">'+COP(r.ahorro_acumulado||0)+'</div></div>'
          +'</div>'
          +'<div style="margin-bottom:8px">'
            +'<div style="display:flex;justify-content:space-between;font-size:11px;color:var(--muted);margin-bottom:3px"><span>Capital pagado: '+COP(r.capital_pagado||0)+'</span><span>'+pct.toFixed(0)+'% cancelado</span></div>'
            +'<div class="prog"><div class="prog-fill" style="width:'+Math.min(100,pct)+'%;background:var(--green)"></div></div>'
          +'</div>'
          +'<div style="font-size:11.5px;color:var(--muted);display:flex;gap:10px;flex-wrap:wrap">'
            +(r.fecha_inicio?'<span>Inicio: '+fmtFecha(r.fecha_inicio)+'</span>':'')
            +(r.fin_normal?'<span>Fin estimado: '+fmtFecha(r.fin_normal)+'</span>':'')
            +(r.int_mes_actual?'<span>Interés del mes: '+COP(r.int_mes_actual)+'</span>':'')
          +'</div>'
        +'</div>'
        +'<div style="display:flex;flex-direction:column;gap:6px">'
          +(r.estado==='activa'?'<button class="btn btn-primary btn-xs" onclick="abrirMovDeu('+r.id+',\''+((r.entidad||r.tipo).replace(/'/g,"\\\'"))+'\','+r.saldo_actual+','+r.tasa_ea+','+r.cuota+')" title="Registrar pago o abono">Movimiento</button>':'')
          +'<button class="btn btn-edit btn-xs" onclick="editarDeuda('+r.id+')">✏️ Editar</button>'
          +(r.estado==='activa'?'<button class="btn btn-success btn-xs" onclick="liquidarDeuda('+r.id+')">Liquidar</button>':'')
          +'<button class="btn btn-danger btn-xs" onclick="elimDeuda('+r.id+')">🗑</button>'
        +'</div>'
      +'</div>'
    +'</div></div>';
  }).join('');
}

async function liquidarDeuda(id){
  if(!confirm('¿Marcar esta deuda como liquidada?'))return;
  await api('/api/deudas/'+id,'PUT',{estado:'liquidada',saldo_actual:0});
  toast('Deuda liquidada ✅');cargarDeudas();
}

function resetDeuda(){
  ['deu-tipo','deu-prior','deu-estado'].forEach(function(id){var el=document.getElementById(id);if(el)el.selectedIndex=0;});
  ['deu-entidad','deu-inicio','deu-pago','deu-saldo-ini','deu-saldo-act','deu-cuota','deu-cuotas-tot','deu-cuotas-pag','deu-tasa','deu-notas','deu-id'].forEach(function(id){var el=document.getElementById(id);if(el)el.value='';});
  document.getElementById('deu-title').textContent='Nueva deuda';
  document.getElementById('deu-prev').style.display='none';
}

async function editarDeuda(id){
  var r=await api('/api/deudas/'+id);
  document.getElementById('deu-id').value=id;
  document.getElementById('deu-tipo').value=r.tipo||'';
  document.getElementById('deu-entidad').value=r.entidad||'';
  document.getElementById('deu-inicio').value=r.fecha_inicio||'';
  document.getElementById('deu-pago').value=r.fecha_pago||'';
  document.getElementById('deu-saldo-ini').value=r.saldo_inicial||'';
  document.getElementById('deu-saldo-act').value=r.saldo_actual||'';
  document.getElementById('deu-cuota').value=r.cuota||'';
  document.getElementById('deu-cuotas-tot').value=r.cuotas_total||'';
  document.getElementById('deu-cuotas-pag').value=r.cuotas_pagadas||'';
  document.getElementById('deu-tasa').value=r.tasa_ea||'';
  document.getElementById('deu-prior').value=r.prior||'media';
  document.getElementById('deu-estado').value=r.estado||'activa';
  document.getElementById('deu-notas').value=r.notas||'';
  document.getElementById('deu-title').textContent='Editar deuda';
  calcDeuPreview();openModal('modal-deu');
}

async function guardarDeuda(){
  var id=document.getElementById('deu-id').value;
  var d={tipo:document.getElementById('deu-tipo').value,
    entidad:document.getElementById('deu-entidad').value,
    fecha_inicio:document.getElementById('deu-inicio').value,
    fecha_pago:document.getElementById('deu-pago').value,
    saldo_inicial:parseFloat(document.getElementById('deu-saldo-ini').value)||0,
    saldo_actual:parseFloat(document.getElementById('deu-saldo-act').value)||parseFloat(document.getElementById('deu-saldo-ini').value)||0,
    cuota:parseFloat(document.getElementById('deu-cuota').value)||0,
    cuotas_total:parseInt(document.getElementById('deu-cuotas-tot').value)||0,
    cuotas_pagadas:parseInt(document.getElementById('deu-cuotas-pag').value)||0,
    tasa_ea:parseFloat(document.getElementById('deu-tasa').value)||0,
    prior:document.getElementById('deu-prior').value,
    estado:document.getElementById('deu-estado').value,
    notas:document.getElementById('deu-notas').value};
  if(!d.saldo_actual){toast('Ingresa el saldo',false);return;}
  var r=id?await api('/api/deudas/'+id,'PUT',d):await api('/api/deudas','POST',d);
  if(r.ok||r.id){toast(id?'Deuda actualizada ✅':'Deuda registrada ✅');resetDeuda();closeModal('modal-deu');cargarDeudas();}
  else toast('Error',false);
}

async function elimDeuda(id){if(!confirm('¿Eliminar?'))return;await api('/api/deudas/'+id,'DELETE');toast('Eliminada');cargarDeudas();}
cargarDeudas();
</script>"""
    return base_html(c, session["user_name"], "deudas")


# ══════════════════════════════════════════════════════════════
#  RENDIMIENTOS
# ══════════════════════════════════════════════════════════════

@app.route("/rendimientos")
@login_required
def rendimientos_page():
    c = """<div class="page">
  <div class="topbar">
    <div><div class="page-title">Rendimientos por período</div><div class="page-sub">Diario · Mensual · Anual · Acumulado real</div></div>
    <div><button class="btn btn-sm" onclick="cargar()">Actualizar</button></div>
  </div>
  <div class="kpi-grid g4">
    <div class="kpi"><div class="kpi-label">Rend. hoy (diario)</div><div class="kpi-val up" id="kr-d">$0</div></div>
    <div class="kpi"><div class="kpi-label">Rend. mensual</div><div class="kpi-val up" id="kr-m">$0</div></div>
    <div class="kpi"><div class="kpi-label">Rend. anual proyectado</div><div class="kpi-val up" id="kr-a">$0</div></div>
    <div class="kpi"><div class="kpi-label">Acumulado real</div><div class="kpi-val up" id="kr-ac">$0</div><div class="kpi-sub">Desde fecha de inicio</div></div>
  </div>
  <div class="alert alert-info"><div><b>Capitalización diaria</b> (Nu, Lulo, Nequi, Ualá): <b>Monto × ((1+EA)^(1/365) − 1)</b></div></div>
  <div class="card">
    <div class="card-header"><div><div class="card-title">Detalle por inversión</div><div class="card-sub">Ordenado por rendimiento diario</div></div></div>
    <div class="table-wrap"><table>
      <thead><tr><th>Inversión</th><th>Canal</th><th>Período</th><th class="t-right">Monto</th><th class="t-right">Tasa E.A.</th><th class="t-right">Tasa neta</th><th class="t-right" style="background:#f0fdf4">Rend. diario</th><th class="t-right">Rend. mensual</th><th class="t-right">Rend. anual</th><th class="t-right">Acumulado real</th><th class="t-right">Días activo</th><th>Vence</th></tr></thead>
      <tbody id="tabla-rend"><tr><td colspan="12"><div class="empty">Cargando…</div></td></tr></tbody>
      <tfoot id="tf-rend" style="display:none">
        <tr style="background:var(--bg);font-weight:700;border-top:1px solid var(--bs)">
          <td colspan="3"><b>TOTAL</b></td>
          <td class="mono t-right" id="tot-m"></td><td colspan="2"></td>
          <td class="t-right" id="tot-d"></td>
          <td class="mono t-right" id="tot-me"></td>
          <td class="mono t-right" id="tot-a"></td>
          <td class="mono t-right" id="tot-ac"></td>
          <td colspan="2"></td>
        </tr>
      </tfoot>
    </table></div>
  </div>
</div>
<script>
var PCLS={diario:'period-diario',mensual:'period-mensual',trimestral:'period-trimestral',semestral:'period-semestral',anual:'period-anual',vencimiento:'period-vencimiento'};
async function cargar(){
  var rows=await api('/api/rendimientos');
  var t=document.getElementById('tabla-rend');
  if(!rows.length){t.innerHTML='<tr><td colspan="12"><div class="empty"><div class="empty-icon">📊</div>Sin inversiones activas.</div></td></tr>';return;}
  rows.sort(function(a,b){return b.rend_diario-a.rend_diario;});
  var totM=0,totD=0,totMe=0,totA=0,totAc=0;
  t.innerHTML=rows.map(function(r){
    totM+=r.monto||0;totD+=r.rend_diario||0;totMe+=r.rend_mensual||0;totA+=r.rend_anual||0;totAc+=r.rend_acumulado||0;
    var dv=r.vence?Math.ceil((new Date(r.vence)-new Date())/(864e5)):null;
    var vH=r.vence?'<span class="tag '+(dv<0?'tag-red':dv<=30?'tag-amber':'tag-green')+'">'+(dv<0?'Vencido':dv+'d')+'</span>':'—';
    return '<tr><td><b>'+r.nombre+'</b><br><span style="font-size:10px;color:var(--muted)">'+r.cat+'</span></td>'
      +'<td style="color:var(--muted)">'+r.canal+'</td>'
      +'<td><span class="period-badge '+(PCLS[r.periodo]||'')+'">'+r.periodo+'</span></td>'
      +'<td class="mono t-right">'+COP(r.monto)+'</td>'
      +'<td class="mono t-right">'+Pct(r.tasa_ea)+'</td>'
      +'<td class="mono t-right" style="color:var(--blue)">'+Pct(r.tasa_neta)+'</td>'
      +'<td class="t-right"><span class="rend-daily">+'+COP(r.rend_diario)+'</span></td>'
      +'<td class="mono t-right up">+'+COP(r.rend_mensual)+'</td>'
      +'<td class="mono t-right" style="color:var(--muted)">+'+COP(r.rend_anual)+'</td>'
      +'<td class="mono t-right" style="color:var(--purple)">+'+COP(r.rend_acumulado)+'</td>'
      +'<td class="mono t-right" style="color:var(--muted)">'+r.dias_activo+'</td>'
      +'<td>'+vH+'</td></tr>';
  }).join('');
  document.getElementById('tot-m').textContent=COP(totM);
  document.getElementById('tot-d').innerHTML='<span class="rend-daily">+'+COP(totD)+'</span>';
  document.getElementById('tot-me').textContent='+'+COP(totMe);
  document.getElementById('tot-a').textContent='+'+COP(totA);
  document.getElementById('tot-ac').textContent='+'+COP(totAc);
  document.getElementById('tf-rend').style.display='';
  document.getElementById('kr-d').textContent='+'+COP(totD);
  document.getElementById('kr-m').textContent='+'+COP(totMe);
  document.getElementById('kr-a').textContent='+'+COP(totA);
  document.getElementById('kr-ac').textContent='+'+COP(totAc);
}
cargar();
</script>"""
    return base_html(c, session["user_name"], "rendimientos")

# ══════════════════════════════════════════════════════════════
#  APIs — MERCADO Y RESUMEN
# ══════════════════════════════════════════════════════════════

# ══════════════════════════════════════════════════════════════
#  API — PRECIO DE MERCADO EN TIEMPO REAL
#  Yahoo Finance para acciones/ETFs · CoinGecko para crypto
# ══════════════════════════════════════════════════════════════

CRYPTO_IDS = {
    "bitcoin":"bitcoin","btc":"bitcoin","ethereum":"ethereum","eth":"ethereum",
    "usdt":"tether","bnb":"binancecoin","sol":"solana","xrp":"ripple",
    "usdc":"usd-coin","ada":"cardano","avax":"avalanche-2","doge":"dogecoin",
    "dot":"polkadot","matic":"matic-network","link":"chainlink","ltc":"litecoin",
    "uni":"uniswap","shib":"shiba-inu","atom":"cosmos","xlm":"stellar",
}

@app.route("/api/precio_mercado")
@login_required
def api_precio_mercado():
    ticker = request.args.get("ticker","").strip()
    if not ticker:
        return jsonify({"error":"ticker requerido"}), 400

    # Detectar si es crypto
    ticker_low = ticker.lower()
    crypto_id  = CRYPTO_IDS.get(ticker_low, ticker_low if len(ticker_low) > 3 else None)

    # Intentar CoinGecko primero si parece crypto
    if ticker_low in CRYPTO_IDS or (len(ticker_low) > 3 and "/" not in ticker and "." not in ticker and ticker_low.isalpha()):
        cg_id = CRYPTO_IDS.get(ticker_low, ticker_low)
        try:
            url = f"https://api.coingecko.com/api/v3/simple/price?ids={cg_id}&vs_currencies=usd&include_24hr_change=true"
            r = requests.get(url, timeout=8, headers={"Accept":"application/json"})
            if r.ok:
                data = r.json()
                if cg_id in data:
                    precio    = data[cg_id]["usd"]
                    cambio    = data[cg_id].get("usd_24h_change")
                    return jsonify({
                        "ticker":   ticker,
                        "nombre":   cg_id.capitalize(),
                        "precio":   precio,
                        "moneda":   "USD",
                        "cambio_pct": round(cambio, 2) if cambio else None,
                        "fuente":   "CoinGecko",
                    })
        except: pass

    # Yahoo Finance para acciones y ETFs
    # Formatos: PFBCOL.CL, ECOPETROL.CL, VOO, AAPL, QQQ, etc.
    try:
        yf_ticker = ticker.upper()
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{yf_ticker}?interval=1d&range=2d"
        headers = {
            "User-Agent": "Mozilla/5.0 (compatible; FinTrackCO/1.0)",
            "Accept": "application/json",
        }
        r = requests.get(url, timeout=10, headers=headers)
        if r.ok:
            d   = r.json()
            meta = d.get("chart",{}).get("result",[{}])[0].get("meta",{})
            precio_act = meta.get("regularMarketPrice") or meta.get("previousClose")
            precio_ant = meta.get("chartPreviousClose") or meta.get("previousClose")
            nombre     = meta.get("longName") or meta.get("shortName") or yf_ticker
            moneda     = meta.get("currency","COP")
            cambio_pct = None
            if precio_act and precio_ant and precio_ant > 0:
                cambio_pct = round((precio_act - precio_ant) / precio_ant * 100, 2)
            if precio_act:
                return jsonify({
                    "ticker":     yf_ticker,
                    "nombre":     nombre,
                    "precio":     round(precio_act, 2),
                    "moneda":     moneda,
                    "cambio_pct": cambio_pct,
                    "fuente":     "Yahoo Finance",
                })
    except Exception as e:
        pass

    return jsonify({"error": f"No se encontró precio para {ticker}", "ticker": ticker}), 404

@app.route("/api/market")
@login_required
def api_market():
    d = {"usd_cop":4200,"gold_usd":2350,"tasa_br":9.50,"dtf":11.28,"ipc":5.2}
    try:
        r = requests.get("https://api.exchangerate-api.com/v4/latest/USD", timeout=5)
        if r.ok: d["usd_cop"] = round(r.json()["rates"].get("COP",4200))
    except: pass
    try:
        r2 = requests.get("https://api.exchangerate-api.com/v4/latest/XAU", timeout=5)
        if r2.ok: d["gold_usd"] = round(r2.json()["rates"].get("USD",2350))
    except: pass
    return jsonify(d)

@app.route("/api/resumen")
@login_required
def api_resumen():
    trm = float(request.args.get("trm", 4200)); u = uid()
    with get_db() as db:
        ing    = db.execute("SELECT COALESCE(SUM(monto),0) s FROM ingresos WHERE usuario=?", (u,)).fetchone()["s"]
        gas    = db.execute("SELECT COALESCE(SUM(monto),0) s FROM gastos WHERE usuario=?", (u,)).fetchone()["s"]
        gas_f  = db.execute("SELECT COALESCE(SUM(monto),0) s FROM gastos WHERE usuario=? AND tipo='fijo'", (u,)).fetchone()["s"]
        ah     = db.execute("SELECT COALESCE(SUM(actual),0) s FROM metas WHERE usuario=?", (u,)).fetchone()["s"]
        ah_m   = db.execute("SELECT COALESCE(SUM(mensual),0) s FROM metas WHERE usuario=?", (u,)).fetchone()["s"]
        deu    = db.execute("SELECT COALESCE(SUM(saldo_actual),0) s FROM deudas WHERE usuario=? AND estado='activa'", (u,)).fetchone()["s"]
        deu_c  = db.execute("SELECT COALESCE(SUM(cuota),0) s FROM deudas WHERE usuario=? AND estado='activa'", (u,)).fetchone()["s"]
        rf_r   = db.execute("SELECT * FROM renta_fija WHERE usuario=? AND estado='activo'", (u,)).fetchall()
        rv_r   = db.execute("SELECT * FROM renta_variable WHERE usuario=?", (u,)).fetchall()
        inmo_r = db.execute("SELECT * FROM inmobiliario WHERE usuario=?", (u,)).fetchall()
        usd_r  = db.execute("SELECT * FROM dolares WHERE usuario=?", (u,)).fetchall()
    rf_tot  = sum(r["monto"] for r in rf_r)
    rv_val  = sum(r["cantidad"]*(r["precio_act"] or r["precio_comp"]) for r in rv_r)
    inmo_v  = sum(r["actual"] or r["compra"] for r in inmo_r)
    usd_cop = sum(r["cant_usd"]*trm for r in usd_r)
    port    = rf_tot + rv_val + inmo_v + usd_cop
    rend_d  = sum(calc_rend(r["monto"],r["tasa_ea"],r["com_ea"],r["periodo"],r["ini"])["rend_diario"]   for r in rf_r)
    rend_m  = sum(calc_rend(r["monto"],r["tasa_ea"],r["com_ea"],r["periodo"],r["ini"])["rend_mensual"]  for r in rf_r)
    rend_a  = sum(calc_rend(r["monto"],r["tasa_ea"],r["com_ea"],r["periodo"],r["ini"])["rend_anual"]    for r in rf_r)
    rend_ac = sum(calc_rend(r["monto"],r["tasa_ea"],r["com_ea"],r["periodo"],r["ini"])["rend_acumulado"]for r in rf_r)
    alertas = []
    if ing>0 and gas/ing>0.8: alertas.append({"tipo":"critical","msg":f"Gastos al {gas/ing*100:.1f}% del ingreso."})
    elif ing>0 and gas/ing>0.6: alertas.append({"tipo":"warning","msg":f"Gastos al {gas/ing*100:.1f}% del ingreso."})
    if gas_f>0 and ah<gas_f*3: alertas.append({"tipo":"warning","msg":f"Fondo emergencia: {ah/gas_f:.1f} meses. Recomendado: 3."})
    if port>0 and deu/port>0.5: alertas.append({"tipo":"critical","msg":f"Ratio deuda/activos: {deu/port*100:.0f}%. Recomendado < 40%."})
    with get_db() as db:
        cdts = db.execute("SELECT tipo,monto,vence FROM renta_fija WHERE usuario=? AND estado='activo' AND vence IS NOT NULL", (u,)).fetchall()
    for cdt in cdts:
        try:
            vd = datetime.strptime(cdt["vence"][:10],"%Y-%m-%d").date()
            dias = (vd - date.today()).days
            if 0<=dias<=30: alertas.append({"tipo":"warning","msg":f"{cdt['tipo']} vence en {dias} días."})
            elif dias<0: alertas.append({"tipo":"critical","msg":f"{cdt['tipo']} venció el {cdt['vence']}."})
        except: pass
    return jsonify({
        "ingresos":round(ing,0),"gastos":round(gas,0),"ahorro":round(ah,0),
        "ahorro_mensual":round(ah_m,0),"portafolio":round(port,0),
        "deudas":round(deu,0),"deudas_cuota":round(deu_c,0),"neto":round(port-deu,0),
        "rendimientos":{"total_diario":round(rend_d,2),"total_mensual":round(rend_m,0),
            "total_anual":round(rend_a,0),"rf_acumulado":round(rend_ac,0)},
        "alertas":alertas,
    })


# ══════════════════════════════════════════════════════════════
#  APIs — RENTA FIJA
# ══════════════════════════════════════════════════════════════

def enrich_rf(row):
    r = dict(row)
    c = calc_rend(r["monto"],r["tasa_ea"],r["com_ea"],r["periodo"],r.get("ini"),r.get("vence"))
    r.update(c)
    if r.get("vence"):
        try:
            vd = datetime.strptime(r["vence"][:10],"%Y-%m-%d").date()
            r["dias_para_vencer"] = (vd - date.today()).days
        except: r["dias_para_vencer"] = None
    return r

@app.route("/api/renta_fija", methods=["GET"])
@login_required
def api_rf_list():
    with get_db() as db:
        rows = db.execute("SELECT * FROM renta_fija WHERE usuario=? ORDER BY creado DESC",(uid(),)).fetchall()
    return jsonify([enrich_rf(r) for r in rows])

@app.route("/api/renta_fija", methods=["POST"])
@login_required
def api_rf_add():
    d = request.get_json() or {}
    ta=float(d.get("tasa_ea",0)); co=float(d.get("com_ea",0))
    with get_db() as db:
        c = db.execute("INSERT INTO renta_fija (usuario,tipo,canal,monto,tasa_ea,com_ea,tasa_neta,periodo,ini,vence,estado,notas) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (uid(),d.get("tipo"),d.get("canal"),d.get("monto",0),ta,co,max(0,ta-co),
             d.get("periodo","mensual"),d.get("ini"),d.get("vence"),d.get("estado","activo"),d.get("notas")))
    return jsonify({"id":c.lastrowid,"ok":True}), 201

@app.route("/api/renta_fija/<int:rid>", methods=["GET"])
@login_required
def api_rf_get(rid):
    with get_db() as db:
        r = db.execute("SELECT * FROM renta_fija WHERE id=? AND usuario=?",(rid,uid())).fetchone()
    return jsonify(enrich_rf(r)) if r else (jsonify({"error":"no encontrado"}),404)

@app.route("/api/renta_fija/<int:rid>", methods=["PUT"])
@login_required
def api_rf_edit(rid):
    d = request.get_json() or {}
    ta=float(d.get("tasa_ea",0)); co=float(d.get("com_ea",0))
    with get_db() as db:
        db.execute("UPDATE renta_fija SET tipo=?,canal=?,monto=?,tasa_ea=?,com_ea=?,tasa_neta=?,periodo=?,ini=?,vence=?,estado=?,notas=? WHERE id=? AND usuario=?",
            (d.get("tipo"),d.get("canal"),d.get("monto",0),ta,co,max(0,ta-co),
             d.get("periodo"),d.get("ini"),d.get("vence"),d.get("estado"),d.get("notas"),rid,uid()))
    return jsonify({"ok":True})

@app.route("/api/renta_fija/<int:rid>", methods=["DELETE"])
@login_required
def api_rf_del(rid):
    db_del("renta_fija", rid)
    return jsonify({"ok":True})

# ══════════════════════════════════════════════════════════════
#  APIs — METAS
# ══════════════════════════════════════════════════════════════

def enrich_meta(row):
    r = dict(row)
    obj = float(r.get("objetivo") or 0)
    act = float(r.get("actual") or 0)
    r["pct"] = round(act/obj*100, 1) if obj > 0 else 0
    r["faltante"] = round(max(0, obj - act), 0)
    return r

@app.route("/api/metas", methods=["GET"])
@login_required
def api_metas_list():
    tipo  = request.args.get("tipo")
    estado= request.args.get("estado")
    q = "SELECT * FROM metas WHERE usuario=?"; p = [uid()]
    if tipo:   q += " AND tipo_meta=?"; p.append(tipo)
    if estado: q += " AND estado=?";    p.append(estado)
    q += " ORDER BY creado DESC"
    with get_db() as db: rows = db.execute(q,p).fetchall()
    return jsonify([enrich_meta(r) for r in rows])

@app.route("/api/metas", methods=["POST"])
@login_required
def api_metas_add():
    d = request.get_json() or {}
    with get_db() as db:
        c = db.execute("INSERT INTO metas (usuario,nombre,tipo_meta,objetivo,actual,mensual,fecha,tipo_cta,estado,notas) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (uid(),d.get("nombre"),d.get("tipo_meta","ahorro"),d.get("objetivo",0),d.get("actual",0),
             d.get("mensual",0),d.get("fecha"),d.get("tipo_cta"),d.get("estado","activa"),d.get("notas")))
    return jsonify({"id":c.lastrowid,"ok":True}), 201

@app.route("/api/metas/<int:rid>", methods=["GET"])
@login_required
def api_meta_get(rid):
    with get_db() as db:
        r = db.execute("SELECT * FROM metas WHERE id=? AND usuario=?",(rid,uid())).fetchone()
    return jsonify(enrich_meta(r)) if r else (jsonify({"error":"no encontrado"}),404)

@app.route("/api/metas/<int:rid>", methods=["PUT"])
@login_required
def api_meta_edit(rid):
    d = request.get_json() or {}
    with get_db() as db:
        # Si solo se envía estado (marcar cumplida), update parcial
        if list(d.keys()) == ["estado"]:
            db.execute("UPDATE metas SET estado=? WHERE id=? AND usuario=?",(d["estado"],rid,uid()))
        else:
            db.execute("UPDATE metas SET nombre=?,tipo_meta=?,objetivo=?,actual=?,mensual=?,fecha=?,tipo_cta=?,estado=?,notas=? WHERE id=? AND usuario=?",
                (d.get("nombre"),d.get("tipo_meta","ahorro"),d.get("objetivo",0),d.get("actual",0),
                 d.get("mensual",0),d.get("fecha"),d.get("tipo_cta"),d.get("estado","activa"),d.get("notas"),rid,uid()))
    return jsonify({"ok":True})

@app.route("/api/metas/<int:rid>", methods=["DELETE"])
@login_required
def api_meta_del(rid):
    db_del("metas", rid)
    return jsonify({"ok":True})

# ══════════════════════════════════════════════════════════════
#  APIs — DEUDAS (con cálculo de ahorro acumulado)
# ══════════════════════════════════════════════════════════════

def enrich_deuda(row):
    r = dict(row)
    metricas = calc_deuda(
        r.get("saldo_inicial"), r.get("saldo_actual"), r.get("cuota"),
        r.get("tasa_ea"), r.get("cuotas_total"), r.get("cuotas_pagadas"),
        r.get("fecha_inicio"), r.get("fecha_pago")
    )
    r.update(metricas)
    return r

def get_ahorro_acumulado(deu_id):
    """Suma total de ahorros en intereses registrados para esta deuda"""
    with get_db() as db:
        row = db.execute(
            "SELECT COALESCE(SUM(ahorro_interes),0) s FROM movimientos WHERE usuario=? AND cat='deudas' AND inv_id=?",
            (uid(), deu_id)
        ).fetchone()
    return float(row["s"] or 0)

@app.route("/api/deudas", methods=["GET"])
@login_required
def api_deudas_list():
    with get_db() as db:
        rows = db.execute("SELECT * FROM deudas WHERE usuario=? ORDER BY prior DESC, tasa_ea DESC",(uid(),)).fetchall()
    result = []
    for r in rows:
        d = enrich_deuda(r)
        d["ahorro_acumulado"] = get_ahorro_acumulado(r["id"])
        result.append(d)
    return jsonify(result)

@app.route("/api/deudas", methods=["POST"])
@login_required
def api_deudas_add():
    d = request.get_json() or {}
    with get_db() as db:
        c = db.execute("""INSERT INTO deudas
            (usuario,tipo,entidad,fecha_inicio,fecha_pago,saldo_inicial,saldo_actual,
             cuota,cuotas_total,cuotas_pagadas,tasa_ea,prior,estado,notas)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (uid(),d.get("tipo"),d.get("entidad"),d.get("fecha_inicio"),d.get("fecha_pago"),
             d.get("saldo_inicial",0),d.get("saldo_actual",d.get("saldo_inicial",0)),
             d.get("cuota",0),d.get("cuotas_total",0),d.get("cuotas_pagadas",0),
             d.get("tasa_ea",0),d.get("prior","media"),d.get("estado","activa"),d.get("notas")))
    return jsonify({"id":c.lastrowid,"ok":True}), 201

@app.route("/api/deudas/<int:rid>", methods=["GET"])
@login_required
def api_deuda_get(rid):
    with get_db() as db:
        r = db.execute("SELECT * FROM deudas WHERE id=? AND usuario=?",(rid,uid())).fetchone()
    if not r: return jsonify({"error":"no encontrado"}),404
    d = enrich_deuda(r); d["ahorro_acumulado"] = get_ahorro_acumulado(rid)
    return jsonify(d)

@app.route("/api/deudas/<int:rid>", methods=["PUT"])
@login_required
def api_deuda_edit(rid):
    d = request.get_json() or {}
    with get_db() as db:
        db.execute("""UPDATE deudas SET tipo=?,entidad=?,fecha_inicio=?,fecha_pago=?,
            saldo_inicial=?,saldo_actual=?,cuota=?,cuotas_total=?,cuotas_pagadas=?,
            tasa_ea=?,prior=?,estado=?,notas=? WHERE id=? AND usuario=?""",
            (d.get("tipo"),d.get("entidad"),d.get("fecha_inicio"),d.get("fecha_pago"),
             d.get("saldo_inicial",0),d.get("saldo_actual",0),
             d.get("cuota",0),d.get("cuotas_total",0),d.get("cuotas_pagadas",0),
             d.get("tasa_ea",0),d.get("prior","media"),d.get("estado","activa"),d.get("notas"),rid,uid()))
    return jsonify({"ok":True})

@app.route("/api/deudas/<int:rid>", methods=["DELETE"])
@login_required
def api_deuda_del(rid):
    db_del("deudas", rid)
    return jsonify({"ok":True})

# ══════════════════════════════════════════════════════════════
#  API — MOVIMIENTOS (registro central, actualiza saldos)
# ══════════════════════════════════════════════════════════════

@app.route("/api/movimientos", methods=["GET"])
@login_required
def api_movs_list():
    cat = request.args.get("cat"); inv_id = request.args.get("inv_id")
    q = "SELECT * FROM movimientos WHERE usuario=?"; p = [uid()]
    if cat:    q += " AND cat=?";    p.append(cat)
    if inv_id: q += " AND inv_id=?"; p.append(inv_id)
    q += " ORDER BY fecha DESC, creado DESC"
    with get_db() as db: rows = db.execute(q,p).fetchall()
    return jsonify([dict(r) for r in rows])

@app.route("/api/movimientos", methods=["POST"])
@login_required
def api_movs_add():
    d    = request.get_json() or {}
    cat  = d.get("cat"); inv_id = d.get("inv_id"); tipo = d.get("tipo")
    monto= float(d.get("monto",0))
    ahorro= float(d.get("ahorro_interes",0))
    cuotas_menos = int(d.get("cuotas_menos",0))
    with get_db() as db:
        # ─── Actualizar saldos según el tipo de movimiento ───
        if cat == "renta_fija":
            if tipo == "aporte":
                db.execute("UPDATE renta_fija SET monto=monto+? WHERE id=? AND usuario=?",(monto,inv_id,uid()))
            elif tipo == "retiro":
                db.execute("UPDATE renta_fija SET monto=MAX(0,monto-?) WHERE id=? AND usuario=?",(monto,inv_id,uid()))
            elif tipo == "liquidacion":
                db.execute("UPDATE renta_fija SET monto=0,estado='vencido' WHERE id=? AND usuario=?",(inv_id,uid()))
            elif tipo == "renovacion" and d.get("nueva_tasa"):
                db.execute("UPDATE renta_fija SET tasa_ea=?,tasa_neta=? WHERE id=? AND usuario=?",
                    (d["nueva_tasa"],d["nueva_tasa"]-float(d.get("nueva_com",0)),inv_id,uid()))

        elif cat == "renta_variable":
            if tipo == "compra":
                # Promedio ponderado
                row = db.execute("SELECT cantidad,precio_comp FROM renta_variable WHERE id=? AND usuario=?",(inv_id,uid())).fetchone()
                if row:
                    q_old = float(row["cantidad"] or 0); p_old = float(row["precio_comp"] or 0)
                    q_new = float(d.get("cantidad",0)); p_new = float(d.get("precio",0) or monto/q_new if q_new else 0)
                    q_tot = q_old + q_new
                    p_avg = ((q_old*p_old)+(q_new*p_new))/q_tot if q_tot > 0 else p_new
                    db.execute("UPDATE renta_variable SET cantidad=?,precio_comp=?,precio_act=? WHERE id=? AND usuario=?",
                        (q_tot,round(p_avg,2),d.get("precio",p_new),inv_id,uid()))
            elif tipo == "venta":
                q_sell = float(d.get("cantidad",0))
                db.execute("UPDATE renta_variable SET cantidad=MAX(0,cantidad-?) WHERE id=? AND usuario=?",(q_sell,inv_id,uid()))
            elif tipo == "actualizacion":
                if d.get("precio"): db.execute("UPDATE renta_variable SET precio_act=? WHERE id=? AND usuario=?",(d["precio"],inv_id,uid()))
            elif tipo == "liquidacion":
                db.execute("UPDATE renta_variable SET cantidad=0 WHERE id=? AND usuario=?",(inv_id,uid()))

        elif cat == "deudas":
            row = db.execute("SELECT * FROM deudas WHERE id=? AND usuario=?",(inv_id,uid())).fetchone()
            if row:
                sa = float(row["saldo_actual"] or 0)
                tm = math.pow(1+float(row["tasa_ea"] or 0)/100, 1/12) - 1 if row["tasa_ea"] else 0
                int_mes = sa * tm  # interés del mes

                if tipo == "pago_cuota":
                    # Cuota = interés + amortización
                    amort = max(0, monto - int_mes)
                    nuevo_saldo = max(0, sa - amort)
                    db.execute("UPDATE deudas SET saldo_actual=?,cuotas_pagadas=cuotas_pagadas+1 WHERE id=? AND usuario=?",
                        (round(nuevo_saldo,0),inv_id,uid()))
                elif tipo == "abono_capital":
                    nuevo_saldo = max(0, sa - monto)
                    db.execute("UPDATE deudas SET saldo_actual=? WHERE id=? AND usuario=?",
                        (round(nuevo_saldo,0),inv_id,uid()))
                elif tipo == "ajuste_saldo":
                    db.execute("UPDATE deudas SET saldo_actual=? WHERE id=? AND usuario=?",
                        (monto,inv_id,uid()))
                elif tipo == "cambio_condiciones":
                    updates = []
                    if d.get("nueva_tasa"): updates.append(("tasa_ea",float(d["nueva_tasa"])))
                    if d.get("nueva_cuota"): updates.append(("cuota",float(d["nueva_cuota"])))
                    for col, val in updates:
                        db.execute(f"UPDATE deudas SET {col}=? WHERE id=? AND usuario=?",(val,inv_id,uid()))
                elif tipo == "liquidacion":
                    db.execute("UPDATE deudas SET saldo_actual=0,estado='liquidada' WHERE id=? AND usuario=?",(inv_id,uid()))

        elif cat == "metas":
            row = db.execute("SELECT actual,objetivo FROM metas WHERE id=? AND usuario=?",(inv_id,uid())).fetchone()
            if row:
                act = float(row["actual"] or 0)
                obj = float(row["objetivo"] or 0)
                if tipo == "deposito_meta":
                    nuevo = act + monto
                    db.execute("UPDATE metas SET actual=? WHERE id=? AND usuario=?",(min(nuevo,obj),inv_id,uid()))
                elif tipo == "retiro_meta":
                    db.execute("UPDATE metas SET actual=MAX(0,actual-?) WHERE id=? AND usuario=?",(monto,inv_id,uid()))
                elif tipo == "ajuste_meta":
                    db.execute("UPDATE metas SET actual=? WHERE id=? AND usuario=?",(monto,inv_id,uid()))

        # Insertar el movimiento en el registro central
        c = db.execute("""INSERT INTO movimientos
            (usuario,cat,inv_id,tipo,monto,precio,fecha,ctx,trm,ahorro_interes,cuotas_menos)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (uid(),cat,inv_id,tipo,monto,d.get("precio"),d.get("fecha",today()),
             d.get("ctx"),d.get("trm",4200),ahorro,cuotas_menos))
    return jsonify({"id":c.lastrowid,"ok":True}), 201

# ══════════════════════════════════════════════════════════════
#  API — RENDIMIENTOS DETALLADOS
# ══════════════════════════════════════════════════════════════

@app.route("/api/rendimientos")
@login_required
def api_rendimientos():
    with get_db() as db:
        rf_r   = db.execute("SELECT * FROM renta_fija WHERE usuario=? AND estado='activo'",(uid(),)).fetchall()
        inmo_r = db.execute("SELECT * FROM inmobiliario WHERE usuario=?",(uid(),)).fetchall()
    res = []
    for r in rf_r:
        c = calc_rend(r["monto"],r["tasa_ea"],r["com_ea"],r["periodo"],r["ini"],r["vence"])
        res.append({"cat":"Renta Fija","id":r["id"],"nombre":r["tipo"],"canal":r["canal"],
            "monto":r["monto"],"tasa_ea":r["tasa_ea"],"tasa_neta":c["tasa_neta"],
            "periodo":r["periodo"],"rend_diario":c["rend_diario"],"rend_mensual":c["rend_mensual"],
            "rend_anual":c["rend_anual"],"rend_periodo":c["rend_periodo"],
            "rend_acumulado":c["rend_acumulado"],"dias_activo":c["dias_activo"],"vence":r["vence"]})
    for r in inmo_r:
        canon = float(r["canon"] or 0)
        res.append({"cat":"Inmobiliario","id":r["id"],"nombre":r["nombre"] or r["tipo"],"canal":r["canal"],
            "monto":r["compra"],"tasa_ea":r["tasa_ea"],"tasa_neta":r["tasa_ea"],
            "periodo":r["periodo"],"rend_diario":round(canon/30,2),"rend_mensual":canon,
            "rend_anual":canon*12,"rend_periodo":canon,"rend_acumulado":0,"dias_activo":0,"vence":None})
    return jsonify(res)


# ══════════════════════════════════════════════════════════════
#  APIs — INGRESOS
# ══════════════════════════════════════════════════════════════

@app.route("/api/ingresos", methods=["GET"])
@login_required
def api_ingresos_list():
    with get_db() as db:
        rows = db.execute("SELECT * FROM ingresos WHERE usuario=? ORDER BY fecha DESC, creado DESC",(uid(),)).fetchall()
    return jsonify([dict(r) for r in rows])

@app.route("/api/ingresos", methods=["POST"])
@login_required
def api_ingresos_add():
    d = request.get_json() or {}
    with get_db() as db:
        c = db.execute(
            "INSERT INTO ingresos (usuario,cat,desc,monto,fecha,period,fecha_fin,notas) VALUES (?,?,?,?,?,?,?,?)",
            (uid(), d.get("cat"), d.get("desc"), float(d.get("monto",0)),
             d.get("fecha", today()), d.get("period","mensual"),
             d.get("fecha_fin"), d.get("notas")))
    return jsonify({"id": c.lastrowid, "ok": True}), 201

@app.route("/api/ingresos/<int:rid>", methods=["GET"])
@login_required
def api_ingreso_get(rid):
    with get_db() as db:
        r = db.execute("SELECT * FROM ingresos WHERE id=? AND usuario=?",(rid,uid())).fetchone()
    return jsonify(dict(r)) if r else (jsonify({"error":"no encontrado"}),404)

@app.route("/api/ingresos/<int:rid>", methods=["PUT"])
@login_required
def api_ingreso_edit(rid):
    d = request.get_json() or {}
    with get_db() as db:
        db.execute(
            "UPDATE ingresos SET cat=?,desc=?,monto=?,fecha=?,period=?,fecha_fin=?,notas=? WHERE id=? AND usuario=?",
            (d.get("cat"), d.get("desc"), float(d.get("monto",0)),
             d.get("fecha"), d.get("period","mensual"),
             d.get("fecha_fin"), d.get("notas"), rid, uid()))
    return jsonify({"ok": True})

@app.route("/api/ingresos/<int:rid>", methods=["DELETE"])
@login_required
def api_ingreso_del(rid):
    db_del("ingresos", rid)
    return jsonify({"ok": True})

# ══════════════════════════════════════════════════════════════
#  APIs — GASTOS
# ══════════════════════════════════════════════════════════════

@app.route("/api/gastos", methods=["GET"])
@login_required
def api_gastos_list():
    with get_db() as db:
        rows = db.execute("SELECT * FROM gastos WHERE usuario=? ORDER BY fecha DESC, creado DESC",(uid(),)).fetchall()
    return jsonify([dict(r) for r in rows])

@app.route("/api/gastos", methods=["POST"])
@login_required
def api_gastos_add():
    d = request.get_json() or {}
    with get_db() as db:
        c = db.execute(
            "INSERT INTO gastos (usuario,cat,desc,monto,fecha,tipo,presup,notas) VALUES (?,?,?,?,?,?,?,?)",
            (uid(), d.get("cat"), d.get("desc"), float(d.get("monto",0)),
             d.get("fecha", today()), d.get("tipo","variable"),
             float(d.get("presup",0)), d.get("notas")))
    return jsonify({"id": c.lastrowid, "ok": True}), 201

@app.route("/api/gastos/<int:rid>", methods=["GET"])
@login_required
def api_gasto_get(rid):
    with get_db() as db:
        r = db.execute("SELECT * FROM gastos WHERE id=? AND usuario=?",(rid,uid())).fetchone()
    return jsonify(dict(r)) if r else (jsonify({"error":"no encontrado"}),404)

@app.route("/api/gastos/<int:rid>", methods=["PUT"])
@login_required
def api_gasto_edit(rid):
    d = request.get_json() or {}
    with get_db() as db:
        db.execute(
            "UPDATE gastos SET cat=?,desc=?,monto=?,fecha=?,tipo=?,presup=?,notas=? WHERE id=? AND usuario=?",
            (d.get("cat"), d.get("desc"), float(d.get("monto",0)),
             d.get("fecha"), d.get("tipo","variable"),
             float(d.get("presup",0)), d.get("notas"), rid, uid()))
    return jsonify({"ok": True})

@app.route("/api/gastos/<int:rid>", methods=["DELETE"])
@login_required
def api_gasto_del(rid):
    db_del("gastos", rid)
    return jsonify({"ok": True})

# ══════════════════════════════════════════════════════════════
#  APIs — RENTA VARIABLE
# ══════════════════════════════════════════════════════════════

def enrich_rv(row):
    r = dict(row)
    cant  = float(r.get("cantidad") or 0)
    pcomp = float(r.get("precio_comp") or 0)
    pact  = float(r.get("precio_act") or pcomp)
    r["costo_total"]  = round(cant * pcomp, 0)
    r["valor_actual"] = round(cant * pact, 0)
    r["ganancia"]     = round(r["valor_actual"] - r["costo_total"], 0)
    r["retorno_pct"]  = round(r["ganancia"] / r["costo_total"] * 100, 2) if r["costo_total"] > 0 else 0
    return r

@app.route("/api/renta_variable", methods=["GET"])
@login_required
def api_rv_list():
    with get_db() as db:
        rows = db.execute("SELECT * FROM renta_variable WHERE usuario=? ORDER BY creado DESC",(uid(),)).fetchall()
    return jsonify([enrich_rv(r) for r in rows])

@app.route("/api/renta_variable", methods=["POST"])
@login_required
def api_rv_add():
    d = request.get_json() or {}
    with get_db() as db:
        c = db.execute(
            "INSERT INTO renta_variable (usuario,tipo,ticker,canal,cantidad,precio_comp,precio_act,com_pct,fecha,riesgo,tesis) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (uid(), d.get("tipo"), d.get("ticker"), d.get("canal"),
             float(d.get("cantidad",0)), float(d.get("precio_comp",0)),
             float(d.get("precio_act") or d.get("precio_comp",0)),
             float(d.get("com_pct",0)), d.get("fecha", today()),
             d.get("riesgo","moderado"), d.get("tesis")))
    return jsonify({"id": c.lastrowid, "ok": True}), 201

@app.route("/api/renta_variable/<int:rid>", methods=["GET"])
@login_required
def api_rv_get(rid):
    with get_db() as db:
        r = db.execute("SELECT * FROM renta_variable WHERE id=? AND usuario=?",(rid,uid())).fetchone()
    return jsonify(enrich_rv(r)) if r else (jsonify({"error":"no encontrado"}),404)

@app.route("/api/renta_variable/<int:rid>", methods=["PUT"])
@login_required
def api_rv_edit(rid):
    d = request.get_json() or {}
    with get_db() as db:
        db.execute(
            "UPDATE renta_variable SET tipo=?,ticker=?,canal=?,cantidad=?,precio_comp=?,precio_act=?,com_pct=?,fecha=?,riesgo=?,tesis=? WHERE id=? AND usuario=?",
            (d.get("tipo"), d.get("ticker"), d.get("canal"),
             float(d.get("cantidad",0)), float(d.get("precio_comp",0)),
             float(d.get("precio_act") or d.get("precio_comp",0)),
             float(d.get("com_pct",0)), d.get("fecha"), d.get("riesgo","moderado"),
             d.get("tesis"), rid, uid()))
    return jsonify({"ok": True})

@app.route("/api/renta_variable/<int:rid>", methods=["DELETE"])
@login_required
def api_rv_del(rid):
    db_del("renta_variable", rid)
    return jsonify({"ok": True})

# ══════════════════════════════════════════════════════════════
#  APIs — INMOBILIARIO
# ══════════════════════════════════════════════════════════════

def enrich_inmo(row):
    r = dict(row)
    r["valorizacion"] = round(float(r.get("actual") or 0) - float(r.get("compra") or 0), 0)
    r["renta_anual"]  = round(float(r.get("canon") or 0) * 12, 0)
    return r

@app.route("/api/inmobiliario", methods=["GET"])
@login_required
def api_inmo_list():
    with get_db() as db:
        rows = db.execute("SELECT * FROM inmobiliario WHERE usuario=? ORDER BY creado DESC",(uid(),)).fetchall()
    return jsonify([enrich_inmo(r) for r in rows])

@app.route("/api/inmobiliario", methods=["POST"])
@login_required
def api_inmo_add():
    d = request.get_json() or {}
    with get_db() as db:
        c = db.execute(
            "INSERT INTO inmobiliario (usuario,tipo,nombre,canal,compra,actual,canon,tasa_ea,com_ea,periodo,fecha,notas) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (uid(), d.get("tipo"), d.get("nombre"), d.get("canal"),
             float(d.get("compra",0)), float(d.get("actual") or d.get("compra",0)),
             float(d.get("canon",0)), float(d.get("tasa_ea",0)),
             float(d.get("com_ea",0)), d.get("periodo","mensual"),
             d.get("fecha", today()), d.get("notas")))
    return jsonify({"id": c.lastrowid, "ok": True}), 201

@app.route("/api/inmobiliario/<int:rid>", methods=["GET"])
@login_required
def api_inmo_get(rid):
    with get_db() as db:
        r = db.execute("SELECT * FROM inmobiliario WHERE id=? AND usuario=?",(rid,uid())).fetchone()
    return jsonify(enrich_inmo(r)) if r else (jsonify({"error":"no encontrado"}),404)

@app.route("/api/inmobiliario/<int:rid>", methods=["PUT"])
@login_required
def api_inmo_edit(rid):
    d = request.get_json() or {}
    with get_db() as db:
        db.execute(
            "UPDATE inmobiliario SET tipo=?,nombre=?,canal=?,compra=?,actual=?,canon=?,tasa_ea=?,com_ea=?,periodo=?,fecha=?,notas=? WHERE id=? AND usuario=?",
            (d.get("tipo"), d.get("nombre"), d.get("canal"),
             float(d.get("compra",0)), float(d.get("actual") or d.get("compra",0)),
             float(d.get("canon",0)), float(d.get("tasa_ea",0)),
             float(d.get("com_ea",0)), d.get("periodo","mensual"),
             d.get("fecha"), d.get("notas"), rid, uid()))
    return jsonify({"ok": True})

@app.route("/api/inmobiliario/<int:rid>", methods=["DELETE"])
@login_required
def api_inmo_del(rid):
    db_del("inmobiliario", rid)
    return jsonify({"ok": True})

# ══════════════════════════════════════════════════════════════
#  APIs — DÓLARES
# ══════════════════════════════════════════════════════════════

def enrich_usd(row, trm=4200):
    r = dict(row)
    cant = float(r.get("cant_usd") or 0)
    trm_c = float(r.get("trm_compra") or trm)
    r["cop_compra"]    = round(cant * trm_c, 0)
    r["cop_actual"]    = round(cant * trm, 0)
    r["gp_cambiaria"]  = round(r["cop_actual"] - r["cop_compra"], 0)
    return r

@app.route("/api/dolares", methods=["GET"])
@login_required
def api_usd_list():
    trm = float(request.args.get("trm", 4200))
    with get_db() as db:
        rows = db.execute("SELECT * FROM dolares WHERE usuario=? ORDER BY creado DESC",(uid(),)).fetchall()
    return jsonify([enrich_usd(r, trm) for r in rows])

@app.route("/api/dolares", methods=["POST"])
@login_required
def api_usd_add():
    d = request.get_json() or {}
    with get_db() as db:
        c = db.execute(
            "INSERT INTO dolares (usuario,tipo,nombre,canal,cant_usd,trm_compra,rend_usd,fecha,notas) VALUES (?,?,?,?,?,?,?,?,?)",
            (uid(), d.get("tipo"), d.get("nombre"), d.get("canal"),
             float(d.get("cant_usd",0)), float(d.get("trm_compra",4200)),
             float(d.get("rend_usd",0)), d.get("fecha", today()), d.get("notas")))
    return jsonify({"id": c.lastrowid, "ok": True}), 201

@app.route("/api/dolares/<int:rid>", methods=["GET"])
@login_required
def api_usd_get(rid):
    with get_db() as db:
        r = db.execute("SELECT * FROM dolares WHERE id=? AND usuario=?",(rid,uid())).fetchone()
    return jsonify(enrich_usd(r)) if r else (jsonify({"error":"no encontrado"}),404)

@app.route("/api/dolares/<int:rid>", methods=["PUT"])
@login_required
def api_usd_edit(rid):
    d = request.get_json() or {}
    with get_db() as db:
        db.execute(
            "UPDATE dolares SET tipo=?,nombre=?,canal=?,cant_usd=?,trm_compra=?,rend_usd=?,fecha=?,notas=? WHERE id=? AND usuario=?",
            (d.get("tipo"), d.get("nombre"), d.get("canal"),
             float(d.get("cant_usd",0)), float(d.get("trm_compra",4200)),
             float(d.get("rend_usd",0)), d.get("fecha"), d.get("notas"),
             rid, uid()))
    return jsonify({"ok": True})

@app.route("/api/dolares/<int:rid>", methods=["DELETE"])
@login_required
def api_usd_del(rid):
    db_del("dolares", rid)
    return jsonify({"ok": True})

# ══════════════════════════════════════════════════════════════
#  API — MOVIMIENTOS DELETE
# ══════════════════════════════════════════════════════════════

@app.route("/api/movimientos/<int:rid>", methods=["DELETE"])
@login_required
def api_mov_del(rid):
    db_del("movimientos", rid)
    return jsonify({"ok": True})

@app.route("/health")
def health():
    return jsonify({"status":"ok","app":"FinTrack CO v4","time":datetime.now().isoformat()})

# ══════════════════════════════════════════════════════════════
#  INIT
# ══════════════════════════════════════════════════════════════

init_db()

if __name__ == "__main__":
    port  = int(os.environ.get("PORT", 5000))
    debug = os.environ.get("FLASK_DEBUG","true").lower() == "true"
    print(f"\n  FinTrack CO v4  →  http://localhost:{port}\n")
    app.run(host="0.0.0.0", port=port, debug=debug)
