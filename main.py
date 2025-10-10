
from flask import Flask, request
from twilio.twiml.messaging_response import MessagingResponse
import csv
from datetime import datetime

app = Flask(__name__)

# --- Menu du jour ---
menu = {
    "1": ("Riz au poisson", 6000),
    "2": ("Poulet braisé", 8000),
    "3": ("Frites", 5000),
    "4": ("Jus naturel", 2500)
}

# --- États utilisateurs ---
user_state = {}


def format_cart(orders):
    """Crée un texte lisible du panier et calcule le total."""
    lines = []
    total = 0
    for item in orders:
        name, qty, price = item["dish"], item["qty"], item["price"]
        subtotal = qty * price
        total += subtotal
        lines.append(f"{qty}× {name} → {subtotal:,} CDF")
    lines.append(f"\n💰 *Total : {total:,} CDF*")
    return "\n".join(lines), total


def save_order_to_csv(number, orders, address):
    """Sauvegarde une commande dans le fichier CSV."""
    cart_text, total = format_cart(orders)
    dishes_summary = ", ".join([f"{o['qty']}x {o['dish']}" for o in orders])
    with open("orders.csv", "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"), number,
            dishes_summary, total, address
        ])


@app.route("/webhook", methods=["POST"])
def webhook():
    from_number = request.form.get("From")
    msg = request.form.get("Body", "").strip()
    resp = MessagingResponse()
    reply = resp.message()

    # Init état utilisateur
    if from_number not in user_state:
        user_state[from_number] = {"stage": "main", "orders": [], "dish": None}

    state = user_state[from_number]

    # --- Menu principal ---
    if state["stage"] == "main":
        if msg == "1":
            menu_text = "\n".join(
                [f"{k}️⃣ {v[0]} – {v[1]:,} CDF" for k, v in menu.items()])
            reply.body(
                f"🍽 *Menu du jour*\n{menu_text}\n\nTapez 2️⃣ pour commander ou 3️⃣ pour nos horaires."
            )
        elif msg == "2":
            menu_text = "\n".join([f"{k}️⃣ {v[0]}" for k, v in menu.items()])
            reply.body(
                f"Quel plat souhaitez-vous commander ?\n{menu_text}\nTapez le numéro du plat."
            )
            state["stage"] = "choose_dish"
        elif msg == "3":
            reply.body(
                "🕐 11 h – 22 h tous les jours\n📍 Kintambo Magasin\n📞 +243 000 000 000"
            )
        else:
            reply.body(
                "👋 Bienvenue chez *Mama Mia Restaurant !*\nTapez :\n1️⃣ Menu \n2️⃣ Commander \n3️⃣ Nos horaires"
            )
        return str(resp)

    # --- Choix du plat ---
    if state["stage"] == "choose_dish":
        if msg in menu:
            dish_name, price = menu[msg]
            state["dish"] = (dish_name, price)
            reply.body(
                f"Combien de *{dish_name}* souhaitez-vous ?\n(Tapez un nombre, ex : 2)"
            )
            state["stage"] = "choose_quantity"
        else:
            reply.body("Choix invalide. Tapez un numéro du menu.")
        return str(resp)

    # --- Quantité ---
    if state["stage"] == "choose_quantity":
        if msg.isdigit() and int(msg) > 0:
            qty = int(msg)
            dish_name, price = state["dish"]
            state["orders"].append({
                "dish": dish_name,
                "qty": qty,
                "price": price
            })
            reply.body(
                "Souhaitez-vous ajouter un autre plat ?\n1️⃣ Oui \n2️⃣ Non (continuer)"
            )
            state["stage"] = "add_more"
        else:
            reply.body("Merci d’entrer une quantité valide (ex : 2).")
        return str(resp)

    # --- Ajouter un autre plat ? ---
    if state["stage"] == "add_more":
        if msg == "1":
            menu_text = "\n".join([f"{k}️⃣ {v[0]}" for k, v in menu.items()])
            reply.body(f"Quel plat souhaitez-vous ajouter ?\n{menu_text}")
            state["stage"] = "choose_dish"
        elif msg == "2":
            reply.body(
                "Veuillez maintenant envoyer votre *nom et adresse complète* (ex : Nom Prénom – Quartier, Avenue...)."
            )
            state["stage"] = "waiting_address"
        else:
            reply.body("Répondez 1 (oui) ou 2 (non).")
        return str(resp)

    # --- Adresse + résumé ---
    if state["stage"] == "waiting_address":
        state["address"] = msg
        cart_text, total = format_cart(state["orders"])
        reply.body(f"✅ *Résumé de votre commande :*\n\n{cart_text}\n\n"
                   f"🏠 Adresse : {state['address']}\n"
                   f"Confirmez-vous ?\n1️⃣ Oui\n2️⃣ Modifier")
        state["stage"] = "confirm_order"
        return str(resp)

    # --- Confirmation finale ---
    if state["stage"] == "confirm_order":
        if msg == "1":
            save_order_to_csv(from_number, state["orders"], state["address"])
            reply.body(
                "Merci ! Votre commande est confirmée 🍽🚗\nTapez 0 pour revenir au menu principal."
            )
            # reset
            user_state[from_number] = {
                "stage": "main",
                "orders": [],
                "dish": None
            }
        elif msg == "2":
            reply.body("Pas de souci ! Quel plat souhaitez-vous modifier ?\n" +
                       "\n".join([f"{k}️⃣ {v[0]}" for k, v in menu.items()]))
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
