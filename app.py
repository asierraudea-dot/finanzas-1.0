"""
FinTrack CO v4 — Fase 1
Flask + SQLite + Login + CRUD + Rendimientos diarios/mensuales/anuales
Soporta: cuentas remuneradas (diario), CDT, fiducias, fondos inmobiliarios
"""

from flask import (Flask, render_template, request, redirect,
                   url_for, session, jsonify, flash)
from functools import wraps
import sqlite3, hashlib, os, secrets, requests, math
from datetime import datetime, date

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", secrets.token_hex(32))
DB = os.path.join(os.path.dirname(__file__), "fintrack.db")

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
            id       INTEGER PRIMARY KEY AUTOINCREMENT,
            nombre   TEXT NOT NULL,
            email    TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            creado   TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS ingresos (
            id      INTEGER PRIMARY KEY AUTOINCREMENT,
            usuario INTEGER NOT NULL,
            cat     TEXT,
            desc    TEXT,
            monto   REAL DEFAULT 0,
            fecha   TEXT,
            period  TEXT DEFAULT 'mensual',
            notas   TEXT,
            creado  TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (usuario) REFERENCES usuarios(id)
        );

        CREATE TABLE IF NOT EXISTS gastos (
            id      INTEGER PRIMARY KEY AUTOINCREMENT,
            usuario INTEGER NOT NULL,
            cat     TEXT,
            desc    TEXT,
            monto   REAL DEFAULT 0,
            fecha   TEXT,
            tipo    TEXT DEFAULT 'variable',
            presup  REAL DEFAULT 0,
            notas   TEXT,
            creado  TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (usuario) REFERENCES usuarios(id)
        );

        CREATE TABLE IF NOT EXISTS metas (
            id       INTEGER PRIMARY KEY AUTOINCREMENT,
            usuario  INTEGER NOT NULL,
            nombre   TEXT,
            objetivo REAL DEFAULT 0,
            actual   REAL DEFAULT 0,
            mensual  REAL DEFAULT 0,
            fecha    TEXT,
            tipo_cta TEXT,
            creado   TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (usuario) REFERENCES usuarios(id)
        );

        -- periodo: diario | mensual | trimestral | semestral | anual | vencimiento
        -- Ejemplos:
        --   Nu / Lulo / Nequi remunerada  -> diario
        --   CDT mensual / fiducia mensual -> mensual
        --   CDT trimestral                -> trimestral
        --   CDT al vencimiento            -> vencimiento
        CREATE TABLE IF NOT EXISTS renta_fija (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            usuario   INTEGER NOT NULL,
            tipo      TEXT,
            canal     TEXT,
            monto     REAL DEFAULT 0,
            tasa_ea   REAL DEFAULT 0,
            com_ea    REAL DEFAULT 0,
            tasa_neta REAL DEFAULT 0,
            periodo   TEXT DEFAULT 'mensual',
            ini       TEXT,
            vence     TEXT,
            estado    TEXT DEFAULT 'activo',
            notas     TEXT,
            creado    TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (usuario) REFERENCES usuarios(id)
        );

        CREATE TABLE IF NOT EXISTS renta_variable (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            usuario     INTEGER NOT NULL,
            tipo        TEXT,
            ticker      TEXT,
            canal       TEXT,
            cantidad    REAL DEFAULT 0,
            precio_comp REAL DEFAULT 0,
            precio_act  REAL DEFAULT 0,
            com_pct     REAL DEFAULT 0,
            fecha       TEXT,
            riesgo      TEXT DEFAULT 'moderado',
            tesis       TEXT,
            creado      TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (usuario) REFERENCES usuarios(id)
        );

        CREATE TABLE IF NOT EXISTS inmobiliario (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            usuario   INTEGER NOT NULL,
            tipo      TEXT,
            nombre    TEXT,
            canal     TEXT,
            compra    REAL DEFAULT 0,
            actual    REAL DEFAULT 0,
            canon     REAL DEFAULT 0,
            tasa_ea   REAL DEFAULT 0,
            com_ea    REAL DEFAULT 0,
            periodo   TEXT DEFAULT 'mensual',
            fecha     TEXT,
            notas     TEXT,
            creado    TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (usuario) REFERENCES usuarios(id)
        );

        CREATE TABLE IF NOT EXISTS dolares (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            usuario    INTEGER NOT NULL,
            tipo       TEXT,
            nombre     TEXT,
            canal      TEXT,
            cant_usd   REAL DEFAULT 0,
            trm_compra REAL DEFAULT 0,
            rend_usd   REAL DEFAULT 0,
            fecha      TEXT,
            notas      TEXT,
            creado     TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (usuario) REFERENCES usuarios(id)
        );

        CREATE TABLE IF NOT EXISTS deudas (
            id      INTEGER PRIMARY KEY AUTOINCREMENT,
            usuario INTEGER NOT NULL,
            tipo    TEXT,
            entidad TEXT,
            saldo   REAL DEFAULT 0,
            cuota   REAL DEFAULT 0,
            cuotas  INTEGER DEFAULT 0,
            tasa_ea REAL DEFAULT 0,
            fecha   TEXT,
            prior   TEXT DEFAULT 'media',
            notas   TEXT,
            creado  TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (usuario) REFERENCES usuarios(id)
        );

        CREATE TABLE IF NOT EXISTS movimientos (
            id      INTEGER PRIMARY KEY AUTOINCREMENT,
            usuario INTEGER NOT NULL,
            cat     TEXT,
            inv_id  INTEGER,
            tipo    TEXT,
            monto   REAL DEFAULT 0,
            precio  REAL,
            fecha   TEXT,
            ctx     TEXT,
            trm     REAL DEFAULT 4200,
            creado  TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (usuario) REFERENCES usuarios(id)
        );
        """)
    print("[DB] Lista")

# ══════════════════════════════════════════════════════════════
#  MOTOR DE RENDIMIENTOS
#  Soporta: diario, mensual, trimestral, semestral, anual, vencimiento
# ══════════════════════════════════════════════════════════════

PERIODOS = {
    "diario":      {"n": 365, "label": "Rend. diario"},
    "mensual":     {"n": 12,  "label": "Rend. mensual"},
    "trimestral":  {"n": 4,   "label": "Rend. trimestral"},
    "semestral":   {"n": 2,   "label": "Rend. semestral"},
    "anual":       {"n": 1,   "label": "Rend. anual"},
    "vencimiento": {"n": 1,   "label": "Al vencimiento"},
}

def calcular_rendimiento(monto, tasa_ea, com_ea, periodo, fecha_ini=None, fecha_fin=None):
    """
    Calcula rendimientos netos por período (diario, mensual, anual)
    y el acumulado desde la fecha de inicio hasta hoy.

    Fórmula base: capitalización compuesta
      tasa_periodo = (1 + EA)^(1/n) - 1
    """
    tasa_neta = max(0.0, float(tasa_ea or 0) - float(com_ea or 0))
    ea        = tasa_neta / 100
    monto     = float(monto or 0)
    cfg       = PERIODOS.get(periodo, PERIODOS["mensual"])
    n         = cfg["n"]

    # Tasa por período (compuesta)
    tasa_periodo = (math.pow(1 + ea, 1 / n) - 1) if ea > 0 else 0

    # Rendimientos por período
    rend_diario    = monto * (math.pow(1 + ea, 1/365) - 1) if ea > 0 else 0
    rend_mensual   = monto * (math.pow(1 + ea, 1/12)  - 1) if ea > 0 else 0
    rend_trimest   = monto * (math.pow(1 + ea, 1/4)   - 1) if ea > 0 else 0
    rend_semest    = monto * (math.pow(1 + ea, 1/2)   - 1) if ea > 0 else 0
    rend_anual     = monto * ea
    rend_periodo   = monto * tasa_periodo   # según el período elegido

    # Rendimiento acumulado desde inicio hasta hoy (o hasta vencimiento)
    rend_acumulado = 0
    dias_activo    = 0
    if fecha_ini:
        try:
            ini = datetime.strptime(fecha_ini[:10], "%Y-%m-%d").date()
            fin = date.today()
            if fecha_fin:
                try:
                    fin_dt = datetime.strptime(fecha_fin[:10], "%Y-%m-%d").date()
                    fin = min(fin, fin_dt)
                except: pass
            dias_activo = max(0, (fin - ini).days)
            if dias_activo > 0 and ea > 0:
                tasa_dia = math.pow(1 + ea, 1/365) - 1
                rend_acumulado = monto * (math.pow(1 + tasa_dia, dias_activo) - 1)
        except: pass

    return {
        "tasa_neta":      round(tasa_neta, 4),
        "rend_diario":    round(rend_diario, 2),
        "rend_mensual":   round(rend_mensual, 0),
        "rend_trimestral":round(rend_trimest, 0),
        "rend_semestral": round(rend_semest, 0),
        "rend_anual":     round(rend_anual, 0),
        "rend_periodo":   round(rend_periodo, 2),   # según período elegido
        "rend_acumulado": round(rend_acumulado, 0),
        "dias_activo":    dias_activo,
        "periodo":        periodo,
        "label_periodo":  cfg["label"],
    }


# ══════════════════════════════════════════════════════════════
#  HELPERS
# ══════════════════════════════════════════════════════════════

def hash_pw(pw):
    return hashlib.sha256(pw.encode()).hexdigest()

def login_required(f):
    @wraps(f)
    def dec(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return dec

def uid():   return session["user_id"]
def today(): return date.today().isoformat()

def row_or_404(row):
    if not row: return jsonify({"error": "no encontrado"}), 404
    return jsonify(dict(row))

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
            u = db.execute(
                "SELECT * FROM usuarios WHERE email=? AND password=?",
                (email, hash_pw(pw))
            ).fetchone()
        if u:
            session["user_id"]   = u["id"]
            session["user_name"] = u["nombre"]
            return redirect(url_for("dashboard"))
        flash("Email o contraseña incorrectos", "error")
    return render_template("login.html")

@app.route("/registro", methods=["GET","POST"])
def registro():
    if request.method == "POST":
        nombre = request.form.get("nombre","").strip()
        email  = request.form.get("email","").strip().lower()
        pw     = request.form.get("password","")
        pw2    = request.form.get("password2","")
        if pw != pw2:
            flash("Las contraseñas no coinciden","error")
            return render_template("registro.html")
        if len(pw) < 6:
            flash("Contraseña mínimo 6 caracteres","error")
            return render_template("registro.html")
        try:
            with get_db() as db:
                db.execute(
                    "INSERT INTO usuarios (nombre,email,password) VALUES (?,?,?)",
                    (nombre, email, hash_pw(pw))
                )
            flash("Cuenta creada. Inicia sesión.","ok")
            return redirect(url_for("login"))
        except sqlite3.IntegrityError:
            flash("Este email ya está registrado","error")
    return render_template("registro.html")

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

# ══════════════════════════════════════════════════════════════
#  PÁGINAS
# ══════════════════════════════════════════════════════════════

@app.route("/dashboard")
@login_required
def dashboard():
    return render_template("dashboard.html", nombre=session["user_name"])

# ══════════════════════════════════════════════════════════════
#  API — MERCADO
# ══════════════════════════════════════════════════════════════

@app.route("/api/market")
@login_required
def market():
    data = {"usd_cop":4200,"gold_usd":2350,"tasa_br":9.50,"dtf":11.28,"ipc":5.2}
    try:
        r = requests.get("https://api.exchangerate-api.com/v4/latest/USD", timeout=5)
        if r.ok: data["usd_cop"] = round(r.json()["rates"].get("COP",4200))
    except: pass
    try:
        r2 = requests.get("https://api.exchangerate-api.com/v4/latest/XAU", timeout=5)
        if r2.ok: data["gold_usd"] = round(r2.json()["rates"].get("USD",2350))
    except: pass
    return jsonify(data)

# ══════════════════════════════════════════════════════════════
#  CRUD GENÉRICO — helper interno
# ══════════════════════════════════════════════════════════════

def crud_list(tabla, order="fecha DESC, creado DESC"):
    with get_db() as db:
        rows = db.execute(f"SELECT * FROM {tabla} WHERE usuario=? ORDER BY {order}", (uid(),)).fetchall()
    return [dict(r) for r in rows]

def crud_get(tabla, rid):
    with get_db() as db:
        r = db.execute(f"SELECT * FROM {tabla} WHERE id=? AND usuario=?", (rid, uid())).fetchone()
    return dict(r) if r else None

def crud_delete(tabla, rid):
    with get_db() as db:
        db.execute(f"DELETE FROM {tabla} WHERE id=? AND usuario=?", (rid, uid()))

# ══════════════════════════════════════════════════════════════
#  API — INGRESOS
# ══════════════════════════════════════════════════════════════

@app.route("/api/ingresos", methods=["GET"])
@login_required
def get_ingresos():
    return jsonify(crud_list("ingresos"))

@app.route("/api/ingresos", methods=["POST"])
@login_required
def add_ingreso():
    d = request.get_json() or {}
    with get_db() as db:
        c = db.execute(
            "INSERT INTO ingresos (usuario,cat,desc,monto,fecha,period,notas) VALUES (?,?,?,?,?,?,?)",
            (uid(),d.get("cat"),d.get("desc"),d.get("monto",0),d.get("fecha",today()),d.get("period","mensual"),d.get("notas"))
        )
    return jsonify({"id":c.lastrowid,"ok":True}), 201

@app.route("/api/ingresos/<int:rid>", methods=["GET"])
@login_required
def get_ingreso_one(rid):
    r = crud_get("ingresos", rid)
    return jsonify(r) if r else (jsonify({"error":"no encontrado"}),404)

@app.route("/api/ingresos/<int:rid>", methods=["PUT"])
@login_required
def edit_ingreso(rid):
    d = request.get_json() or {}
    with get_db() as db:
        db.execute(
            "UPDATE ingresos SET cat=?,desc=?,monto=?,fecha=?,period=?,notas=? WHERE id=? AND usuario=?",
            (d.get("cat"),d.get("desc"),d.get("monto",0),d.get("fecha"),d.get("period"),d.get("notas"),rid,uid())
        )
    return jsonify({"ok":True})

@app.route("/api/ingresos/<int:rid>", methods=["DELETE"])
@login_required
def del_ingreso(rid):
    crud_delete("ingresos", rid)
    return jsonify({"ok":True})

# ══════════════════════════════════════════════════════════════
#  API — GASTOS
# ══════════════════════════════════════════════════════════════

@app.route("/api/gastos", methods=["GET"])
@login_required
def get_gastos():
    return jsonify(crud_list("gastos"))

@app.route("/api/gastos", methods=["POST"])
@login_required
def add_gasto():
    d = request.get_json() or {}
    with get_db() as db:
        c = db.execute(
            "INSERT INTO gastos (usuario,cat,desc,monto,fecha,tipo,presup,notas) VALUES (?,?,?,?,?,?,?,?)",
            (uid(),d.get("cat"),d.get("desc"),d.get("monto",0),d.get("fecha",today()),d.get("tipo","variable"),d.get("presup",0),d.get("notas"))
        )
    return jsonify({"id":c.lastrowid,"ok":True}), 201

@app.route("/api/gastos/<int:rid>", methods=["GET"])
@login_required
def get_gasto_one(rid):
    r = crud_get("gastos", rid)
    return jsonify(r) if r else (jsonify({"error":"no encontrado"}),404)

@app.route("/api/gastos/<int:rid>", methods=["PUT"])
@login_required
def edit_gasto(rid):
    d = request.get_json() or {}
    with get_db() as db:
        db.execute(
            "UPDATE gastos SET cat=?,desc=?,monto=?,fecha=?,tipo=?,presup=?,notas=? WHERE id=? AND usuario=?",
            (d.get("cat"),d.get("desc"),d.get("monto",0),d.get("fecha"),d.get("tipo"),d.get("presup",0),d.get("notas"),rid,uid())
        )
    return jsonify({"ok":True})

@app.route("/api/gastos/<int:rid>", methods=["DELETE"])
@login_required
def del_gasto(rid):
    crud_delete("gastos", rid)
    return jsonify({"ok":True})

# ══════════════════════════════════════════════════════════════
#  API — METAS
# ══════════════════════════════════════════════════════════════

@app.route("/api/metas", methods=["GET"])
@login_required
def get_metas():
    return jsonify(crud_list("metas","creado DESC"))

@app.route("/api/metas", methods=["POST"])
@login_required
def add_meta():
    d = request.get_json() or {}
    with get_db() as db:
        c = db.execute(
            "INSERT INTO metas (usuario,nombre,objetivo,actual,mensual,fecha,tipo_cta) VALUES (?,?,?,?,?,?,?)",
            (uid(),d.get("nombre"),d.get("objetivo",0),d.get("actual",0),d.get("mensual",0),d.get("fecha"),d.get("tipo_cta"))
        )
    return jsonify({"id":c.lastrowid,"ok":True}), 201

@app.route("/api/metas/<int:rid>", methods=["GET"])
@login_required
def get_meta_one(rid):
    r = crud_get("metas", rid)
    return jsonify(r) if r else (jsonify({"error":"no encontrado"}),404)

@app.route("/api/metas/<int:rid>", methods=["PUT"])
@login_required
def edit_meta(rid):
    d = request.get_json() or {}
    with get_db() as db:
        db.execute(
            "UPDATE metas SET nombre=?,objetivo=?,actual=?,mensual=?,fecha=?,tipo_cta=? WHERE id=? AND usuario=?",
            (d.get("nombre"),d.get("objetivo",0),d.get("actual",0),d.get("mensual",0),d.get("fecha"),d.get("tipo_cta"),rid,uid())
        )
    return jsonify({"ok":True})

@app.route("/api/metas/<int:rid>", methods=["DELETE"])
@login_required
def del_meta(rid):
    crud_delete("metas", rid)
    return jsonify({"ok":True})

# ══════════════════════════════════════════════════════════════
#  API — RENTA FIJA (con motor de rendimientos)
# ══════════════════════════════════════════════════════════════

def enrich_rf(row):
    """Agrega cálculos de rendimiento a un registro de renta fija."""
    item = dict(row)
    calc = calcular_rendimiento(item["monto"],item["tasa_ea"],item["com_ea"],item["periodo"],item["ini"],item["vence"])
    item.update(calc)
    if item.get("vence"):
        try:
            vd = datetime.strptime(item["vence"][:10],"%Y-%m-%d").date()
            item["dias_para_vencer"] = (vd - date.today()).days
        except: item["dias_para_vencer"] = None
    return item

@app.route("/api/renta_fija", methods=["GET"])
@login_required
def get_rf():
    with get_db() as db:
        rows = db.execute("SELECT * FROM renta_fija WHERE usuario=? ORDER BY creado DESC",(uid(),)).fetchall()
    return jsonify([enrich_rf(r) for r in rows])

@app.route("/api/renta_fija", methods=["POST"])
@login_required
def add_rf():
    d = request.get_json() or {}
    tasa_ea  = float(d.get("tasa_ea",0))
    com_ea   = float(d.get("com_ea",0))
    with get_db() as db:
        c = db.execute(
            """INSERT INTO renta_fija
               (usuario,tipo,canal,monto,tasa_ea,com_ea,tasa_neta,periodo,ini,vence,estado,notas)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (uid(),d.get("tipo"),d.get("canal"),d.get("monto",0),
             tasa_ea,com_ea,max(0,tasa_ea-com_ea),
             d.get("periodo","mensual"),d.get("ini"),d.get("vence"),
             d.get("estado","activo"),d.get("notas"))
        )
    return jsonify({"id":c.lastrowid,"ok":True}), 201

@app.route("/api/renta_fija/<int:rid>", methods=["GET"])
@login_required
def get_rf_one(rid):
    with get_db() as db:
        r = db.execute("SELECT * FROM renta_fija WHERE id=? AND usuario=?",(rid,uid())).fetchone()
    return jsonify(enrich_rf(r)) if r else (jsonify({"error":"no encontrado"}),404)

@app.route("/api/renta_fija/<int:rid>", methods=["PUT"])
@login_required
def edit_rf(rid):
    d = request.get_json() or {}
    tasa_ea = float(d.get("tasa_ea",0))
    com_ea  = float(d.get("com_ea",0))
    with get_db() as db:
        db.execute(
            """UPDATE renta_fija SET tipo=?,canal=?,monto=?,tasa_ea=?,com_ea=?,tasa_neta=?,
               periodo=?,ini=?,vence=?,estado=?,notas=? WHERE id=? AND usuario=?""",
            (d.get("tipo"),d.get("canal"),d.get("monto",0),
             tasa_ea,com_ea,max(0,tasa_ea-com_ea),
             d.get("periodo"),d.get("ini"),d.get("vence"),
             d.get("estado"),d.get("notas"),rid,uid())
        )
    return jsonify({"ok":True})

@app.route("/api/renta_fija/<int:rid>", methods=["DELETE"])
@login_required
def del_rf(rid):
    crud_delete("renta_fija", rid)
    return jsonify({"ok":True})

# ══════════════════════════════════════════════════════════════
#  API — RENTA VARIABLE
# ══════════════════════════════════════════════════════════════

def enrich_rv(row):
    item = dict(row)
    costo = item["cantidad"] * item["precio_comp"]
    val   = item["cantidad"] * item["precio_act"]
    item["costo_total"]  = round(costo,0)
    item["valor_actual"] = round(val,0)
    item["ganancia"]     = round(val-costo,0)
    item["retorno_pct"]  = round((val-costo)/costo*100 if costo>0 else 0,2)
    return item

@app.route("/api/renta_variable", methods=["GET"])
@login_required
def get_rv():
    with get_db() as db:
        rows = db.execute("SELECT * FROM renta_variable WHERE usuario=? ORDER BY creado DESC",(uid(),)).fetchall()
    return jsonify([enrich_rv(r) for r in rows])

@app.route("/api/renta_variable", methods=["POST"])
@login_required
def add_rv():
    d = request.get_json() or {}
    with get_db() as db:
        c = db.execute(
            """INSERT INTO renta_variable
               (usuario,tipo,ticker,canal,cantidad,precio_comp,precio_act,com_pct,fecha,riesgo,tesis)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (uid(),d.get("tipo"),d.get("ticker"),d.get("canal"),
             d.get("cantidad",0),d.get("precio_comp",0),
             d.get("precio_act",d.get("precio_comp",0)),
             d.get("com_pct",0),d.get("fecha",today()),d.get("riesgo","moderado"),d.get("tesis"))
        )
    return jsonify({"id":c.lastrowid,"ok":True}), 201

@app.route("/api/renta_variable/<int:rid>", methods=["GET"])
@login_required
def get_rv_one(rid):
    with get_db() as db:
        r = db.execute("SELECT * FROM renta_variable WHERE id=? AND usuario=?",(rid,uid())).fetchone()
    return jsonify(enrich_rv(r)) if r else (jsonify({"error":"no encontrado"}),404)

@app.route("/api/renta_variable/<int:rid>", methods=["PUT"])
@login_required
def edit_rv(rid):
    d = request.get_json() or {}
    with get_db() as db:
        db.execute(
            """UPDATE renta_variable SET tipo=?,ticker=?,canal=?,cantidad=?,precio_comp=?,
               precio_act=?,com_pct=?,fecha=?,riesgo=?,tesis=? WHERE id=? AND usuario=?""",
            (d.get("tipo"),d.get("ticker"),d.get("canal"),d.get("cantidad",0),
             d.get("precio_comp",0),d.get("precio_act",0),d.get("com_pct",0),
             d.get("fecha"),d.get("riesgo"),d.get("tesis"),rid,uid())
        )
    return jsonify({"ok":True})

@app.route("/api/renta_variable/<int:rid>", methods=["DELETE"])
@login_required
def del_rv(rid):
    crud_delete("renta_variable", rid)
    return jsonify({"ok":True})

# ══════════════════════════════════════════════════════════════
#  API — INMOBILIARIO (con rendimientos por período)
# ══════════════════════════════════════════════════════════════

def enrich_inmo(row):
    item = dict(row)
    calc = calcular_rendimiento(item["compra"],item["tasa_ea"],item["com_ea"],item["periodo"],item["fecha"])
    item.update(calc)
    item["valorizacion"] = round(item["actual"]-item["compra"],0)
    item["renta_anual"]  = round(item["canon"]*12,0)
    item["rend_diario_canon"] = round(item["canon"]/30,2)
    return item

@app.route("/api/inmobiliario", methods=["GET"])
@login_required
def get_inmo():
    with get_db() as db:
        rows = db.execute("SELECT * FROM inmobiliario WHERE usuario=? ORDER BY creado DESC",(uid(),)).fetchall()
    return jsonify([enrich_inmo(r) for r in rows])

@app.route("/api/inmobiliario", methods=["POST"])
@login_required
def add_inmo():
    d = request.get_json() or {}
    with get_db() as db:
        c = db.execute(
            """INSERT INTO inmobiliario
               (usuario,tipo,nombre,canal,compra,actual,canon,tasa_ea,com_ea,periodo,fecha,notas)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (uid(),d.get("tipo"),d.get("nombre"),d.get("canal"),
             d.get("compra",0),d.get("actual",d.get("compra",0)),
             d.get("canon",0),d.get("tasa_ea",0),d.get("com_ea",0),
             d.get("periodo","mensual"),d.get("fecha",today()),d.get("notas"))
        )
    return jsonify({"id":c.lastrowid,"ok":True}), 201

@app.route("/api/inmobiliario/<int:rid>", methods=["GET"])
@login_required
def get_inmo_one(rid):
    with get_db() as db:
        r = db.execute("SELECT * FROM inmobiliario WHERE id=? AND usuario=?",(rid,uid())).fetchone()
    return jsonify(enrich_inmo(r)) if r else (jsonify({"error":"no encontrado"}),404)

@app.route("/api/inmobiliario/<int:rid>", methods=["PUT"])
@login_required
def edit_inmo(rid):
    d = request.get_json() or {}
    with get_db() as db:
        db.execute(
            """UPDATE inmobiliario SET tipo=?,nombre=?,canal=?,compra=?,actual=?,canon=?,
               tasa_ea=?,com_ea=?,periodo=?,fecha=?,notas=? WHERE id=? AND usuario=?""",
            (d.get("tipo"),d.get("nombre"),d.get("canal"),
             d.get("compra",0),d.get("actual",0),d.get("canon",0),
             d.get("tasa_ea",0),d.get("com_ea",0),d.get("periodo"),
             d.get("fecha"),d.get("notas"),rid,uid())
        )
    return jsonify({"ok":True})

@app.route("/api/inmobiliario/<int:rid>", methods=["DELETE"])
@login_required
def del_inmo(rid):
    crud_delete("inmobiliario", rid)
    return jsonify({"ok":True})

# ══════════════════════════════════════════════════════════════
#  API — DÓLARES
# ══════════════════════════════════════════════════════════════

@app.route("/api/dolares", methods=["GET"])
@login_required
def get_usd():
    trm = float(request.args.get("trm",4200))
    with get_db() as db:
        rows = db.execute("SELECT * FROM dolares WHERE usuario=? ORDER BY creado DESC",(uid(),)).fetchall()
    result = []
    for r in rows:
        item = dict(r)
        cop_c = item["cant_usd"]*item["trm_compra"]
        cop_a = item["cant_usd"]*trm
        item["cop_compra"]   = round(cop_c,0)
        item["cop_actual"]   = round(cop_a,0)
        item["gp_cambiaria"] = round(cop_a-cop_c,0)
        item["trm_actual"]   = trm
        result.append(item)
    return jsonify(result)

@app.route("/api/dolares", methods=["POST"])
@login_required
def add_usd():
    d = request.get_json() or {}
    with get_db() as db:
        c = db.execute(
            "INSERT INTO dolares (usuario,tipo,nombre,canal,cant_usd,trm_compra,rend_usd,fecha,notas) VALUES (?,?,?,?,?,?,?,?,?)",
            (uid(),d.get("tipo"),d.get("nombre"),d.get("canal"),
             d.get("cant_usd",0),d.get("trm_compra",4200),
             d.get("rend_usd",0),d.get("fecha",today()),d.get("notas"))
        )
    return jsonify({"id":c.lastrowid,"ok":True}), 201

@app.route("/api/dolares/<int:rid>", methods=["GET"])
@login_required
def get_usd_one(rid):
    r = crud_get("dolares",rid)
    return jsonify(r) if r else (jsonify({"error":"no encontrado"}),404)

@app.route("/api/dolares/<int:rid>", methods=["PUT"])
@login_required
def edit_usd(rid):
    d = request.get_json() or {}
    with get_db() as db:
        db.execute(
            "UPDATE dolares SET tipo=?,nombre=?,canal=?,cant_usd=?,trm_compra=?,rend_usd=?,fecha=?,notas=? WHERE id=? AND usuario=?",
            (d.get("tipo"),d.get("nombre"),d.get("canal"),d.get("cant_usd",0),
             d.get("trm_compra",4200),d.get("rend_usd",0),d.get("fecha"),d.get("notas"),rid,uid())
        )
    return jsonify({"ok":True})

@app.route("/api/dolares/<int:rid>", methods=["DELETE"])
@login_required
def del_usd(rid):
    crud_delete("dolares",rid)
    return jsonify({"ok":True})

# ══════════════════════════════════════════════════════════════
#  API — DEUDAS
# ══════════════════════════════════════════════════════════════

@app.route("/api/deudas", methods=["GET"])
@login_required
def get_deudas():
    with get_db() as db:
        rows = db.execute("SELECT * FROM deudas WHERE usuario=? ORDER BY tasa_ea DESC",(uid(),)).fetchall()
    result = []
    for r in rows:
        item = dict(r)
        total = item["cuota"]*item["cuotas"]
        item["total_pagar"] = round(total,0)
        item["int_total"]   = round(max(0,total-item["saldo"]),0)
        if item.get("fecha"):
            try:
                fp = datetime.strptime(item["fecha"][:10],"%Y-%m-%d").date()
                item["dias_pago"] = (fp-date.today()).days
            except: item["dias_pago"] = None
        result.append(item)
    return jsonify(result)

@app.route("/api/deudas", methods=["POST"])
@login_required
def add_deuda():
    d = request.get_json() or {}
    with get_db() as db:
        c = db.execute(
            "INSERT INTO deudas (usuario,tipo,entidad,saldo,cuota,cuotas,tasa_ea,fecha,prior,notas) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (uid(),d.get("tipo"),d.get("entidad"),d.get("saldo",0),
             d.get("cuota",0),d.get("cuotas",0),d.get("tasa_ea",0),
             d.get("fecha"),d.get("prior","media"),d.get("notas"))
        )
    return jsonify({"id":c.lastrowid,"ok":True}), 201

@app.route("/api/deudas/<int:rid>", methods=["GET"])
@login_required
def get_deuda_one(rid):
    r = crud_get("deudas",rid)
    return jsonify(r) if r else (jsonify({"error":"no encontrado"}),404)

@app.route("/api/deudas/<int:rid>", methods=["PUT"])
@login_required
def edit_deuda(rid):
    d = request.get_json() or {}
    with get_db() as db:
        db.execute(
            "UPDATE deudas SET tipo=?,entidad=?,saldo=?,cuota=?,cuotas=?,tasa_ea=?,fecha=?,prior=?,notas=? WHERE id=? AND usuario=?",
            (d.get("tipo"),d.get("entidad"),d.get("saldo",0),d.get("cuota",0),
             d.get("cuotas",0),d.get("tasa_ea",0),d.get("fecha"),
             d.get("prior"),d.get("notas"),rid,uid())
        )
    return jsonify({"ok":True})

@app.route("/api/deudas/<int:rid>", methods=["DELETE"])
@login_required
def del_deuda(rid):
    crud_delete("deudas",rid)
    return jsonify({"ok":True})

# ══════════════════════════════════════════════════════════════
#  API — MOVIMIENTOS
# ══════════════════════════════════════════════════════════════

@app.route("/api/movimientos", methods=["GET"])
@login_required
def get_movs():
    cat    = request.args.get("cat")
    inv_id = request.args.get("inv_id")
    q = "SELECT * FROM movimientos WHERE usuario=?"
    p = [uid()]
    if cat:    q += " AND cat=?";    p.append(cat)
    if inv_id: q += " AND inv_id=?"; p.append(inv_id)
    q += " ORDER BY fecha DESC, creado DESC"
    with get_db() as db:
        rows = db.execute(q,p).fetchall()
    return jsonify([dict(r) for r in rows])

@app.route("/api/movimientos", methods=["POST"])
@login_required
def add_mov():
    d = request.get_json() or {}
    cat    = d.get("cat")
    inv_id = d.get("inv_id")
    tipo   = d.get("tipo")
    monto  = float(d.get("monto",0))
    precio = d.get("precio")
    with get_db() as db:
        c = db.execute(
            "INSERT INTO movimientos (usuario,cat,inv_id,tipo,monto,precio,fecha,ctx,trm) VALUES (?,?,?,?,?,?,?,?,?)",
            (uid(),cat,inv_id,tipo,monto,precio,d.get("fecha",today()),d.get("ctx"),d.get("trm",4200))
        )
        # Actualizar valor subyacente automáticamente
        if tipo=="aporte" and cat=="renta_fija":
            db.execute("UPDATE renta_fija SET monto=monto+? WHERE id=? AND usuario=?",(monto,inv_id,uid()))
        elif tipo=="retiro" and cat=="renta_fija":
            db.execute("UPDATE renta_fija SET monto=MAX(0,monto-?) WHERE id=? AND usuario=?",(monto,inv_id,uid()))
        elif tipo=="actualizacion" and cat=="renta_variable" and precio:
            db.execute("UPDATE renta_variable SET precio_act=? WHERE id=? AND usuario=?",(precio,inv_id,uid()))
        elif tipo=="aporte" and cat=="inmobiliario":
            db.execute("UPDATE inmobiliario SET actual=actual+? WHERE id=? AND usuario=?",(monto,inv_id,uid()))
    return jsonify({"id":c.lastrowid,"ok":True}), 201

@app.route("/api/movimientos/<int:rid>", methods=["DELETE"])
@login_required
def del_mov(rid):
    crud_delete("movimientos",rid)
    return jsonify({"ok":True})

# ══════════════════════════════════════════════════════════════
#  API — RENDIMIENTOS DETALLADOS (diario, mensual, anual)
# ══════════════════════════════════════════════════════════════

@app.route("/api/rendimientos")
@login_required
def rendimientos():
    """
    Tabla de rendimientos por período para todas las inversiones activas.
    Incluye rend. diario para cuentas remuneradas (Nu, Lulo, Nequi).
    """
    with get_db() as db:
        rf_rows   = db.execute("SELECT * FROM renta_fija WHERE usuario=? AND estado='activo'",(uid(),)).fetchall()
        inmo_rows = db.execute("SELECT * FROM inmobiliario WHERE usuario=?",(uid(),)).fetchall()

    resultado = []
    for r in rf_rows:
        c = calcular_rendimiento(r["monto"],r["tasa_ea"],r["com_ea"],r["periodo"],r["ini"],r["vence"])
        resultado.append({
            "cat":             "Renta Fija",
            "id":              r["id"],
            "nombre":          r["tipo"],
            "canal":           r["canal"],
            "monto":           r["monto"],
            "tasa_ea":         r["tasa_ea"],
            "tasa_neta":       c["tasa_neta"],
            "periodo":         r["periodo"],
            "label_periodo":   c["label_periodo"],
            "rend_diario":     c["rend_diario"],
            "rend_mensual":    c["rend_mensual"],
            "rend_trimestral": c["rend_trimestral"],
            "rend_semestral":  c["rend_semestral"],
            "rend_anual":      c["rend_anual"],
            "rend_periodo":    c["rend_periodo"],
            "rend_acumulado":  c["rend_acumulado"],
            "dias_activo":     c["dias_activo"],
            "vence":           r["vence"],
        })

    for r in inmo_rows:
        c = calcular_rendimiento(r["compra"],r["tasa_ea"],r["com_ea"],r["periodo"],r["fecha"])
        resultado.append({
            "cat":             "Inmobiliario",
            "id":              r["id"],
            "nombre":          r["nombre"] or r["tipo"],
            "canal":           r["canal"],
            "monto":           r["compra"],
            "tasa_ea":         r["tasa_ea"],
            "tasa_neta":       c["tasa_neta"],
            "periodo":         r["periodo"],
            "label_periodo":   c["label_periodo"],
            "rend_diario":     round(r["canon"]/30,2),
            "rend_mensual":    r["canon"],
            "rend_trimestral": r["canon"]*3,
            "rend_semestral":  r["canon"]*6,
            "rend_anual":      r["canon"]*12,
            "rend_periodo":    r["canon"],
            "rend_acumulado":  c["rend_acumulado"],
            "dias_activo":     c["dias_activo"],
            "vence":           None,
        })

    return jsonify(resultado)

# ══════════════════════════════════════════════════════════════
#  API — RESUMEN DASHBOARD
# ══════════════════════════════════════════════════════════════

@app.route("/api/resumen")
@login_required
def resumen():
    trm = float(request.args.get("trm",4200))
    u   = uid()
    with get_db() as db:
        ing   = db.execute("SELECT COALESCE(SUM(monto),0) s FROM ingresos WHERE usuario=?",(u,)).fetchone()["s"]
        gas   = db.execute("SELECT COALESCE(SUM(monto),0) s FROM gastos WHERE usuario=?",(u,)).fetchone()["s"]
        gas_f = db.execute("SELECT COALESCE(SUM(monto),0) s FROM gastos WHERE usuario=? AND tipo='fijo'",(u,)).fetchone()["s"]
        ah    = db.execute("SELECT COALESCE(SUM(actual),0) s FROM metas WHERE usuario=?",(u,)).fetchone()["s"]
        ah_m  = db.execute("SELECT COALESCE(SUM(mensual),0) s FROM metas WHERE usuario=?",(u,)).fetchone()["s"]
        deu   = db.execute("SELECT COALESCE(SUM(saldo),0) s FROM deudas WHERE usuario=?",(u,)).fetchone()["s"]
        deu_c = db.execute("SELECT COALESCE(SUM(cuota),0) s FROM deudas WHERE usuario=?",(u,)).fetchone()["s"]
        rf_r  = db.execute("SELECT * FROM renta_fija WHERE usuario=? AND estado='activo'",(u,)).fetchall()
        rv_r  = db.execute("SELECT * FROM renta_variable WHERE usuario=?",(u,)).fetchall()
        inmo_r= db.execute("SELECT * FROM inmobiliario WHERE usuario=?",(u,)).fetchall()
        usd_r = db.execute("SELECT * FROM dolares WHERE usuario=?",(u,)).fetchall()

    rf_tot  = sum(r["monto"] for r in rf_r)
    rv_val  = sum(r["cantidad"]*r["precio_act"] for r in rv_r)
    rv_cost = sum(r["cantidad"]*r["precio_comp"] for r in rv_r)
    inmo_v  = sum(r["actual"] for r in inmo_r)
    usd_cop = sum(r["cant_usd"]*trm for r in usd_r)
    port    = rf_tot + rv_val + inmo_v + usd_cop

    # Rendimientos calculados con motor
    rend_rf    = sum(calcular_rendimiento(r["monto"],r["tasa_ea"],r["com_ea"],r["periodo"],r["ini"])["rend_anual"] for r in rf_r)
    rend_rf_d  = sum(calcular_rendimiento(r["monto"],r["tasa_ea"],r["com_ea"],r["periodo"],r["ini"])["rend_diario"] for r in rf_r)
    rend_rf_m  = sum(calcular_rendimiento(r["monto"],r["tasa_ea"],r["com_ea"],r["periodo"],r["ini"])["rend_mensual"] for r in rf_r)
    rend_rf_ac = sum(calcular_rendimiento(r["monto"],r["tasa_ea"],r["com_ea"],r["periodo"],r["ini"])["rend_acumulado"] for r in rf_r)
    rend_inmo  = sum(r["canon"]*12 for r in inmo_r)
    rend_inmo_d= sum(r["canon"]/30 for r in inmo_r)

    # Alertas
    alertas = []
    if ing>0 and gas/ing>0.8:
        alertas.append({"tipo":"critical","msg":f"Gastos al {gas/ing*100:.1f}% del ingreso. Máx recomendado 80%."})
    elif ing>0 and gas/ing>0.6:
        alertas.append({"tipo":"warning","msg":f"Gastos al {gas/ing*100:.1f}% del ingreso. Revisa tu presupuesto."})
    if gas_f>0 and ah<gas_f*3:
        alertas.append({"tipo":"warning","msg":f"Fondo emergencia: {ah/gas_f:.1f} meses. Mínimo recomendado 3."})
    if port>0 and deu/port>0.5:
        alertas.append({"tipo":"critical","msg":f"Ratio deuda/activos: {deu/port*100:.0f}%. Recomendado < 40%."})

    # CDTs próximos a vencer
    with get_db() as db:
        cdts = db.execute("SELECT tipo,monto,vence FROM renta_fija WHERE usuario=? AND estado='activo' AND vence IS NOT NULL",(u,)).fetchall()
    for c in cdts:
        try:
            vd   = datetime.strptime(c["vence"][:10],"%Y-%m-%d").date()
            dias = (vd - date.today()).days
            if 0<=dias<=30:
                alertas.append({"tipo":"warning","msg":f"{c['tipo']} vence en {dias} días — ${c['monto']:,.0f}"})
            elif dias<0:
                alertas.append({"tipo":"critical","msg":f"{c['tipo']} venció el {c['vence']} sin renovar."})
        except: pass

    # Deudas con pago próximo
    with get_db() as db:
        deudas_p = db.execute("SELECT entidad,tipo,cuota,fecha FROM deudas WHERE usuario=? AND fecha IS NOT NULL",(u,)).fetchall()
    for d in deudas_p:
        try:
            fp   = datetime.strptime(d["fecha"][:10],"%Y-%m-%d").date()
            dias = (fp - date.today()).days
            if 0<=dias<=7:
                alertas.append({"tipo":"warning","msg":f"Pago en {dias} días: {d['entidad'] or d['tipo']} — ${d['cuota']:,.0f}"})
        except: pass

    return jsonify({
        "ingresos":       round(ing,0),
        "gastos":         round(gas,0),
        "gastos_fijos":   round(gas_f,0),
        "ahorro":         round(ah,0),
        "ahorro_mensual": round(ah_m,0),
        "portafolio":     round(port,0),
        "rf":             round(rf_tot,0),
        "rv":             round(rv_val,0),
        "rv_costo":       round(rv_cost,0),
        "rv_gp":          round(rv_val-rv_cost,0),
        "inmo":           round(inmo_v,0),
        "usd":            round(usd_cop,0),
        "deudas":         round(deu,0),
        "deudas_cuota":   round(deu_c,0),
        "neto":           round(port-deu,0),
        "rendimientos": {
            "rf_diario":   round(rend_rf_d,2),
            "rf_mensual":  round(rend_rf_m,0),
            "rf_anual":    round(rend_rf,0),
            "rf_acumulado":round(rend_rf_ac,0),
            "inmo_diario": round(rend_inmo_d,2),
            "inmo_mensual":round(rend_inmo/12,0),
            "inmo_anual":  round(rend_inmo,0),
            "rv_gp":       round(rv_val-rv_cost,0),
            "total_diario":round(rend_rf_d+rend_inmo_d,2),
            "total_mensual":round(rend_rf_m+rend_inmo/12,0),
            "total_anual": round(rend_rf+rend_inmo,0),
        },
        "alertas": alertas,
    })

# ══════════════════════════════════════════════════════════════
#  HEALTH
# ══════════════════════════════════════════════════════════════

@app.route("/health")
def health():
    return jsonify({"status":"ok","app":"FinTrack CO v4","time":datetime.now().isoformat()})

# ══════════════════════════════════════════════════════════════
#  MAIN
# ══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    init_db()
    port  = int(os.environ.get("PORT",5000))
    debug = os.environ.get("FLASK_DEBUG","true").lower()=="true"
    print(f"\n  FinTrack CO v4 — http://localhost:{port}\n")
    app.run(host="0.0.0.0", port=port, debug=debug)

# ── Rutas de páginas adicionales ──────────────────────────────

PAGINAS = [
    ("mercado",       "Mercado"),
    ("ingresos",      "Ingresos"),
    ("gastos",        "Gastos"),
    ("ahorro",        "Ahorro"),
    ("seguimiento",   "Seguimiento"),
    ("renta_fija",    "Renta Fija"),
    ("renta_variable","Renta Variable"),
    ("inmobiliario",  "Inmobiliario"),
    ("dolares",       "Dólares"),
    ("rendimientos",  "Rendimientos"),
    ("deudas",        "Deudas"),
]

for _ruta, _titulo in PAGINAS:
    def _make_view(ruta, titulo):
        @app.route(f"/{ruta}", endpoint=ruta)
        @login_required
        def _view():
            tpl = f"{ruta}.html"
            import os as _os
            tpl_path = _os.path.join(app.template_folder, tpl)
            if _os.path.exists(tpl_path):
                return render_template(tpl, nombre=session["user_name"])
            # Template genérico si no existe archivo específico
            return render_template("generic.html",
                nombre=session["user_name"],
                titulo=titulo,
                active=ruta)
        return _view
    _make_view(_ruta, _titulo)
