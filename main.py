from flask import Flask, request, render_template, redirect, url_for, session
from twilio.twiml.messaging_response import MessagingResponse
from twilio.rest import Client as TwilioClient
from supabase import create_client, Client as SupabaseClient
from datetime import datetime
import os, logging

# -------------------------------------------------------------
# 1️⃣ CONFIGURATION
# -------------------------------------------------------------
app = Flask(__name__)
app.secret_key = os.getenv("SESSION_SECRET", "supersecret")

app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=True
)

# Logging setup
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logging.info("🚀 Starting RestoBot v3.1 – Architect Edition")

# Env vars
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
TWILIO_SID = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_AUTH = os.getenv("TWILIO_AUTH_TOKEN")
TWILIO_WHATSAPP_NUMBER = os.getenv("TWILIO_WHATSAPP_NUMBER", "whatsapp:+14155238886")

ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "mamamia")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "1234")
RESTAURANT_NAME = os.getenv("RESTAURANT_NAME", "Mama Mia Restaurant")
BRAND_NAME = os.getenv("BRAND_NAME", "RestoBot")

# Initialize clients
supabase: SupabaseClient = create_client(SUPABASE_URL, SUPABASE_KEY)
twilio_client = TwilioClient(TWILIO_SID, TWILIO_AUTH)

# -------------------------------------------------------------
# 2️⃣ UTILITIES
# -------------------------------------------------------------
def normalize_number(number: str) -> str:
    """Ensure all phone numbers follow 'whatsapp:+countrycode...' format."""
    if not number:
        return ""
    number = number.strip()
    if not number.startswith("whatsapp:"):
        if not number.startswith("+"):
            number = f"+{number}"
        number = f"whatsapp:{number}"
    return number


def format_cart(orders):
    """Format cart summary for WhatsApp messages."""
    lines, total = [], 0
    for item in orders:
        qty, name, price = item["qty"], item["dish"], item["price"]
        subtotal = qty * price
        total += subtotal
        lines.append(f"{qty}× {name} → {subtotal:,} CDF")
    lines.append(f"\n💰 *Total : {total:,} CDF*")
    return "\n".join(lines), total


def save_order_to_supabase(number, orders, address):
    """Insert an order safely in Supabase."""
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
        res = supabase.table("orders").insert(payload).execute()
        order_id = res.data[0]["id"]
        logging.info(f"🧾 Order saved | ID:{order_id} | {number} | {items_summary} | {total:,} CDF")
        return total, order_id
    except Exception as e:
        logging.error(f"❌ Error saving order: {e}")
        return None, None


# -------------------------------------------------------------
# 3️⃣ GLOBAL STATE (per session)
# -------------------------------------------------------------
user_state = {}

menu = {
    "1": ("Riz au poisson", 6000),
    "2": ("Poulet braisé", 8000),
    "3": ("Frites", 5000),
    "4": ("Jus naturel", 2500),
}


# -------------------------------------------------------------
# 4️⃣ FLASK ROUTES
# -------------------------------------------------------------

@app.before_request
def protect_admin():
    """Require login for admin and update endpoints."""
    if request.path.startswith("/admin") or request.path.startswith("/update_status"):
        if not session.get("logged_in"):
            return redirect(url_for("login"))


@app.route("/")
def home():
    return f"🍽️ {RESTAURANT_NAME} v3.1 Pro Hybrid running ✅"


# -------------------- LOGIN ------------------------
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        password = (request.form.get("password") or "").strip()
        if username == ADMIN_USERNAME and password == ADMIN_PASSWORD:
            session["logged_in"] = True
            logging.info("✅ Admin login OK")
            return redirect(url_for("admin"))
        else:
            logging.warning("⚠️ Admin login failed")
            return render_template("login.html", error="Identifiants invalides.")
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# -------------------- ADMIN DASHBOARD ------------------------
@app.route("/admin")
def admin():
    try:
        data = supabase.table("orders").select("*").order("id", desc=True).execute().data
        return render_template("dashboard.html", orders=data, brand=BRAND_NAME, rname=RESTAURANT_NAME)
    except Exception as e:
        logging.error(f"❌ Dashboard error: {e}")
        return "<h3>Erreur de chargement du tableau de bord.</h3>", 500


# -------------------- UPDATE STATUS ------------------------
@app.route("/update_status", methods=["POST"])
def update_status():
    order_id = request.form.get("order_id")
    try:
        # Update delivery status
        supabase.table("orders").update(
            {"status": "delivered", "confirmed_by": "admin"}
        ).eq("id", order_id).execute()

        # Fetch order info
        order = (
            supabase.table("orders")
            .select("number, items, total")
            .eq("id", order_id)
            .single()
            .execute()
            .data
        )

        msg = (
            f"✅ *Commande livrée !*\n\n"
            f"Vos plats : {order['items']}\n"
            f"Montant total : {int(order['total']):,} CDF\n\n"
            f"Merci d’avoir commandé chez *{RESTAURANT_NAME}* 🍽️"
        )
        twilio_client.messages.create(from_=TWILIO_WHATSAPP_NUMBER, to=order["number"], body=msg)
        logging.info(f"📩 Confirmation envoyée à {order['number']}")

        return redirect(url_for("admin"), code=303)
    except Exception as e:
        logging.error(f"❌ Error updating order: {e}")
        return f"<h3>Erreur : {e}</h3>", 500


# -------------------- WHATSAPP WEBHOOK ------------------------
@app.route("/webhook", methods=["POST"])
def webhook():
    from_number = normalize_number(request.form.get("From"))
    msg = (request.form.get("Body") or "").strip()
    resp = MessagingResponse()
    reply = resp.message()

    # ---- CLIENT CONFIRMATION ----
    if msg.lower() in ["1", "livré", "livree", "delivered"]:
        try:
            query = (
                supabase.table("orders")
                .select("*")
                .eq("number", from_number)
                .eq("status", "pending")
                .order("id", desc=True)
                .limit(1)
                .execute()
            )
            if query.data:
                order_id = query.data[0]["id"]
                supabase.table("orders").update(
                    {"status": "delivered", "confirmed_by": "client"}
                ).eq("id", order_id).execute()
                reply.body("✅ Merci ! Votre commande est confirmée comme livrée. Bon appétit 🍽️")
                logging.info(f"🤖 Client confirmed delivery | {from_number} | order_id={order_id}")
            else:
                reply.body("ℹ️ Aucune commande en attente trouvée ou déjà livrée.")
                logging.info(f"ℹ️ Client tried to confirm but no pending order: {from_number}")
        except Exception as e:
            logging.error(f"❌ Error client confirmation: {e}")
            reply.body("⚠️ Erreur interne, veuillez réessayer plus tard.")
        return str(resp)

    # ---- MENU LOGIC ----
    if from_number not in user_state:
        user_state[from_number] = {"stage": "main", "orders": [], "dish": None}

    state = user_state[from_number]

    # Main menu
    if state["stage"] == "main":
        if msg == "1":
            menu_text = "\n".join([f"{k}️⃣ {v[0]} – {v[1]:,} CDF" for k, v in menu.items()])
            reply.body(f"🍽 *Menu du jour – {RESTAURANT_NAME}*\n{menu_text}\n\nTapez 2️⃣ pour commander.")
        elif msg == "2":
            menu_text = "\n".join([f"{k}️⃣ {v[0]}" for k, v in menu.items()])
            reply.body(f"Quel plat souhaitez-vous commander ?\n{menu_text}")
            state["stage"] = "choose_dish"
        elif msg == "3":
            reply.body("🕐 11h – 22h tous les jours\n📍 Kintambo Magasin\n📞 +243 000 000 000")
        else:
            reply.body(f"👋 Bienvenue chez *{RESTAURANT_NAME}* !\n1️⃣ Menu\n2️⃣ Commander\n3️⃣ Nos horaires")
        return str(resp)

    # Choose dish
    if state["stage"] == "choose_dish":
        if msg in menu:
            dish_name, price = menu[msg]
            state["dish"] = (dish_name, price)
            reply.body(f"Combien de *{dish_name}* souhaitez-vous ? (ex: 2)")
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
            reply.body("Souhaitez-vous ajouter un autre plat ?\n1️⃣ Oui\n2️⃣ Non")
            state["stage"] = "add_more"
        else:
            reply.body("Merci d’entrer une quantité valide (ex : 2).")
        return str(resp)

    # Add more
    if state["stage"] == "add_more":
        if msg == "1":
            menu_text = "\n".join([f"{k}️⃣ {v[0]}" for k, v in menu.items()])
            reply.body(f"Quel plat souhaitez-vous ajouter ?\n{menu_text}")
            state["stage"] = "choose_dish"
        elif msg == "2":
            reply.body("Veuillez envoyer votre *nom et adresse complète* (ex : Nom – Quartier, Avenue...)")
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
                    f"Quand vous la recevrez, tapez *1* pour confirmer la livraison."
                )
            else:
                reply.body("❌ Erreur d’enregistrement, réessayez plus tard.")
            user_state[from_number] = {"stage": "main", "orders": [], "dish": None}
        elif msg == "2":
            reply.body("Quel plat souhaitez-vous modifier ?\n" +
                       "\n".join([f"{k}️⃣ {v[0]}" for k, v in menu.items()]))
            state["orders"] = []
            state["stage"] = "choose_dish"
        else:
            reply.body("Répondez 1 (Oui) ou 2 (Modifier).")
        return str(resp)

    return str(resp)


# -------------------------------------------------------------
# 5️⃣ APP START
# -------------------------------------------------------------
if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
