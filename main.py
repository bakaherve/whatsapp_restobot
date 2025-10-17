from flask import Flask, request, render_template, redirect, url_for, session
from twilio.twiml.messaging_response import MessagingResponse
from twilio.rest import Client as TwilioClient
from supabase import create_client, Client as SupabaseClient
from datetime import datetime
import os, logging, requests 
from urllib.parse import urlencode


# ============================================================
# GLOBAL MEMORY (session store)
# ============================================================
user_sessions = {}


# ============================================================
# 1️⃣ APP CONFIGURATION
# ============================================================
app = Flask(__name__)
app.secret_key = os.getenv("SESSION_SECRET", "supersecret")

app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=True,
    PERMANENT_SESSION_LIFETIME=3600,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logging.info("🚀 Starting Mama Mia Bot – Full Edition")


# ============================================================
# 2️⃣ ENVIRONMENT VARIABLES
# ============================================================
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
TWILIO_SID = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_AUTH = os.getenv("TWILIO_AUTH_TOKEN")
TWILIO_WHATSAPP_NUMBER = os.getenv("TWILIO_WHATSAPP_NUMBER")

ADMIN_USERNAME = os.getenv("ADMIN_USERNAME")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD")
RESTAURANT_NAME = os.getenv("RESTAURANT_NAME", "Mama Mia Restaurant")
BRAND_NAME = os.getenv("BRAND_NAME", "RestoBot")

supabase: SupabaseClient = create_client(SUPABASE_URL, SUPABASE_KEY)
twilio_client = TwilioClient(TWILIO_SID, TWILIO_AUTH)


# ============================================================
# 3️⃣ UTILITIES
# ============================================================
def normalize_number(number: str) -> str:
    """Ensure WhatsApp number format is consistent."""
    if not number:
        return ""
    number = number.strip()
    if not number.startswith("whatsapp:"):
        if not number.startswith("+"):
            number = f"+{number}"
        number = f"whatsapp:{number}"
    return number


def save_order_to_supabase(number, orders, address):
    """Save an order safely and always return its ID."""
    try:
        number = normalize_number(number)
        items_summary = ", ".join([f"{o['qty']}x {o['dish']}" for o in orders])
        total = sum(o["qty"] * o["price"] for o in orders)
        payload = {
            "date": datetime.now().isoformat(),
            "number": number,
            "items": items_summary,
            "total": total,
            "address": address,
            "status": "pending",
            "confirmed_by": None
        }

        url = f"{SUPABASE_URL}/rest/v1/orders"
        headers = {
            "apikey": SUPABASE_KEY,
            "Authorization": f"Bearer {SUPABASE_KEY}",
            "Content-Type": "application/json",
            "Prefer": "return=representation"
        }

        response = requests.post(url, headers=headers, json=payload, timeout=10)
        if response.status_code not in (200, 201):
            raise Exception(f"HTTP {response.status_code}: {response.text}")

        data = response.json()
        order_id = data[0]["id"] if data else None

        logging.info(f"🧾 Order saved | ID:{order_id} | {number} | {items_summary} | {total:,} CDF")
        return total, order_id

    except Exception as e:
        logging.error(f"❌ Error saving order: {e}")
        return None, None


# ============================================================
# 4️⃣ MENU
# ============================================================
menu = {
    "1": ("Riz au poisson", 6000),
    "2": ("Poulet braisé", 8000),
    "3": ("Frites", 5000),
    "4": ("Jus naturel", 2500),
}


# ============================================================
# 5️⃣ ROUTES
# ============================================================
@app.before_request
def protect_admin():
    if request.path.startswith(("/admin", "/update_status")):
        if not session.get("logged_in"):
            return redirect(url_for("login"))


@app.route("/")
def home():
    return f"🍽️ {RESTAURANT_NAME} is running ✅"


# ---------------- LOGIN ----------------
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "").strip()
        if username == ADMIN_USERNAME and password == ADMIN_PASSWORD:
            session["logged_in"] = True
            logging.info("✅ Admin logged in")
            return redirect(url_for("admin"))
        return render_template("login.html", error="Identifiants invalides.", rname=RESTAURANT_NAME, brand=BRAND_NAME)
    return render_template("login.html", rname=RESTAURANT_NAME, brand=BRAND_NAME)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# ---------------- ADMIN DASHBOARD ----------------
@app.route("/admin")
def admin():
    try:
        params = urlencode({"select": "*", "order": "id.desc"})
        headers = {
            "apikey": SUPABASE_KEY,
            "Authorization": f"Bearer {SUPABASE_KEY}",
        }
        url = f"{SUPABASE_URL}/rest/v1/orders?{params}"
        response = requests.get(url, headers=headers, timeout=10)
        if response.status_code != 200:
            raise Exception(f"HTTP {response.status_code}: {response.text}")

        data = response.json()
        return render_template(
            "dashboard.html",
            orders=data,
            brand=BRAND_NAME,
            rname=RESTAURANT_NAME,
        )
    except Exception as e:
        logging.error(f"❌ Dashboard error: {e}")
        return f"<h3>Erreur de chargement du tableau de bord.<br><br><code>{e}</code></h3>", 500


# ---------------- UPDATE STATUS ----------------
@app.route("/update_status", methods=["POST"])
def update_status():
    order_id = request.form.get("order_id")
    new_status = request.form.get("status")
    confirmed_by = request.form.get("confirmed_by")

    try:
        supabase.table("orders").update(
            {"status": new_status, "confirmed_by": confirmed_by}
        ).eq("id", order_id).execute()

        logging.info(f"✅ Order {order_id} updated manually by admin.")
        return redirect(url_for("admin"), code=303)
    except Exception as e:
        logging.error(f"❌ Error updating order manually: {e}")
        return f"<h3>Erreur : {e}</h3>", 500



# ============================================================
# 6️⃣ WHATSAPP WEBHOOK — CONVERSATION FLOW
# ============================================================
@app.route("/webhook", methods=["POST"])
def webhook():
    from_number = normalize_number(request.form.get("From"))
    msg = (request.form.get("Body") or "").strip().lower()
    resp = MessagingResponse()
    reply = resp.message()

    # Persistent session using global dictionary
    if from_number not in user_sessions:
        user_sessions[from_number] = {"stage": "start", "cart": []}
    user = user_sessions[from_number]

    logging.info(f"{from_number} | stage={user['stage']} | msg={msg}")

    # 🧭 GLOBAL COMMANDS (handled before any state logic)
    if msg in ["0", "menu", "accueil", "start"]:
        user["stage"] = "start"
        user["cart"].clear()
        reply.body(
            "👋 *Bienvenue chez Mama Mia Restaurant !*\n"
            "Tapez :\n"
            "1️⃣ Menu\n"
            "2️⃣ Commander\n"
            "3️⃣ Nos horaires"
        )
        user["stage"] = "main"
        return str(resp)

    # ---- 1. MAIN MENU ----
    if user["stage"] == "start":
        reply.body(
            "👋 *Bienvenue chez Mama Mia Restaurant !*\n"
            "Tapez :\n"
            "1️⃣ Menu\n"
            "2️⃣ Commander\n"
            "3️⃣ Nos horaires"
        )
        user["stage"] = "main"
        return str(resp)

    # ---- 2. MAIN MENU CHOICES ----
    if user["stage"] == "main":
        if msg == "1":
            reply.body(
                "📋 *Menu du jour :*\n"
                "1️⃣ Riz au poisson\n"
                "2️⃣ Poulet braisé\n"
                "3️⃣ Frites\n"
                "4️⃣ Jus naturel\n\n"
                "Tapez le numéro du plat pour commander 🍽️"
            )
            user["stage"] = "menu"
        elif msg == "2":
            reply.body("Tapez *menu* pour voir les plats et choisir ce que vous voulez commander.")
        elif msg == "3":
            reply.body("🕒 *Nos horaires :*\nLun–Dim : 10h00 – 22h00")
        else:
            reply.body("❌ Choix invalide. Tapez 0 pour revenir au menu principal.")
        return str(resp)

    # ---- 3. MENU SELECTION ----
    if user["stage"] == "menu":
        if msg in menu:
            dish, price = menu[msg]
            user["cart"].append({"dish": dish, "qty": 1, "price": price})
            reply.body(f"Combien de *{dish}* souhaitez-vous ? (Tapez un nombre, ex: 2)")
            user["stage"] = "quantity"
        else:
            reply.body("⚠️ Choix invalide. Tapez un numéro de plat ou 0 pour revenir au menu principal.")
        return str(resp)

    # ---- 4. QUANTITY ----
    if user["stage"] == "quantity":
        if msg.isdigit():
            user["cart"][-1]["qty"] = int(msg)
            reply.body("Souhaitez-vous ajouter un autre plat ?\n1️⃣ Oui\n2️⃣ Non (continuer)")
            user["stage"] = "add_more"
        else:
            reply.body("Veuillez entrer un nombre valide (ex: 2).")
        return str(resp)

    # ---- 5. ADD MORE ----
    if user["stage"] == "add_more":
        if msg == "1":
            reply.body(
                "Quel plat souhaitez-vous ajouter ?\n"
                "1️⃣ Riz au poisson\n"
                "2️⃣ Poulet braisé\n"
                "3️⃣ Frites\n"
                "4️⃣ Jus naturel"
            )
            user["stage"] = "menu"
        elif msg == "2":
            reply.body(
                "Veuillez maintenant envoyer votre *nom et adresse complète* "
                "(ex : Nom Prénom – Quartier, Avenue...)."
            )
            user["stage"] = "address"
        else:
            reply.body("❌ Choix invalide. Tapez 1 ou 2.")
        return str(resp)

    # ---- 6. ADDRESS ----
    if user["stage"] == "address":
        address = msg
        total = sum(o["qty"] * o["price"] for o in user["cart"])
        items_summary = "\n".join([f"{o['qty']}× {o['dish']} → {o['qty']*o['price']:,} CDF" for o in user["cart"]])

        reply.body(
            f"✅ *Résumé de votre commande :*\n\n{items_summary}\n\n"
            f"💰 *Total : {total:,} CDF*\n\n"
            f"🏠 *Adresse :* {address}\n\n"
            "Confirmez-vous ?\n1️⃣ Oui\n2️⃣ Modifier"
        )
        user["stage"] = "confirm"
        user["address"] = address
        return str(resp)

    # ---- 7. CONFIRM ----
    if user["stage"] == "confirm":
        if msg == "1":
            total, order_id = save_order_to_supabase(from_number, user["cart"], user["address"])
            reply.body(
                f"Merci ! Votre commande est confirmée 🍽️🚗\n\n"
                f"ID : {order_id or '—'}\n"
                "Tapez 0 pour revenir au menu principal."
            )
            user["stage"] = "done"
            user["cart"].clear()
        elif msg == "2":
            reply.body("OK, renvoyez votre adresse correcte :")
            user["stage"] = "address"
        else:
            reply.body("Choix invalide. Tapez 1 pour confirmer, 2 pour modifier.")
        return str(resp)

    # ---- 8. DONE ----
    if user["stage"] == "done":
        reply.body("Tapez 0 pour revenir au menu principal.")
        return str(resp)

    # ---- FALLBACK ----
    reply.body("Tapez 0 pour revenir au menu principal.")
    return str(resp)


# ============================================================
# 7️⃣ RUN APP
# ============================================================
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
