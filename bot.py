import os
import json
import gspread
from google.oauth2.service_account import Credentials
import anthropic
from flask import Flask, request, jsonify
import requests

app = Flask(__name__)

# ==================== إعدادات ====================
SHEET_ID = "1xsNhw8tS0_EbOHJtIHn-3evNAjiqIuxo"

WHATSAPP_TOKEN = os.environ.get("WHATSAPP_TOKEN", "")
WHATSAPP_PHONE_ID = os.environ.get("WHATSAPP_PHONE_ID", "")
VERIFY_TOKEN = os.environ.get("VERIFY_TOKEN", "my_verify_token_123")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
ADMIN_NUMBERS = os.environ.get("ADMIN_NUMBERS", "").split(",")

# ==================== قراءة الشيت ====================
def get_prices_from_sheet():
    try:
        creds_json = os.environ.get("GOOGLE_CREDENTIALS", "")
        if not creds_json:
            print("❌ GOOGLE_CREDENTIALS مش موجود")
            return {}
        
        creds_dict = json.loads(creds_json)
        scopes = ['https://www.googleapis.com/auth/spreadsheets.readonly']
        creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)
        gc = gspread.authorize(creds)
        sh = gc.open_by_key(SHEET_ID)
        
        prices_data = {}
        for worksheet in sh.worksheets():
            if worksheet.title == "إعدادات البوت":
                continue
            data = worksheet.get_all_records()
            prices_data[worksheet.title] = data
        
        return prices_data
    except Exception as e:
        print(f"خطأ في قراءة الشيت: {e}")
        return {}

def format_prices_for_ai(prices_data):
    text = "قائمة الأسعار المتاحة:\n\n"
    for sheet_name, rows in prices_data.items():
        text += f"=== {sheet_name} ===\n"
        for row in rows:
            if not any(row.values()):
                continue
            text += " | ".join([f"{k}: {v}" for k, v in row.items() if v]) + "\n"
        text += "\n"
    return text

# ==================== AI ====================
def get_ai_response(user_message, prices_text):
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    
    system_prompt = f"""أنت موظف خدمة عملاء في مركز صيانة موبايلات في مصر.
بتردد على أسئلة محلات الموبايلات اللي بتسأل عن أسعار قطع الغيار والإصلاح.

قواعد مهمة جداً:
1. رد بالعامية المصرية الطبيعية دايماً
2. لو سألوا عن أكتر من حاجة، ردود مرتبة ومنظمة بنقاط
3. لو السعر مش موجود في القايمة، قول "مش متاح دلوقتي"
4. متبعتش قوايم أسعار كاملة حتى لو طلبوا - قولهم "تقدر تسأل على المنتج اللي محتاجه"
5. لو السؤال مش عن أسعار أو صيانة، قولهم "ده مش في تخصصي"
6. الردود تكون مختصرة ومفيدة
7. استخدم إيموجي بشكل طبيعي مش مبالغ
8. لو السؤال مش واضح، اسأل توضيح بشكل ودي
9. لو مش لاقي إجابة للسؤال، رد بالكلمة بالظبط: NEED_HUMAN

قائمة الأسعار عندك:
{prices_text}"""

    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=500,
        system=system_prompt,
        messages=[{"role": "user", "content": user_message}]
    )
    
    return message.content[0].text

# ==================== WhatsApp ====================
def send_whatsapp_message(to, message):
    url = f"https://graph.facebook.com/v18.0/{WHATSAPP_PHONE_ID}/messages"
    headers = {
        "Authorization": f"Bearer {WHATSAPP_TOKEN}",
        "Content-Type": "application/json"
    }
    data = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "text",
        "text": {"body": message}
    }
    response = requests.post(url, headers=headers, json=data)
    print(f"WhatsApp response: {response.json()}")
    return response.json()

def mention_admins(sender, original_question, sender_name):
    mention_text = f"⚠️ سؤال محتاج مساعدة:\n\nالسؤال: {original_question}\nمن: {sender_name}\n\n"
    for admin in ADMIN_NUMBERS:
        if admin:
            mention_text += f"@{admin} "
    send_whatsapp_message(sender, mention_text)

# ==================== Webhook ====================
@app.route("/webhook", methods=["GET"])
def verify_webhook():
    mode = request.args.get("hub.mode")
    token = request.args.get("hub.verify_token")
    challenge = request.args.get("hub.challenge")
    
    if mode == "subscribe" and token == VERIFY_TOKEN:
        print("✅ Webhook verified!")
        return challenge, 200
    return "Forbidden", 403

@app.route("/webhook", methods=["POST"])
def handle_webhook():
    data = request.json
    
    try:
        entry = data["entry"][0]
        changes = entry["changes"][0]
        value = changes["value"]
        
        if "messages" not in value:
            return jsonify({"status": "ok"})
        
        message = value["messages"][0]
        sender = message["from"]
        
        if message["type"] != "text":
            return jsonify({"status": "ok"})
        
        user_text = message["text"]["body"]
        contacts = value.get("contacts", [{}])
        sender_name = contacts[0].get("profile", {}).get("name", sender) if contacts else sender
        
        print(f"📨 رسالة من {sender_name}: {user_text}")
        
        prices_data = get_prices_from_sheet()
        prices_text = format_prices_for_ai(prices_data)
        ai_response = get_ai_response(user_text, prices_text)
        
        if "NEED_HUMAN" in ai_response:
            send_whatsapp_message(sender, "⏳ سؤالك محتاج متخصص — بنعمل منشن للمسؤولين دلوقتي!")
            mention_admins(sender, user_text, sender_name)
        else:
            send_whatsapp_message(sender, ai_response)
        
        return jsonify({"status": "ok"})
    
    except Exception as e:
        print(f"❌ خطأ: {e}")
        return jsonify({"status": "error", "message": str(e)})

@app.route("/", methods=["GET"])
def home():
    return "✅ البوت شغال!"

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
