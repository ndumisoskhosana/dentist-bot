import os
import json
import datetime
from flask import Flask, request
from twilio.twiml.messaging_response import MessagingResponse
from twilio.rest import Client as TwilioClient
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)

# --- API CLIENTS ---
groq_client = OpenAI(
    api_key=os.environ.get("GROQ_API_KEY"),
    base_url="https://api.groq.com/openai/v1"
)

# Only needed if you want the SMS alert feature working
twilio_sms_client = None
try:
    twilio_sms_client = TwilioClient(
        os.environ.get("TWILIO_ACCOUNT_SID"),
        os.environ.get("TWILIO_AUTH_TOKEN")
    )
except Exception as e:
    print(f"Warning: Twilio SMS not set up. {e}")

# --- CONFIGURATION ---
# For the demo, put YOUR number here so YOU get the alert!
DOCTOR_PHONE = os.environ.get("DOCTOR_PHONE") 
DB_FILE = "memory.json"
CALENDAR_FILE = "calendar.json"
REVENUE_FILE = "missed_revenue.txt"

# --- THE BRAIN (JK THE DENTIST) ---
BASE_SYSTEM_PROMPT = """
You are 'Jay', the AI concierge for **JK The Dentist** in Parktown North.
The practice is run by **Dr. Jan van Schalkwyk** (Dr. Jan).
Your goal is to be professional, warm, and efficient.

**CLINIC DETAILS:**
- **Location:** 11 12th Ave, Parktown North.
- **Hours:** Mon-Thu 08:00-17:00, Fri 08:00-16:00.
- **Vibe:** Tranquil, modern, high-tech (we do "Same-Day Crowns" and Laser Dentistry).

**SERVICES & PRICING GUIDES (Estimates):**
- **Consultation:** R850 - R1,200 (includes basic x-rays).
- **Cleaning (Oral Hygiene):** ~R950 (Ask if they want to see Fhatu, our hygienist).
- **Teeth Whitening:** We use the Pola Professional Laser system (In-chair).
- **Invisalign:** We are certified providers. Requires a consult first.
- **Emergency:** If they are in pain, prioritize them!

**MEDICAL AID POLICY:**
- We are a private practice. Patients usually pay upfront and claim back from their medical aid.
- We DO NOT accept Discovery KeyCare or basic network plans directly.

**RULES:**
1. **Escalation:** If the user asks for a human, is angry, or has a medical emergency (swelling/bleeding), output strictly: ACTION_ESCALATE
2. **Booking:** If user confirms a slot, output: ACTION_BOOK: Day|Time
3. **Full:** If no slots are open, output: ACTION_LOG_MISSED
4. **EMERGENCY / HUMAN HANDOFF:** If the user seems angry, asks for a human, or describes a medical emergency (pain, swelling, bleeding), 
   you MUST end your reply with the hidden tag: ACTION_ESCALATE
5. **Tone:** Use South African warmth. Keep replies short for WhatsApp.
"""

# --- HELPER FUNCTIONS ---
# CORRECT VERSION
def get_memory():
    if not os.path.exists(DB_FILE):
        return {}
    try:
        # Press Enter here! The 'with' must be on its own line.
        with open(DB_FILE, 'r') as f:
            return json.load(f)
    except:
        return {}

def save_memory(data):
    with open(DB_FILE, 'w') as f: json.dump(data, f, indent=2)

def get_calendar():
    if not os.path.exists(CALENDAR_FILE):
        return "No slots available."
    try:
        # Press Enter here too!
        with open(CALENDAR_FILE, 'r') as f:
            data = json.load(f)
            # Format nicely as a list
            return "\n".join([f"• *{day}*: {', '.join(slots)}" for day, slots in data.items()])
    except:
        return "Error loading schedule."

def log_missed_revenue(user_number):
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(REVENUE_FILE, "a") as f:
        f.write(f"[{timestamp}] Lost Patient: {user_number} (No Slots)\n")

def send_emergency_sms(user_msg, user_number):
    """Sends a real SMS to the doctor's phone."""
    # 1. Get variables (ensure these are in your .env)
    doctor_phone = os.environ.get("DOCTOR_PHONE")
    my_twilio_number = os.environ.get("TWILIO_PHONE_NUMBER")
    
    # 2. Safety Check: Don't crash if keys are missing
    if not twilio_sms_client or not doctor_phone or not my_twilio_number:
        print("DEBUG: Cannot send SMS. Missing keys or phone numbers in .env")
        return

    # 3. Send the Message
    try:
        message = twilio_sms_client.messages.create(
            body=f"🚨 DENTIST BOT ALERT 🚨\nPatient {user_number} needs help!\nMessage: \"{user_msg}\"",
            from_=my_twilio_number,
            to=doctor_phone
        )
        print(f"DEBUG: Emergency SMS sent! SID: {message.sid}")
    except Exception as e:
        print(f"DEBUG: SMS Failed: {e}")

@app.route("/webhook", methods=['POST'])
def whatsapp_reply():
    sender_id = request.form.get('From')
    user_msg = request.form.get('Body')
    print(f"DEBUG: {sender_id} says: {user_msg}")

    # 1. RESET COMMAND
    threads = get_memory()
    if user_msg.lower().strip() == "reset":
        threads[sender_id] = []
        save_memory(threads)
        resp = MessagingResponse()
        resp.message("Memory cleared!")
        return str(MessagingResponse().message("Memory cleared!"))

    # 2. INJECT CALENDAR DATA
    current_slots = get_calendar()
    dynamic_prompt = f"""
    {BASE_SYSTEM_PROMPT}
    --- LIVE AVAILABILITY ---
    {current_slots}
    -------------------------
    """

    # 3. GENERATE AI RESPONSE
    user_history = threads.get(sender_id, [])
    user_history.append({"role": "user", "content": user_msg})
    
    try:
        response = groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "system", "content": dynamic_prompt}] + user_history
        )
        raw_reply = response.choices[0].message.content
        final_reply = raw_reply

        # --- ACTION TRIGGERS ---
        
        # A. Human Handoff (Escalation)
        if "ACTION_ESCALATE" in raw_reply:
            send_emergency_sms(user_msg, sender_id)
            final_reply = "I've alerted Dr. Jan's team directly. Someone will contact you shortly."

        # B. Missed Revenue Logger
        elif "ACTION_LOG_MISSED" in raw_reply:
            log_missed_revenue(sender_id)
            final_reply = raw_reply.replace("ACTION_LOG_MISSED", "").strip()

        # C. Booking Handler
        elif "ACTION_BOOK:" in raw_reply:
            # We strip the command so the user doesn't see it
            final_reply = raw_reply.split("ACTION_BOOK:")[0].strip()
            # (In a real app, you would delete the slot from calendar.json here)

        # -----------------------

        user_history.append({"role": "assistant", "content": final_reply})
        threads[sender_id] = user_history
        save_memory(threads)

    except Exception as e:
        print(f"Error: {e}")
        final_reply = "Sorry, our system is currently updating. Please try again in a moment."

    resp = MessagingResponse()
    resp.message(final_reply)
    return str(resp)

if __name__ == "__main__":
    app.run(debug=True, port=5000)