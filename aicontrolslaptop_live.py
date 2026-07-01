import base64
import cv2
import json
import time
import re
import requests
import websocket
import threading
import tkinter as tk
from tkinter import ttk, messagebox
from PIL import Image, ImageTk

# ==================== CONFIGURATION ====================
USE_LOCAL_AI = False         # Set to True to use local Ollama, False for Gemini
LOCAL_MODEL = "minicpm-v4.5:8b" # Use the official base model directly!

GEMINI_API_KEY = "AQ.REDACTED"
PHONE_IP = "192.168.1.183"  # Change to your phone's IP address
VIDEO_SOURCE = 0            # 0 is usually /dev/video0 (your webcam)

# --- INITIAL IMAGE OPTIMIZATION (Can be adjusted in GUI) ---
CROP_TOP = 0.14
CROP_BOTTOM = 0.06
CROP_LEFT = 0.18
CROP_RIGHT = 0.05

MAX_AI_RESOLUTION_WIDTH = 3400
# =======================================================


DEFAULT_SYSTEM_INSTRUCTION = """


You are an autonomous AI assistant that will DOWNLOAD (not login) and install TLauncher (maybe direct link?). You are looking at a side-by-side comparison: left is PREVIOUS SCREEN (this is only for context), right is CURRENT SCREEN (the one where you see what happened).
The laptop ALWAYS WORKS, your the problem, not the laptop.
THE USER CANNOT DO ANYTHING EXCEPT START ANOTHER RESPONSE FROM YOU, SO THE USER IS ONLY TIMING YOUR MESSAGES
Output a single JSON command with a "reason" field. Valid formats:
1. Keypress: {"type": "key", "value": "enter", "reason": "..."}
   (Keys: enter, space, tab, escape, backspace, f10, shift, ctrl, alt. Use "-" for simultaneous, " " for sequential).
2. Type text: {"type": "type", "value": "text_to_type", "reason": "..."}

3. Mouse Action: {"type": "mouse", "value": "mouse_action_arguments", "reason": "..."}

MOUSE GUIDELINES (Use relative stepping to reach targets):
- Valid mouse formats:

  1. Move Relative: {"type": "mouse", "value": "move X_OFFSET Y_OFFSET", "reason": "..."}
     Use integers. (e.g., "move 127 -150" for large jumps, "move -20 15" for close fine-tuning). (values are respectively: down right, max 127 which is around 50% of screen height, try mainly to do smaller movements to not overshoot)
  2. Click: {"type": "mouse", "value": "click left", "reason": "..."} or {"type": "mouse", "value": "click right", "reason": "..."}
  3. Dragging (Hold/Release): {"type": "mouse", "value": "down left", "reason": "..."} and {"type": "mouse", "value": "up left", "reason": "..."}

CRITICAL GUIDELINES:
- FIRST, write a 1-2 sentence analysis of what changed on the screen and your next step.
- SECOND, think about what you want to do in another 2-4 sentences and look where the mouse is.
- THIRD, actually think if your mouse/keyboard commands are correct and will go where you want it in another 2-3 sentances.
- THEN, output the single JSON command at the very end of your response.

using tab is anoying and long, just know that move 127 127 goes the most down and right it can, -127 127 goes down left, -127 -127 goes up left
DONT CARE ABOUT COOKIE BANNERS

YOU CANT TAB OUT OF A WEBPAGE, YOU HAVE TO USE BROWSER SHORTCUTS FOR THAT (THE DOWNLOAD POPUP IS A BROWSER ELEMENT, NOT A WEBPAGE ELEMENT SO YOU CANT TAB TO IT)


Warning: tlauncher doesnt show which element is selected (tabbing doesnt indicate anything)
"""

def resize_for_ai(frame):
    """Resizes the frame to a lower resolution to reduce token count and VRAM usage."""
    if frame is None or MAX_AI_RESOLUTION_WIDTH is None:
        return frame
    h, w, _ = frame.shape
    if w > MAX_AI_RESOLUTION_WIDTH:
        scale = MAX_AI_RESOLUTION_WIDTH / w
        new_w = int(w * scale)
        new_h = int(h * scale)
        return cv2.resize(frame, (new_w, new_h), interpolation=cv2.INTER_AREA)
    return frame

def clean_json_response(response_text):
    """Strips thinking blocks, markdown wrappers, and extracts the raw JSON object."""
    text = response_text.strip()

    text = re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL).strip()

    if "<think>" in text:
        parts = text.split("</think>")
        if len(parts) > 1:
            text = parts[-1].strip()
        else:
            start_idx = text.find("{")
            if start_idx != -1:
                text = text[start_idx:]

    if text.startswith("```json"):
        text = text[7:]
    elif text.startswith("```"):
        text = text[3:]
    if text.endswith("```"):
        text = text[:-3]

    start_idx = text.find("{")
    end_idx = text.rfind("}")
    if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
        text = text[start_idx:end_idx+1]

    return text.strip()

class WindowsInstallerAIApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Windows Installer AI Assistant")
        self.root.geometry("1200x750")

        # State variables
        self.ws = None
        self.ws_url = f"ws://{PHONE_IP}:8765"
        self.cap = None
        self.running = True
        self.is_analyzing = False
        self.cancel_requested = False  # Flag to track cancellation requests
        self.action_history = []  # Stores history of executed actions

        # Screen history for automatic comparison
        self.screen_history = []  # Stores past optimized frames

        # Interactive Crop Variables (bound to GUI sliders)
        self.crop_top = tk.DoubleVar(value=CROP_TOP)
        self.crop_bottom = tk.DoubleVar(value=CROP_BOTTOM)
        self.crop_left = tk.DoubleVar(value=CROP_LEFT)
        self.crop_right = tk.DoubleVar(value=CROP_RIGHT)

        # Build UI Layout
        self.create_widgets()

        # Initialize Webcam
        self.init_webcam()

        # Start WebSocket monitor loop in a background thread
        threading.Thread(target=self.websocket_monitor_loop, daemon=True).start()

        # Start the webcam frame update loop
        self.update_webcam_loop()

        # Handle window close event
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

    def create_widgets(self):
        paned_window = ttk.PanedWindow(self.root, orient=tk.HORIZONTAL)
        paned_window.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        # Left Frame: Webcam Feed
        left_frame = ttk.LabelFrame(paned_window, text=" Live Webcam Feed (What the AI Sees) ")
        paned_window.add(left_frame, weight=3)

        self.webcam_label = ttk.Label(left_frame, anchor="center")
        self.webcam_label.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        # Right Frame: Controls & Status
        right_frame = ttk.Frame(paned_window)
        paned_window.add(right_frame, weight=2)

        # Status Panel
        status_frame = ttk.LabelFrame(right_frame, text=" Status Indicators ")
        status_frame.pack(fill=tk.X, pady=(0, 10))

        self.ws_status_label = ttk.Label(status_frame, text="WebSocket: Disconnected", font=("Arial", 10, "bold"), foreground="red")
        self.ws_status_label.pack(anchor="w", padx=10, pady=5)

        self.ai_status_label = ttk.Label(status_frame, text="AI Status: Idle", font=("Arial", 10))
        self.ai_status_label.pack(anchor="w", padx=10, pady=5)

        # Notebook for Instructions, History, and Crop Settings
        self.notebook = ttk.Notebook(right_frame)
        self.notebook.pack(fill=tk.BOTH, expand=True, pady=(0, 10))

        # Tab 1: Goal / System Instruction Panel
        tab_instructions = ttk.Frame(self.notebook)
        self.notebook.add(tab_instructions, text=" Goal / Instructions ")

        self.instruction_text = tk.Text(tab_instructions, wrap=tk.WORD, font=("Consolas", 9))
        self.instruction_text.insert(tk.END, DEFAULT_SYSTEM_INSTRUCTION)
        self.instruction_text.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        # Tab 2: Action History Panel
        tab_history = ttk.Frame(self.notebook)
        self.notebook.add(tab_history, text=" Action History ")

        self.history_text = tk.Text(tab_history, wrap=tk.WORD, font=("Consolas", 9), state=tk.DISABLED)
        self.history_text.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        clear_btn = ttk.Button(tab_history, text="Clear History", command=self.clear_history)
        clear_btn.pack(fill=tk.X, padx=5, pady=(0, 5))

        # Tab 3: Interactive Crop Settings Panel
        tab_crop = ttk.Frame(self.notebook)
        self.notebook.add(tab_crop, text=" Crop Settings ")

        # Top Crop Slider
        self.lbl_top = ttk.Label(tab_crop, text=f"Top Crop: {int(self.crop_top.get() * 100)}%")
        self.lbl_top.pack(anchor="w", padx=10, pady=(10, 2))
        scale_top = ttk.Scale(tab_crop, from_=0.0, to=0.45, variable=self.crop_top, command=self.on_crop_change)
        scale_top.pack(fill=tk.X, padx=10, pady=(0, 10))

        # Bottom Crop Slider
        self.lbl_bottom = ttk.Label(tab_crop, text=f"Bottom Crop: {int(self.crop_bottom.get() * 100)}%")
        self.lbl_bottom.pack(anchor="w", padx=10, pady=(10, 2))
        scale_bottom = ttk.Scale(tab_crop, from_=0.0, to=0.45, variable=self.crop_bottom, command=self.on_crop_change)
        scale_bottom.pack(fill=tk.X, padx=10, pady=(0, 10))

        # Left Crop Slider
        self.lbl_left = ttk.Label(tab_crop, text=f"Left Crop: {int(self.crop_left.get() * 100)}%")
        self.lbl_left.pack(anchor="w", padx=10, pady=(10, 2))
        scale_left = ttk.Scale(tab_crop, from_=0.0, to=0.45, variable=self.crop_left, command=self.on_crop_change)
        scale_left.pack(fill=tk.X, padx=10, pady=(0, 10))

        # Right Crop Slider
        self.lbl_right = ttk.Label(tab_crop, text=f"Right Crop: {int(self.crop_right.get() * 100)}%")
        self.lbl_right.pack(anchor="w", padx=10, pady=(10, 2))
        scale_right = ttk.Scale(tab_crop, from_=0.0, to=0.45, variable=self.crop_right, command=self.on_crop_change)
        scale_right.pack(fill=tk.X, padx=10, pady=(0, 10))

        # Reset Crop Button
        reset_btn = ttk.Button(tab_crop, text="Reset Crop", command=self.reset_crop)
        reset_btn.pack(fill=tk.X, padx=10, pady=10)

        # Button Frame (Holds Trigger and Cancel side-by-side)
        btn_frame = ttk.Frame(right_frame)
        btn_frame.pack(fill=tk.X, pady=(0, 10))

        # Trigger Button
        self.trigger_btn = ttk.Button(btn_frame, text="Trigger AI Analysis", command=self.trigger_ai)
        self.trigger_btn.pack(side=tk.LEFT, fill=tk.X, expand=True, ipady=10, padx=(0, 5))

        # Cancel Button
        self.cancel_btn = ttk.Button(btn_frame, text="Cancel", command=self.cancel_ai, state=tk.DISABLED)
        self.cancel_btn.pack(side=tk.RIGHT, fill=tk.X, expand=True, ipady=10, padx=(5, 0))

        # AI Response Panel
        response_frame = ttk.LabelFrame(right_frame, text=" Last AI Response (JSON Command) ")
        response_frame.pack(fill=tk.BOTH, expand=True)

        self.response_text = tk.Text(response_frame, wrap=tk.WORD, height=8, font=("Consolas", 10), state=tk.DISABLED)
        self.response_text.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

    def init_webcam(self):
        self.cap = cv2.VideoCapture(VIDEO_SOURCE, cv2.CAP_V4L2)
        if not self.cap.isOpened():
            self.update_ai_status("Webcam Error: Could not open device.")

    def crop_frame(self, frame):
        """Crops the frame based on the interactive slider values."""
        if frame is None:
            return None
        h, w, _ = frame.shape

        # Retrieve current values from the DoubleVars
        c_top = self.crop_top.get()
        c_bottom = self.crop_bottom.get()
        c_left = self.crop_left.get()
        c_right = self.crop_right.get()

        top = int(h * c_top)
        bottom = int(h * (1 - c_bottom))
        left = int(w * c_left)
        right = int(w * (1 - c_right))

        top = max(0, min(top, h - 1))
        bottom = max(top + 1, min(bottom, h))
        left = max(0, min(left, w - 1))
        right = max(left + 1, min(right, w))

        return frame[top:bottom, left:right]

    def on_crop_change(self, event=None):
        """Updates the slider labels when values change."""
        self.lbl_top.config(text=f"Top Crop: {int(self.crop_top.get() * 100)}%")
        self.lbl_bottom.config(text=f"Bottom Crop: {int(self.crop_bottom.get() * 100)}%")
        self.lbl_left.config(text=f"Left Crop: {int(self.crop_left.get() * 100)}%")
        self.lbl_right.config(text=f"Right Crop: {int(self.crop_right.get() * 100)}%")

    def reset_crop(self):
        """Resets all crop values to 0%."""
        self.crop_top.set(0.0)
        self.crop_bottom.set(0.0)
        self.crop_left.set(0.0)
        self.crop_right.set(0.0)
        self.on_crop_change()

    def update_webcam_loop(self):
        if self.cap and self.cap.isOpened():
            ret, frame = self.cap.read()
            if ret:
                # Crop the frame dynamically based on slider values
                cropped = self.crop_frame(frame)

                frame_rgb = cv2.cvtColor(cropped, cv2.COLOR_BGR2RGB)
                img = Image.fromarray(frame_rgb)

                width = max(100, self.webcam_label.winfo_width())
                height = max(100, self.webcam_label.winfo_height())
                img.thumbnail((width, height))

                img_tk = ImageTk.PhotoImage(image=img)
                self.webcam_label.img_tk = img_tk
                self.webcam_label.config(image=img_tk)

        if self.running:
            self.root.after(30, self.update_webcam_loop)

    def is_ws_connected(self):
        """Simplified check. We let actual send failures detect a dead socket."""
        return self.ws is not None

    def websocket_monitor_loop(self):
        """
        Background thread that automatically connects if no connection exists.
        Strict 1.0-second connection timeout, checking every 100ms.
        """
        while self.running:
            if self.ws is None:
                self.update_ws_status("Connecting...", "orange")
                try:
                    self.ws = websocket.create_connection(self.ws_url, timeout=1.0)
                    self.update_ws_status("Connected", "green")
                except Exception:
                    self.update_ws_status("Disconnected", "red")
            time.sleep(0.1)  # Safe check interval

    def send_ws_message(self, payload_dict):
        """
        Sends a JSON payload to the phone's WebSocket.
        If it fails, aggressively attempts to reconnect up to 3 times with a strict 1.0-second timeout.
        """
        payload_str = json.dumps(payload_dict)

        # Attempt 1: Try sending with the existing connection
        if self.ws:
            try:
                self.ws.send(payload_str)
                self.update_ws_status("Connected", "green")
                return True
            except Exception as e:
                # Send failed. Cleanly reset the socket so the thread can recover it.
                print(f"[WebSocket] Send failed ({e}). Resetting socket...")
                try:
                    self.ws.close()
                except Exception:
                    pass
                self.ws = None

        # Attempt 2: Aggressive reconnection and send loop (up to 3 sequential attempts)
        for attempt in range(1, 4):
            self.update_ws_status(f"Reconnecting (Attempt {attempt}/3)...", "orange")
            try:
                self.ws = websocket.create_connection(self.ws_url, timeout=1.0)
                self.ws.send(payload_str)
                self.update_ws_status("Connected", "green")
                return True
            except Exception as e:
                print(f"[WebSocket] Reconnect attempt {attempt} failed: {e}")
                self.ws = None
                time.sleep(0.1)  # Fast delay between immediate socket retries

        self.update_ws_status("Disconnected", "red")
        return False

    def add_to_history(self, action_str):
        timestamp = time.strftime("%H:%M:%S")
        entry = f"[{timestamp}] {action_str}"
        self.action_history.append(entry)

        if len(self.action_history) > 15:
            self.action_history.pop(0)

        self.update_history_display()

    def clear_history(self):
        self.action_history = []
        self.screen_history = []  # Clear screen history as well
        self.update_history_display()

    def update_history_display(self):
        def update():
            self.history_text.config(state=tk.NORMAL)
            self.history_text.delete("1.0", tk.END)
            self.history_text.insert(tk.END, "\n".join(self.action_history))
            self.history_text.config(state=tk.DISABLED)
        self.root.after(0, update)

    def trigger_ai(self):
        if self.is_analyzing:
            return
        self.is_analyzing = True
        self.cancel_requested = False  # Reset cancellation flag
        self.trigger_btn.config(state=tk.DISABLED)
        self.cancel_btn.config(state=tk.NORMAL)  # Enable cancel button
        self.update_ai_status("Capturing frame...")

        threading.Thread(target=self.run_ai_workflow, daemon=True).start()

    def cancel_ai(self):
        """Triggers cancellation of the active AI workflow."""
        if not self.is_analyzing:
            return
        self.cancel_requested = True
        self.update_ai_status("Cancellation requested...")
        self.cancel_btn.config(state=tk.DISABLED)

    def run_ai_workflow(self):
        try:
            if self.cancel_requested:
                self.update_ai_status("Analysis cancelled.")
                return

            if self.cap:
                for _ in range(5):
                    if self.cancel_requested:
                        self.update_ai_status("Analysis cancelled.")
                        return
                    self.cap.grab()
                ret, frame = self.cap.read()
            else:
                ret = False

            if not ret:
                self.update_ai_status("Error: Failed to capture frame.")
                self.reset_trigger_btn()
                return

            if self.cancel_requested:
                self.update_ai_status("Analysis cancelled.")
                return

            # Crop and optimize the current frame
            cropped_frame = self.crop_frame(frame)
            optimized_frame = resize_for_ai(cropped_frame)

            # --- AUTOMATIC SIDE-BY-SIDE COMPARISON ---
            if len(self.screen_history) == 0:
                # First turn: No history yet, send only the current screen
                self.screen_history.append(optimized_frame)
                image_to_send = optimized_frame
                user_prompt = "Analyze the current screen and output the next JSON command."
            else:
                # Subsequent turns: Get the previous screen
                previous_img = self.screen_history[-1]

                # Append current frame to history
                self.screen_history.append(optimized_frame)
                if len(self.screen_history) > 5:
                    self.screen_history.pop(0)

                current_img = optimized_frame

                # Ensure both are 3-channel BGR images
                if len(current_img.shape) == 2:
                    current_img = cv2.cvtColor(current_img, cv2.COLOR_GRAY2BGR)
                if len(previous_img.shape) == 2:
                    previous_img = cv2.cvtColor(previous_img, cv2.COLOR_GRAY2BGR)

                h1, w1, _ = previous_img.shape
                h2, w2, _ = current_img.shape

                # Resize previous to match current height
                if h1 != h2:
                    scale = h2 / h1
                    previous_img = cv2.resize(previous_img, (int(w1 * scale), h2), interpolation=cv2.INTER_AREA)

                # Add 40 pixels of black padding to the bottom of each image for labels
                labeled_prev = cv2.copyMakeBorder(previous_img, 0, 40, 0, 0, cv2.BORDER_CONSTANT, value=(0,0,0))
                labeled_curr = cv2.copyMakeBorder(current_img, 0, 40, 0, 0, cv2.BORDER_CONSTANT, value=(0,0,0))

                h_prev = labeled_prev.shape[0]
                h_curr = labeled_curr.shape[0]

                # Draw the text in the newly added bottom black space
                cv2.putText(labeled_prev, "PREVIOUS SCREEN (Before Last Action)", (10, h_prev - 15),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 255), 2, cv2.LINE_AA)
                cv2.putText(labeled_curr, "CURRENT SCREEN (Latest)", (10, h_curr - 15),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2, cv2.LINE_AA)

                comparison_img = cv2.hconcat([labeled_prev, labeled_curr])
                image_to_send = comparison_img
                user_prompt = (
                    "You are looking at a side-by-side comparison. On the left is the PREVIOUS SCREEN (before your last action). "
                    "On the right is the CURRENT SCREEN. Compare them carefully to see if your last action had any effect, "
                    "then output your next JSON command."
                )

            _, buffer = cv2.imencode('.jpg', image_to_send)
            img_b64 = base64.b64encode(buffer).decode('utf-8')

            sys_inst = self.instruction_text.get("1.0", tk.END).strip()

            if self.action_history:
                history_str = "\n".join([f"- {act}" for act in self.action_history])
                sys_inst = (
                    f"{sys_inst}\n\n"
                    f"### RECENT ACTION & REASONING HISTORY (Oldest to Newest):\n"
                    f"{history_str}\n\n"
                    f"CRITICAL WARNING FOR LOOP PREVENTION:\n"
                    f"Review the history above carefully. It contains both the actions taken and the 'reason' you gave for them.\n"
                    f"If the screen has NOT changed since your last action, it means your previous action or reasoning failed.\n"
                    f"DO NOT repeat the same action for the same reason. If a keypress or mouse movement did not produce the "
                    f"expected result, you must change your strategy, try a different key (like 'tab' instead of 'down'), or find another path."
                )

            self.clear_response_display()
            ai_response = ""

            if self.cancel_requested:
                self.update_ai_status("Analysis cancelled.")
                return

            if USE_LOCAL_AI:
                self.update_ai_status(f"Streaming from Local Ollama ({LOCAL_MODEL})...")
                endpoint = "http://localhost:11434/api/chat"

                payload = {
                    "model": LOCAL_MODEL,
                    "messages": [
                        {
                            "role": "system",
                            "content": sys_inst
                        },
                        {
                            "role": "user",
                            "content": user_prompt,
                            "images": [img_b64]
                        }
                    ],
                    "stream": True,
                    "think": True,
                    "options": {
                        "temperature": 0.0,
                        "top_p": 0.0,
                        "num_ctx": 4000
                    }
                }

                response = requests.post(endpoint, json=payload, stream=True)
                response.raise_for_status()

                for line in response.iter_lines():
                    if not self.running or self.cancel_requested:
                        break
                    if line:
                        chunk = json.loads(line.decode('utf-8'))
                        message = chunk.get("message", {})
                        content = message.get("content", "")
                        thinking = message.get("thinking", "")

                        if thinking:
                            if not ai_response.startswith("<think>"):
                                self.append_to_response_display("<think>\n")
                                ai_response += "<think>\n"
                            self.append_to_response_display(thinking)
                            ai_response += thinking
                        else:
                            if ai_response.count("<think>") > ai_response.count("</think>") and content:
                                self.append_to_response_display("\n</think>\n")
                                ai_response += "\n</think>\n"
                            self.append_to_response_display(content)
                            ai_response += content
            else:
                self.update_ai_status("Connecting to Gemini Live WebSocket...")

                # Establish the live bidirectional connection
                ws_url = f"wss://generativelanguage.googleapis.com/ws/google.ai.generativelanguage.v1beta.GenerativeService.BidiGenerateContent?key={GEMINI_API_KEY}"

                try:
                    gemini_ws = websocket.create_connection(ws_url, timeout=15)
                except Exception as e:
                    self.update_ai_status(f"Failed to open WebSocket connection: {e}")
                    self.reset_trigger_btn()
                    return

                # Setup configuration targeting AUDIO with TEXT transcription output
                setup_msg = {
                    "setup": {
                        "model": "models/gemini-3.1-flash-live-preview",
                        "generationConfig": {
                            "responseModalities": ["AUDIO"],
                            "temperature": 0.0
                        },
                        "outputAudioTranscription": {},
                        "systemInstruction": {
                            "parts": [
                                {"text": sys_inst}
                            ]
                        }
                    }
                }

                try:
                    gemini_ws.send(json.dumps(setup_msg))
                except Exception as e:
                    self.update_ai_status(f"Failed to send setup configuration: {e}")
                    gemini_ws.close()
                    self.reset_trigger_btn()
                    return

                # Await setupComplete from Gemini server safely
                setup_done = False
                while not setup_done:
                    if self.cancel_requested:
                        gemini_ws.close()
                        self.update_ai_status("Analysis cancelled.")
                        return

                    try:
                        raw_msg = gemini_ws.recv()
                    except Exception as e:
                        self.update_ai_status(f"Handshake error: {e}")
                        self.reset_trigger_btn()
                        return

                    if not raw_msg:
                        self.update_ai_status("Connection rejected by Google (Check key or quotas).")
                        self.reset_trigger_btn()
                        return

                    try:
                        msg_data = json.loads(raw_msg)
                    except Exception as e:
                        self.update_ai_status(f"Unexpected handhake payload: {e}")
                        self.reset_trigger_btn()
                        return

                    if "setupComplete" in msg_data:
                        setup_done = True

                self.update_ai_status("Gemini Live connected. Sending payload...")

                # Send the prompt and visual turn over the WebSocket channel
                client_content_msg = {
                    "clientContent": {
                        "turns": [
                            {
                                "role": "user",
                                "parts": [
                                    {"text": user_prompt},
                                    {
                                        "inlineData": {
                                            "mimeType": "image/jpeg",
                                            "data": img_b64
                                        }
                                    }
                                ]
                            }
                        ],
                        "turnComplete": True
                    }
                }

                try:
                    gemini_ws.send(json.dumps(client_content_msg))
                except Exception as e:
                    self.update_ai_status(f"Failed to send turn payload: {e}")
                    gemini_ws.close()
                    self.reset_trigger_btn()
                    return

                # Use a snappy read timeout for the streaming phase (0.5 seconds per recv)
                gemini_ws.settimeout(0.5)

                self.update_ai_status("Streaming response from Gemini Live...")

                # Receive chunks until the turn is marked complete or times out
                turn_complete = False
                last_chunk_time = time.time()
                started_receiving = False

                while not turn_complete:
                    if self.cancel_requested:
                        gemini_ws.close()
                        self.update_ai_status("Analysis cancelled.")
                        return

                    try:
                        raw_msg = gemini_ws.recv()
                    except websocket.WebSocketTimeoutException:
                        # Inactivity timeout logic
                        elapsed = time.time() - last_chunk_time
                        if started_receiving and elapsed > 1.5:
                            # 1.5 seconds of silence after starting to receive -> complete
                            turn_complete = True
                            break
                        elif not started_receiving and elapsed > 8.0:
                            # No response started after 8 seconds -> give up
                            self.update_ai_status("Inactivity timeout (no response).")
                            break
                        continue
                    except Exception as e:
                        self.update_ai_status(f"Stream interrupted: {e}")
                        break

                    if not raw_msg:
                        # Server closed the connection
                        break

                    try:
                        msg_data = json.loads(raw_msg)
                    except Exception:
                        continue  # Safe ignore for any non-JSON binary payload slices

                    server_content = msg_data.get("serverContent", {})

                    # Extract the text transcription of the generated voice response
                    transcription = server_content.get("outputTranscription", {})
                    text_chunk = transcription.get("text", "")

                    if text_chunk:
                        # Mark that we have actively started receiving text chunks
                        started_receiving = True
                        last_chunk_time = time.time()

                        self.append_to_response_display(text_chunk)
                        ai_response += text_chunk

                    # End parsing if Google explicitly signals turn completion or transcription finished
                    if (server_content.get("turnComplete") or
                        msg_data.get("turnComplete") or
                        transcription.get("finished")):
                        turn_complete = True

                # Terminate WebSocket connection cleanly
                gemini_ws.close()

            if self.cancel_requested:
                self.update_ai_status("Analysis cancelled.")
                return

            cleaned_response = clean_json_response(ai_response)

            command = json.loads(cleaned_response)
            cmd_type = command.get("type")
            cmd_value = command.get("value")
            cmd_reason = command.get("reason", "No reason provided")

            # --- SAFETY NET FOR AI FORMATTING SLIP-UPS ---
            if cmd_type in ["tab", "enter", "space", "up", "down", "left", "right", "escape", "backspace"]:
                if cmd_value not in ["tab", "enter", "space", "up", "down", "left", "right", "escape", "backspace"]:
                    cmd_value = cmd_type
                cmd_type = "key"

            cmd_type = str(cmd_type).lower().strip()
            if cmd_type not in ["key", "type", "mouse", "wait"]:
                cmd_type = "key"

            # --- HANDLE COMMANDS ---
            if cmd_type == "wait":
                self.update_ai_status(f"AI decided to wait. Reason: {cmd_reason}")
                self.add_to_history(f"Action: wait | Reason: {cmd_reason}")
            elif cmd_type == "type":
                self.update_ai_status(f"Typing: {cmd_value}")
                self.add_to_history(f"Action: type '{cmd_value}' | Reason: {cmd_reason}")

                # Directly transmit the text as a single payload to the phone
                success = self.send_ws_message({"type": "text", "value": cmd_value})
                if success:
                    self.update_ai_status(f"Finished typing sequence. Reason: {cmd_reason}")
                else:
                    self.update_ai_status("Typing failed (WebSocket disconnected).")
            else:
                self.update_ai_status(f"Sending command: {cmd_type} -> {cmd_value}")
                self.add_to_history(f"Action: {cmd_type} ({cmd_value}) | Reason: {cmd_reason}")
                success = self.send_ws_message({"type": cmd_type, "value": cmd_value})
                if success:
                    self.update_ai_status(f"Command sent. Reason: {cmd_reason}")
                else:
                    self.update_ai_status("Command failed (WebSocket disconnected).")

        except Exception as e:
            self.update_ai_status(f"Error: {e}")
            if 'response' in locals() and response is not None:
                self.display_ai_response(f"Error details:\n{e}\n\nRaw Response:\n{ai_response}")
        finally:
            self.reset_trigger_btn()

    def update_ws_status(self, text, color):
        self.root.after(0, lambda: self.ws_status_label.config(text=f"WebSocket: {text}", foreground=color))

    def update_ai_status(self, text):
        self.root.after(0, lambda: self.ai_status_label.config(text=f"AI Status: {text}"))

    def clear_response_display(self):
        def update():
            self.response_text.config(state=tk.NORMAL)
            self.response_text.delete("1.0", tk.END)
            self.response_text.config(state=tk.DISABLED)
        self.root.after(0, update)

    def append_to_response_display(self, text):
        def update():
            self.response_text.config(state=tk.NORMAL)
            self.response_text.insert(tk.END, text)
            self.response_text.see(tk.END)
            self.response_text.config(state=tk.DISABLED)
        self.root.after(0, update)

    def display_ai_response(self, text):
        def update():
            self.response_text.config(state=tk.NORMAL)
            self.response_text.delete("1.0", tk.END)
            self.response_text.insert(tk.END, text)
            self.response_text.config(state=tk.DISABLED)
        self.root.after(0, update)

    def reset_trigger_btn(self):
        def update():
            self.trigger_btn.config(state=tk.NORMAL)
            self.cancel_btn.config(state=tk.DISABLED)
            self.is_analyzing = False
        self.root.after(0, update)

    def on_close(self):
        self.running = False
        if self.cap:
            self.cap.release()
        if self.ws:
            try:
                self.ws.close()
            except Exception:
                pass
        self.root.destroy()

if __name__ == "__main__":
    root = tk.Tk()
    app = WindowsInstallerAIApp(root)
    root.mainloop()
