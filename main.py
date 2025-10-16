from flask import Flask, request, render_template, redirect, session, url_for
from twilio.twiml.messaging_response import MessagingResponse
from twilio.rest import Client as TwilioClient
from supabase import create_client, Client as SupabaseClient
from datetime import datetime
import os
import logging
from logging.handlers import RotatingFileHandler

# ---------- Logging (clean & focused) ----------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(message)s",
    datefmt="%H:%M:%S"
)
handler = RotatingFileHandler("app.log", maxBytes=1_000_000, backupCount=3)
logging.getLogger().addHandler(handler)
logging.getLogger("werkzeug").setLevel(logging.WARNING)
logging.getLogger("supabase").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)

# ---------- App ----------
app = Flask(__name__)
app.secret_key = os.getenv("SESSION_SECRET", "change_me_in_env")  # set in env on Render/Replit

# ---------- Secrets / Config ----------
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
TWILIO_WHATSAPP_NUMBER = os.getenv("TWILIO_WHATSAPP_NUMBER")

ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "mamamia")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "1234")

BRAND_NAME = os.getenv("BRAND_NAME", "RestoBot")
RESTAURANT_NAME = os.getenv("RESTAURANT_NAME", "Mama Mia Restaurant")

# ---------- Clients ----------
supabase: SupabaseClient = create_client(SUPABASE_URL, SUPABASE_KEY)
twilio_client = TwilioClient(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)

logging.info("🚀 RestoBot started successfully")

# ---------- Menu (demo) ----------
menu = {
    "1": ("Riz au poisson", 6000),
    "2": ("Poulet braisé", 8000),
    "3": ("Frites", 5000),
    "4": ("Jus naturel", 2500),
}

# In-memory user state (ok for single instance demo)
user_state = {}

# ---------- Helpers ----------
def format_cart(orders):
    lines, total = [], 0
    for item in orders:
        name, qty, price = item["dish"], item["qty"], item["price"]
        subtotal = qty * price
        total += subtotal
        lines.append(f"{qty}× {name} → {subtotal:,} CDF")
    lines.append(f"\n💰 *Total : {total:,} CDF*")
    return "\n".join(lines), total

def save_order_to_supabase(number, orders, address):
    try:
        items_summary = ", ".join([f"{o['qty']}x {o['dish']}" for o in orders])
        total = sum(o["qty"] * o["price"] for o in orders)
        payload = {
            "date": datetime.now().isoformat(),
            "number": number,
            "items": items_summary,
            "total": total,
            "address": address,
            "status": "pending",
        }
        res = supabase.table("orders").insert(payload).execute()
        order_id = res.data[0]["id"]
        logging.info(f"🧾 Order saved | ID:{order_id} | {number} | {items_summary} | {total:,} CDF")
        return total, order_id
    except Exception as e:
        logging.error(f"❌ Error saving order: {e}")
        return None, None

# ---------- WhatsApp Webhook ----------
@app.route("/webhook", methods=["POST"])
def webhook():
    from_number = request.form.get("From")
    msg = (request.form.get("Body") or "").strip()
    resp = MessagingResponse()
    reply = resp.message()

    if from_number not in user_state:
        user_state[from_number] = {"stage": "main", "orders": [], "dish": None}

    state = user_state[from_number]

    # Main menu
    if state["stage"] == "main":
        if msg == "1":
            menu_text = "\n".join([f"{k}️⃣ {v[0]} – {v[1]:,} CDF" for k, v in menu.items()])
            reply.body(f"🍽 *Menu du jour – {RESTAURANT_NAME}*\n{menu_text}\n\nTapez 2️⃣ pour commander ou 3️⃣ pour nos horaires.")
        elif msg == "2":
            menu_text = "\n".join([f"{k}️⃣ {v[0]}" for k, v in menu.items()])
            reply.body(f"Quel plat souhaitez-vous commander ?\n{menu_text}\nTapez le numéro du plat.")
            state["stage"] = "choose_dish"
        elif msg == "3":
            reply.body("🕐 11 h – 22 h tous les jours\n📍 Kintambo Magasin\n📞 +243 000 000 000")
        else:
            reply.body(f"👋 Bienvenue chez *{RESTAURANT_NAME}* !\nTapez :\n1️⃣ Menu\n2️⃣ Commander\n3️⃣ Nos horaires")
        return str(resp)

    # Choose dish
    if state["stage"] == "choose_dish":
        if msg in menu:
            dish_name, price = menu[msg]
            state["dish"] = (dish_name, price)
            reply.body(f"Combien de *{dish_name}* souhaitez-vous ? (Tapez un nombre, ex : 2)")
            state["stage"] = "choose_quantity"
        else:
            reply.body("Choix invalide. Tapez un numéro du menu.")
        return str(resp)

    # Quantity
    if state["stage"] == "choose_quantity":
        if msg.isdigit() and int(msg) > 0:
            qty = int(msg)
            dish_name, price = state["dish"]
            state["orders"].append({"dish": dish_name, "qty": qty, "price": price})
            reply.body("Souhaitez-vous ajouter un autre plat ?\n1️⃣ Oui\n2️⃣ Non (continuer)")
            state["stage"] = "add_more"
        else:
            reply.body("Merci d’entrer une quantité valide (ex : 2).")
        return str(resp)

    # Add more?
    if state["stage"] == "add_more":
        if msg == "1":
            menu_text = "\n".join([f"{k}️⃣ {v[0]}" for k, v in menu.items()])
            reply.body(f"Quel plat souhaitez-vous ajouter ?\n{menu_text}")
            state["stage"] = "choose_dish"
        elif msg == "2":
            reply.body("Veuillez maintenant envoyer votre *nom et adresse complète* (ex : Nom – Quartier, Avenue...).")
            state["stage"] = "waiting_address"
        else:
            reply.body("Répondez 1 (oui) ou 2 (non).")
        return str(resp)

    # Address + summary
    if state["stage"] == "waiting_address":
        state["address"] = msg
        cart_text, total = format_cart(state["orders"])
        reply.body(
            f"✅ *Résumé de votre commande :*\n\n{cart_text}\n\n"
            f"🏠 Adresse : {state['address']}\n"
            f"Confirmez-vous ?\n1️⃣ Oui\n2️⃣ Modifier"
        )
        state["stage"] = "confirm_order"
        return str(resp)

    # Final confirmation
    if state["stage"] == "confirm_order":
        if msg == "1":
            total, order_id = save_order_to_supabase(from_number, state["orders"], state["address"])
            if order_id:
                reply.body(
                    f"✅ *Commande n°{order_id} enregistrée !*\n"
                    f"💰 Total : {total:,} CDF\n"
                    f"🚗 Livraison en préparation.\n\n"
                    f"Merci d’avoir commandé chez *{RESTAURANT_NAME}* 🍽️"
                )
            else:
                reply.body("❌ Une erreur est survenue lors de l’enregistrement. Réessayez plus tard.")
            # reset user
            user_state[from_number] = {"stage": "main", "orders": [], "dish": None}
        elif msg == "2":
            reply.body("Pas de souci ! Quel plat souhaitez-vous modifier ?\n" +
                       "\n".join([f"{k}️⃣ {v[0]}" for k, v in menu.items()]))
            state["orders"] = []
            state["stage"] = "choose_dish"
        else:
            reply.body("Répondez 1 (Oui) ou 2 (Modifier).")
        return str(resp)

    return str(resp)

# ---------- Auth ----------
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        password = (request.form.get("password") or "").strip()
        if username == ADMIN_USERNAME and password == ADMIN_PASSWORD:
            session["logged_in"] = True
            session["restaurant_name"] = RESTAURANT_NAME
            logging.info("✅ Admin login OK")
            return redirect(url_for("admin"))
        logging.warning("⚠️ Admin login failed")
        return render_template("login.html", error="Identifiants invalides.")
    return render_template("login.html")

@app.route("/logout")
def logout():
    session.pop("logged_in", None)
    session.pop("restaurant_name", None)
    return redirect(url_for("login"))

# ---------- Protect admin routes ----------
@app.before_request
def protect_admin():
    if request.path.startswith("/admin") or request.path.startswith("/update_status"):
        if not session.get("logged_in"):
            return redirect(url_for("login"))

# ---------- Admin Dashboard ----------
@app.route("/admin")
def admin():
    try:
        data = supabase.table("orders").select("*").order("id", desc=True).execute()
        return render_template("dashboard.html", orders=data.data, brand=BRAND_NAME, rname=session.get("restaurant_name"))
    except Exception as e:
        logging.error(f"❌ Dashboard error: {e}")
        return "<h3>Dashboard error</h3>", 500

# ---------- Update status + notify customer ----------
@app.route("/update_status", methods=["POST"])
def update_status():
    order_id = request.form.get("order_id")
    try:
        # 1) Update DB
        supabase.table("orders").update({"status": "delivered"}).eq("id", order_id).execute()
        logging.info(f"🟢 Order {order_id} marked delivered")
        # 2) Fetch contact
        row = supabase.table("orders").select("number, items, total").eq("id", order_id).single().execute().data
        number, items, total = row["number"], row["items"], row["total"]
        # 3) WhatsApp confirm
        msg = (
            f"✅ *Commande livrée !*\n\n"
            f"Vos plats : {items}\n"
            f"Montant total : {total:,} CDF\n\n"
            f"Merci d’avoir commandé chez *{RESTAURANT_NAME}* 🍽️"
        )
        twilio_client.messages.create(from_=TWILIO_WHATSAPP_NUMBER, to=number, body=msg)
        logging.info(f"📩 Delivery confirmation sent to {number}")
        # quick redirect
        return """
        <script>
          alert('Commande marquée livrée ✅');
          window.location.href = '/admin?' + new Date().getTime();
        </script>
        """
    except Exception as e:
        logging.error(f"❌ Update/delivery error: {e}")
        return f"<h3>Error: {e}</h3>", 500

# ---------- Health / Home ----------
@app.route("/")
def home():
    return "RestoBot is running ✅", 200

@app.route("/health")
def health():
    return {"ok": True}, 200

if __name__ == "__main__":
    # For Render, keep this simple run; Render sets PORT. Locally we use 5000.
    port = int(os.getenv("PORT", "5000"))
    app.run(host="0.0.0.0", port=port)
