from flask import Flask, request
from twilio.twiml.messaging_response import MessagingResponse
from supabase import create_client, Client
from datetime import datetime
import os

app = Flask(__name__)

# --- Supabase config ---
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# --- Menu du jour ---
menu = {
    "1": ("Riz au poisson", 6000),
    "2": ("Poulet braisé", 8000),
    "3": ("Frites", 5000),
    "4": ("Jus naturel", 2500)
}

# --- États utilisateurs ---
user_state = {}

# --- Fonction panier ---
def format_cart(orders):
    lines, total = [], 0
    for item in orders:
        name, qty, price = item["dish"], item["qty"], item["price"]
        subtotal = qty * price
        total += subtotal
        lines.append(f"{qty}× {name} → {subtotal:,} CDF")
    lines.append(f"\n💰 *Total : {total:,} CDF*")
    return "\n".join(lines), total

# --- Sauvegarde dans Supabase ---
def save_order_to_supabase(number, orders, address):
    try:
        items_summary = ", ".join([f"{o['qty']}x {o['dish']}" for o in orders])
        total = sum(o["qty"] * o["price"] for o in orders)
        data = {
            "date": datetime.now().isoformat(),
            "number": number,
            "items": items_summary,
            "total": total,
            "address": address,
            "status": "pending"
        }
        result = supabase.table("orders").insert(data).execute()
        print(f"✅ Order saved to Supabase: {result.data}")
        return total, result.data[0]["id"]
    except Exception as e:
        print(f"❌ Error saving to Supabase: {e}")
        return None, None

# --- Webhook principal ---
@app.route("/webhook", methods=["POST"])
def webhook():
    from_number = request.form.get("From")
    msg = request.form.get("Body", "").strip()
    resp = MessagingResponse()
    reply = resp.message()

    if from_number not in user_state:
        user_state[from_number] = {"stage": "main", "orders": [], "dish": None}

    state = user_state[from_number]

    # --- Menu principal ---
    if state["stage"] == "main":
        if msg == "1":
            menu_text = "\n".join([f"{k}️⃣ {v[0]} – {v[1]:,} CDF" for k, v in menu.items()])
            reply.body(f"🍽 *Menu du jour*\n{menu_text}\n\nTapez 2️⃣ pour commander ou 3️⃣ pour nos horaires.")
        elif msg == "2":
            menu_text = "\n".join([f"{k}️⃣ {v[0]}" for k, v in menu.items()])
            reply.body(f"Quel plat souhaitez-vous commander ?\n{menu_text}\nTapez le numéro du plat.")
            state["stage"] = "choose_dish"
        elif msg == "3":
            reply.body("🕐 11 h – 22 h tous les jours\n📍 Kintambo Magasin\n📞 +243 000 000 000")
        else:
            reply.body("👋 Bienvenue chez *Mama Mia Restaurant !*\nTapez :\n1️⃣ Menu\n2️⃣ Commander\n3️⃣ Nos horaires")
        return str(resp)

    # --- Choix du plat ---
    if state["stage"] == "choose_dish":
        if msg in menu:
            dish_name, price = menu[msg]
            state["dish"] = (dish_name, price)
            reply.body(f"Combien de *{dish_name}* souhaitez-vous ? (Tapez un nombre, ex : 2)")
            state["stage"] = "choose_quantity"
        else:
            reply.body("Choix invalide. Tapez un numéro du menu.")
        return str(resp)

    # --- Quantité ---
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

    # --- Ajouter un autre plat ---
    if state["stage"] == "add_more":
        if msg == "1":
            menu_text = "\n".join([f"{k}️⃣ {v[0]}" for k, v in menu.items()])
            reply.body(f"Quel plat souhaitez-vous ajouter ?\n{menu_text}")
            state["stage"] = "choose_dish"
        elif msg == "2":
            reply.body("Veuillez maintenant envoyer votre *nom et adresse complète* (ex : Nom Prénom – Quartier, Avenue...).")
            state["stage"] = "waiting_address"
        else:
            reply.body("Répondez 1 (oui) ou 2 (non).")
        return str(resp)

    # --- Adresse + résumé ---
    if state["stage"] == "waiting_address":
        state["address"] = msg
        cart_text, total = format_cart(state["orders"])
        reply.body(f"✅ *Résumé de votre commande :*\n\n{cart_text}\n\n🏠 Adresse : {state['address']}\nConfirmez-vous ?\n1️⃣ Oui\n2️⃣ Modifier")
        state["stage"] = "confirm_order"
        return str(resp)

    # --- Confirmation finale ---
    if state["stage"] == "confirm_order":
        if msg == "1":
            print(f"📝 Sauvegarde de la commande pour {from_number}...")
            total, order_id = save_order_to_supabase(from_number, state["orders"], state["address"])
            if order_id:
                reply.body(f"✅ *Commande n°{order_id} enregistrée !*\n💰 Total : {total:,} CDF\n🚗 Livraison en préparation.\n\nMerci pour votre commande 🙏")
            else:
                reply.body("❌ Une erreur est survenue lors de l’enregistrement. Réessayez plus tard.")
            user_state[from_number] = {"stage": "main", "orders": [], "dish": None}
        elif msg == "2":
            reply.body("Pas de souci ! Quel plat souhaitez-vous modifier ?\n" + "\n".join([f"{k}️⃣ {v[0]}" for k, v in menu.items()]))
            state["orders"] = []
            state["stage"] = "choose_dish"
        else:
            reply.body("Répondez 1 (Oui) ou 2 (Modifier).")
        return str(resp)

    return str(resp)

@app.route("/")
def home():
    return "Bot is running ✅", 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
