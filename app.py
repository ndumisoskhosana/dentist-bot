import os
import json  # <--- NEW: To handle file storage
from flask import Flask, request
from twilio.twiml.messaging_response import MessagingResponse
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)

# --- CONFIGURATION ---
client = OpenAI(
    api_key=os.environ.get("GROQ_API_KEY"),
    base_url="https://api.groq.com/openai/v1"
)

SYSTEM_PROMPT = """
You are 'Thandi', the receptionist for Dr. Molefe Dental (Sandton).
1. Prices: Cleaning R850, Consultation R500.
2. Medical Aid: ONLY Discovery KeyCare and Momentum.
3. Keep answers under 50 words.
"""

# --- DATABASE FUNCTIONS (File I/O) ---
DB_FILE = "memory.json"

def get_memory():
    """Reads the chat history from the hard drive."""
    if not os.path.exists(DB_FILE):
        return {}  # Return empty dict if file doesn't exist yet
    try:
        with open(DB_FILE, 'r') as f:
            return json.load(f)
    except:
        return {} # Handle empty/corrupt files

def save_memory(memory_dict):
    """Writes the chat history to the hard drive."""
    with open(DB_FILE, 'w') as f:
        json.dump(memory_dict, f, indent=2)

@app.route("/webhook", methods=['POST'])
def whatsapp_reply():
    sender_id = request.form.get('From')
    user_msg = request.form.get('Body')
    
    print(f"DEBUG: {sender_id} says: {user_msg}")

    # 1. LOAD: Fetch history from the file
    threads = get_memory()
    
    # 2. CLEAR COMMAND (Optional)
    if user_msg.lower().strip() == "reset":
        threads[sender_id] = []
        save_memory(threads)
        resp = MessagingResponse()
        resp.message("Memory cleared!")
        return str(resp)

    # 3. Get User's History
    user_history = threads.get(sender_id, [])
    user_history.append({"role": "user", "content": user_msg})

    # 4. Generate Reply
    messages_payload = [{"role": "system", "content": SYSTEM_PROMPT}] + user_history
    
    try:
        response = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=messages_payload
        )
        bot_reply = response.choices[0].message.content
        
        # 5. SAVE: Update history and write back to file
        user_history.append({"role": "assistant", "content": bot_reply})
        threads[sender_id] = user_history
        save_memory(threads)  # <--- CRITICAL: Saves to disk

    except Exception as e:
        print(f"Error: {e}")
        bot_reply = "Sorry, my brain is offline."

    resp = MessagingResponse()
    resp.message(bot_reply)
    return str(resp)

if __name__ == "__main__":
    app.run(debug=True, port=5000)