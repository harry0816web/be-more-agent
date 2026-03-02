# =========================================================================
#  Be More Agent 🤖
#  A Local, Offline-First AI Agent for Raspberry Pi
#
#  Copyright (c) 2026 brenpoly
#  Licensed under the MIT License
#  Source: https://github.com/brenpoly/be-more-agent
#
#  DISCLAIMER:
#  This software is provided "as is", without warranty of any kind.
#  This project is a generic framework and includes no copyrighted assets.
# =========================================================================

import tkinter as tk
from tkinter import ttk
from PIL import Image, ImageTk, ImageDraw, ImageFont
import threading
import time
import json
import os
import subprocess
import random
import re
import sys
import select
import traceback
import atexit
import datetime
import warnings
import wave
import struct 

# Suppress harmless library warnings
warnings.filterwarnings("ignore", category=RuntimeWarning, module="duckduckgo_search")

# Core dependencies
import sounddevice as sd
import numpy as np
import scipy.signal 

# --- AI ENGINES ---
import openwakeword
from openwakeword.model import Model
import ollama 

# --- WEB SEARCH (Using your working import) ---
from ddgs import DDGS

# --- WEATHER SERVICE ---
import weather_svc 

# =========================================================================
# 1. CONFIGURATION & CONSTANTS
# =========================================================================

CONFIG_FILE = "config.json"
MEMORY_FILE = "memory.json"
BMO_IMAGE_FILE = "current_image.jpg"
WAKE_WORD_MODEL = "./wakeword.onnx"
WAKE_WORD_THRESHOLD = 0.5

# HARDWARE SETTINGS
INPUT_DEVICE_NAME = None 

DEFAULT_CONFIG = {
    "text_model": "gemma3:1b",
    "voice_model": "piper/en_GB-semaine-medium.onnx",
    "chat_memory": True,
    "camera_rotation": 0,
    "system_prompt_extras": "",
    "openweather_api_key": "",
    "weather_lat": "24.801",
    "weather_lon": "120.971",
}

# LLM SETTINGS
OLLAMA_OPTIONS = {
    'keep_alive': '-1',     
    'num_thread': 4,
    'temperature': 0.7,     
    'top_k': 40,
    'top_p': 0.9
}

def load_config():
    config = DEFAULT_CONFIG.copy()
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r") as f:
                user_config = json.load(f)
                config.update(user_config)
        except Exception as e:
            print(f"Config Error: {e}. Using defaults.")
    return config

CURRENT_CONFIG = load_config()
TEXT_MODEL = CURRENT_CONFIG["text_model"]
VISION_MODEL = CURRENT_CONFIG["vision_model"]

class BotStates:
    IDLE = "idle"             
    LISTENING = "listening"   
    THINKING = "thinking"     
    SPEAKING = "speaking"     
    ERROR = "error"           
    CAPTURING = "capturing" 
    WARMUP = "warmup"       

# --- SYSTEM PROMPT ---
BASE_SYSTEM_PROMPT = """You are a helpful robot assistant running on a Raspberry Pi.
Personality: Cute, helpful, robot.
Style: Short sentences. Enthusiastic.

INSTRUCTIONS:
- If the user asks for a physical action (time, search, photo), output JSON.
- If the user just wants to chat, reply with NORMAL TEXT.

### EXAMPLES ###

User: What time is it?
You: {"action": "get_time", "value": "now"}

User: Hello!
You: Hi! I am ready to help!

User: Search for news about robots.
You: {"action": "search_web", "value": "robots news"}

User: What's the weather?
You: {"action": "get_weather", "value": "now"}

### END EXAMPLES ###
"""

SYSTEM_PROMPT = BASE_SYSTEM_PROMPT + "\n\n" + CURRENT_CONFIG.get("system_prompt_extras", "")

# Sound Directories
greeting_sounds_dir = "sounds/greeting_sounds"
ack_sounds_dir = "sounds/ack_sounds"
thinking_sounds_dir = "sounds/thinking_sounds"
error_sounds_dir = "sounds/error_sounds"
celebration_sounds_dir = "sounds/celebration_sounds"

POMODORO_SYSTEM_PROMPT = """You are in Pomodoro study mode. User said: "{text}"
Current: {remaining} min {sec} sec left, paused={paused}

Output ONLY valid JSON. Choose ONE action by user intent (do NOT use keyword match, use semantic understanding):

IMPORTANT: If user mentions a NUMBER of minutes (e.g. "30 minutes", "set to 20", "change to 15 min") -> ALWAYS use pomodoro_set_duration with that number.

- pomodoro_pause: user wants to PAUSE/STOP the timer (e.g. pause, wait, hold on, stop for now). NOT when they say a number.
- pomodoro_resume: user wants to resume (e.g. resume, continue, start, go)
- pomodoro_reset: user wants to RESTART the timer from beginning (e.g. reset, restart, restart timer, again, start over, "restart your timer")
- pomodoro_set_duration: user wants to CHANGE the timer duration to N minutes -> {{"action": "pomodoro_set_duration", "value": N}} (N=1-60). Use when: "set time to X", "change to X min", "X minutes", "make it X min".
- pomodoro_chat: chat/question/unclear -> {{"action": "pomodoro_chat", "value": "your short reply"}}

Examples (intent-based, not literal):
"hold on a sec" -> {{"action": "pomodoro_pause"}}
"restart your timer" -> {{"action": "pomodoro_reset"}}
"reset" -> {{"action": "pomodoro_reset"}}
"set the time to 30 minutes" -> {{"action": "pomodoro_set_duration", "value": 30}}
"change to 20 minutes" -> {{"action": "pomodoro_set_duration", "value": 20}}
"30 minutes" -> {{"action": "pomodoro_set_duration", "value": 30}}
"how much time left" -> {{"action": "pomodoro_chat", "value": "X min Y sec left"}}"""

# =========================================================================
# 2. GUI CLASS
# =========================================================================

class BotGUI:
    BG_WIDTH, BG_HEIGHT = 800, 480 
    OVERLAY_WIDTH, OVERLAY_HEIGHT = 400, 300 

    def __init__(self, master):
        self.master = master
        master.title("Pi Assistant")
        master.attributes('-fullscreen', True) 
        master.bind('<Escape>', self.exit_fullscreen)
        
        # Inputs
        master.bind('<Return>', self.handle_ptt_toggle)
        master.bind('<space>', self.handle_speaking_interrupt)
        atexit.register(self.safe_exit)
        
        # State
        self.current_state = BotStates.WARMUP
        self.current_volume = 0 
        self.animations = {}
        self.current_frame_index = 0
        self.current_overlay_image = None
        self.current_idle_overlay_image = None  # Keep ref for IDLE time overlay
        
        self.permanent_memory = self.load_chat_history()
        self.session_memory = []
        self.thinking_sound_active = threading.Event()
        
        self.last_ptt_time = 0 
        self.ptt_event = threading.Event()       
        self.recording_active = threading.Event() 
        self.interrupted = threading.Event() 
        
        self.tts_queue = []          
        self.tts_queue_lock = threading.Lock() 
        self.tts_thread = None       
        self.tts_active = threading.Event()
        self.current_audio_process = None 
        
        # --- POMODORO STATE ---
        self.pomodoro_active = False
        self.pomodoro_remaining_seconds = 0
        self.pomodoro_stop_event = threading.Event()
        self.pomodoro_paused = False
        self.pomodoro_duration_minutes = 25
        
        # --- WAKE WORD INITIALIZATION ---
        print("[INIT] Loading Wake Word...", flush=True)
        self.oww_model = None
        if os.path.exists(WAKE_WORD_MODEL):
            try:
                self.oww_model = Model(wakeword_model_paths=[WAKE_WORD_MODEL])
                print("[INIT] Wake Word Loaded.", flush=True)
            except TypeError:
                try:
                    self.oww_model = Model(wakeword_models=[WAKE_WORD_MODEL])
                    print("[INIT] Wake Word Loaded (New API).", flush=True)
                except Exception as e:
                    print(f"[CRITICAL] Failed to load model: {e}")
            except Exception as e:
                print(f"[CRITICAL] Failed to load model: {e}")
        else:
            print(f"[CRITICAL] Model not found: {WAKE_WORD_MODEL}")

        # GUI Setup
        self.background_label = tk.Label(master)
        self.background_label.place(x=0, y=0, width=self.BG_WIDTH, height=self.BG_HEIGHT)
        self.background_label.bind('<Button-1>', self.toggle_hud_visibility) 
        
        self.overlay_label = tk.Label(master, bg='black')
        self.overlay_label.bind('<Button-1>', self.toggle_hud_visibility)
        
        self.response_text = tk.Text(master, height=6, width=60, wrap=tk.WORD, 
                                     state=tk.DISABLED, bg="#ffffff", fg="#000000", font=('Arial', 12)) 
        
        self.status_var = tk.StringVar(value="Initializing...")
        self.status_label = ttk.Label(master, textvariable=self.status_var, background="#2e2e2e", foreground="white")
        
        self.exit_button = ttk.Button(master, text="Exit & Save", command=self.safe_exit)

        self.load_animations()
        self.update_animation()

        # Start weather service (idle face + get_weather action)
        weather_svc.init()
        weather_svc.start_background_thread()

        threading.Thread(target=self.safe_main_execution, daemon=True).start()

    # --- HELPERS ---

    def extract_json_from_text(self, text):
        try:
            match = re.search(r'\{.*\}', text, re.DOTALL)
            if match:
                parsed = json.loads(match.group(0))
                if not isinstance(parsed, dict):
                    print(f"[JSON] Parsed result is not dict: type={type(parsed).__name__}, value={parsed!r}", flush=True)
                    return None
                return parsed
            return None
        except json.JSONDecodeError as e:
            print(f"[JSON] Parse error: {e}, text={text[:150]!r}", flush=True)
            return None
        except Exception as e:
            print(f"[JSON] Unexpected error: {e}", flush=True)
            return None

    def safe_exit(self):
        print("\n--- SHUTDOWN SEQUENCE ---", flush=True)
        if self.current_audio_process:
            try:
                self.current_audio_process.terminate()
                self.current_audio_process.wait(timeout=1)
            except: pass

        self.recording_active.clear()
        self.thinking_sound_active.clear()
        self.tts_active.clear() 
        
        self.save_chat_history()
        
        try:
            ollama.generate(model=TEXT_MODEL, prompt="", keep_alive=0)
        except: pass

        self.master.quit()
        sys.exit(0) 
        
    def exit_fullscreen(self, event=None):
        self.master.attributes('-fullscreen', False)
        self.safe_exit()

    def toggle_hud_visibility(self, event=None):
        try:
            if self.response_text.winfo_ismapped():
                self.response_text.place_forget()
                self.status_label.place_forget()
                self.exit_button.place_forget()
            else:
                self.response_text.place(relx=0.5, rely=0.82, anchor=tk.S)
                self.status_label.place(relx=0.5, rely=1.0, anchor=tk.S, relwidth=1)
                self.exit_button.place(x=10, y=10)
        except tk.TclError: pass

    def handle_ptt_toggle(self, event=None):
        current_time = time.time()
        if current_time - self.last_ptt_time < 0.5: 
            return 
        self.last_ptt_time = current_time

        if self.recording_active.is_set():
            print("[PTT] Toggle OFF", flush=True)
            self.recording_active.clear() 
        else:
            if self.current_state == BotStates.IDLE or "Wait" in self.status_var.get():
                print("[PTT] Toggle ON", flush=True)
                self.recording_active.set() 
                self.ptt_event.set()

    def handle_speaking_interrupt(self, event=None):
        if self.current_state == BotStates.SPEAKING or self.current_state == BotStates.THINKING:
            self.interrupted.set()
            self.thinking_sound_active.clear()
            with self.tts_queue_lock:
                self.tts_queue.clear()
            if self.current_audio_process:
                try: self.current_audio_process.terminate()
                except: pass
            self.set_state(BotStates.IDLE, "Interrupted.")

    def load_animations(self):
        base_path = "faces"
        states = ["idle", "listening", "thinking", "speaking", "error", "capturing", "warmup"]
        self.idle_base_images = []  # list for backward compat
        self.idle_images_by_name = {}  # { "idle 01.png": PIL_img, "idle_sunny.png": PIL_img, ... }
        for state in states:
            folder = os.path.join(base_path, state)
            self.animations[state] = []
            if os.path.exists(folder):
                files = sorted([f for f in os.listdir(folder) if f.lower().endswith('.png')])
                if state == "idle":
                    # Load all idle PNGs for weather-based selection
                    for f in files:
                        img = Image.open(os.path.join(folder, f)).resize((self.BG_WIDTH, self.BG_HEIGHT))
                        self.idle_base_images.append(img.copy())
                        self.idle_images_by_name[f] = img.copy()
                        self.animations[state].append(ImageTk.PhotoImage(img))
                    # Fallback: ensure we have at least one (prefer "idle 01.png")
                    if not self.idle_base_images and files:
                        img = Image.open(os.path.join(folder, files[0])).resize((self.BG_WIDTH, self.BG_HEIGHT))
                        self.idle_base_images.append(img.copy())
                        self.idle_images_by_name[files[0]] = img.copy()
                        self.animations[state].append(ImageTk.PhotoImage(img))
                else:
                    for f in files:
                        img = Image.open(os.path.join(folder, f)).resize((self.BG_WIDTH, self.BG_HEIGHT))
                        self.animations[state].append(ImageTk.PhotoImage(img))
            if not self.animations[state]:
                if state in self.animations.get("idle", []):
                    self.animations[state] = self.animations["idle"]
                else:
                    blank = Image.new('RGB', (self.BG_WIDTH, self.BG_HEIGHT), color='#0000FF')
                    self.animations[state].append(ImageTk.PhotoImage(blank))

    def _generate_idle_with_time_overlay(self, frame_index):
        """Overlay compact time (M/D HH:MM) or Pomodoro countdown at bottom of idle image."""
        if self.pomodoro_active:
            base_img = "idle_tomato.png"
            if self.idle_images_by_name and base_img in self.idle_images_by_name:
                img = self.idle_images_by_name[base_img].copy()
            elif self.idle_base_images:
                img = self.idle_base_images[frame_index % len(self.idle_base_images)].copy()
            else:
                img = Image.new('RGB', (self.BG_WIDTH, self.BG_HEIGHT), color=(189, 255, 203))
            time_str = f"{self.pomodoro_remaining_seconds // 60:02d}:{self.pomodoro_remaining_seconds % 60:02d}"
        else:
            weather_img = weather_svc.get_current_idle_image()
            if self.idle_images_by_name and weather_img in self.idle_images_by_name:
                img = self.idle_images_by_name[weather_img].copy()
            elif self.idle_base_images:
                img = self.idle_base_images[frame_index % len(self.idle_base_images)].copy()
            else:
                img = Image.new('RGB', (self.BG_WIDTH, self.BG_HEIGHT), color=(189, 255, 203))
            time_str = datetime.datetime.now().strftime("%m/%d %H:%M")
        if img.mode != 'RGB':
            img = img.convert('RGB')
        draw = ImageDraw.Draw(img)
        font_size = 26
        try:
            font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", font_size)
        except (OSError, IOError):
            font = ImageFont.load_default()
        try:
            bbox = draw.textbbox((0, 0), time_str, font=font)
            tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        except AttributeError:
            tw, th = draw.textsize(time_str, font=font)
        x = (self.BG_WIDTH - tw) // 2
        y = self.BG_HEIGHT - th - 18
        draw.text((x, y), time_str, fill=(0, 0, 0), font=font)
        return ImageTk.PhotoImage(img)

    def update_animation(self):
        if self.current_state == BotStates.IDLE:
            self.current_idle_overlay_image = self._generate_idle_with_time_overlay(0)
            self.background_label.config(image=self.current_idle_overlay_image)
            self.master.after(1000, self.update_animation)
            return

        frames = self.animations.get(self.current_state, []) or self.animations.get(BotStates.IDLE, [])
        if not frames:
            self.master.after(500, self.update_animation)
            return

        if self.current_state == BotStates.SPEAKING:
            if len(frames) > 1:
                self.current_frame_index = random.randint(1, len(frames) - 1)
            else:
                self.current_frame_index = 0 
        else:
            self.current_frame_index = (self.current_frame_index + 1) % len(frames)

        self.background_label.config(image=frames[self.current_frame_index])
        
        speed = 50 if self.current_state == BotStates.SPEAKING else 500
        self.master.after(speed, self.update_animation)

    def set_state(self, state, msg="", cam_path=None):
        def _update():
            if msg: print(f"[STATE] {state.upper()}: {msg}", flush=True)
            if self.current_state != state:
                self.current_state = state
                self.current_frame_index = 0
            if msg: self.status_var.set(msg)
            if cam_path and os.path.exists(cam_path) and state in [BotStates.THINKING, BotStates.SPEAKING]:
                try:
                    img = Image.open(cam_path).resize((self.OVERLAY_WIDTH, self.OVERLAY_HEIGHT))
                    self.current_overlay_image = ImageTk.PhotoImage(img)
                    self.overlay_label.config(image=self.current_overlay_image)
                    self.overlay_label.place(x=200, y=90)
                except: pass
            else:
                self.overlay_label.place_forget()
        self.master.after(0, _update)

    def append_to_text(self, text, newline=True):
        def _update():
            self.response_text.config(state=tk.NORMAL)
            if newline: 
                self.response_text.insert(tk.END, text + "\n")
            else: 
                self.response_text.insert(tk.END, text)
            
            self.response_text.see(tk.END)
            self.response_text.config(state=tk.DISABLED)
            
        self.master.after(0, _update)

    def _stream_to_text(self, chunk):
        def update_text_stream():
            self.response_text.config(state=tk.NORMAL)
            self.response_text.insert(tk.END, chunk)
            self.response_text.see(tk.END) 
            self.response_text.config(state=tk.DISABLED)
        self.master.after(0, update_text_stream)

    # =========================================================================
    # 3. ACTION ROUTER
    # =========================================================================
    
    def execute_action_and_get_result(self, action_data):
        if not isinstance(action_data, dict):
            print(f"[Action] action_data is not dict: type={type(action_data).__name__}, value={action_data!r}", flush=True)
            return "INVALID_ACTION"
        raw_action = action_data.get("action", "").lower().strip()
        value = action_data.get("value") or action_data.get("query")
        
        VALID_TOOLS = {
            "get_time", "search_web", "capture_image", "get_weather"
        }

        ALIASES = {
            "google": "search_web", "browser": "search_web", "news": "search_web",
            "search_news": "search_web", "look": "capture_image", "see": "capture_image",
            "check_time": "get_time", "weather": "get_weather",
        }

        action = ALIASES.get(raw_action, raw_action)
        print(f"ACTION: {raw_action} -> {action}", flush=True)

        if action not in VALID_TOOLS:
            if value and isinstance(value, str) and len(value.split()) > 1:
                return f"CHAT_FALLBACK::{value}"
            return "INVALID_ACTION"

        if action == "get_time":
            now = datetime.datetime.now().strftime("%I:%M %p")
            return f"The current time is {now}."

        elif action == "get_weather":
            info = weather_svc.get_weather_info()
            if info["last_update"] > 0:
                temp_str = f"{info['temp']:.0f}°C" if info.get("temp") is not None else "N/A"
                return f"Weather: {info['main']}, {temp_str}, wind {info['wind_speed']:.1f} m/s."
            return "Weather data not available yet. Check your OpenWeather API key in config.json."

        elif action == "search_web":
            print(f"Searching web for: {value}...", flush=True)
            try:
                # 'us-en' region is often more stable for CLI queries
                with DDGS() as ddgs:
                    results = []
                    # 1. News search
                    try:
                        results = list(ddgs.news(value, region='us-en', max_results=1))
                        if results: 
                            print(f"[DEBUG] Found News: {results[0].get('title')}", flush=True)
                    except Exception as e: 
                        print(f"[DEBUG] News Search Error: {e}", flush=True)
                    
                    # 2. Text fallback
                    if not results:
                        print("[DEBUG] No news found, trying text search...", flush=True)
                        try: 
                            results = list(ddgs.text(value, region='us-en', max_results=1))
                            if results: 
                                print(f"[DEBUG] Found Text: {results[0].get('title')}", flush=True)
                        except Exception as e:
                             print(f"[DEBUG] Text Search Error: {e}", flush=True)

                    if results:
                        r = results[0]
                        # Safe get
                        title = r.get('title', 'No Title')
                        body = r.get('body', r.get('snippet', 'No Body'))
                        return f"SEARCH RESULTS for '{value}':\nTitle: {title}\nSnippet: {body[:300]}"
                    else: 
                        print(f"[DEBUG] Search returned 0 results.", flush=True)
                        return "SEARCH_EMPTY"
            except Exception as e:
                print(f"[DEBUG] Connection/Library Error: {e}", flush=True)
                return "SEARCH_ERROR"
        
        elif action == "capture_image":
             return "IMAGE_CAPTURE_TRIGGERED"

        return None

    # =========================================================================
    # 4. CORE LOGIC
    # =========================================================================

    def safe_main_execution(self):
        try:
            self.warm_up_logic()
            self.tts_active.set()
            self.tts_thread = threading.Thread(target=self._tts_worker, daemon=True)
            self.tts_thread.start()
            
            while True:
                trigger_source = self.detect_wake_word_or_ptt()
                if self.interrupted.is_set():
                    self.interrupted.clear()
                    self.set_state(BotStates.IDLE, "Resetting...")
                    continue

                self.set_state(BotStates.LISTENING, "I'm listening!")
                
                audio_file = None
                if trigger_source == "PTT":
                    audio_file = self.record_voice_ptt()
                else:
                    audio_file = self.record_voice_adaptive()
                
                if not audio_file: 
                    self.set_state(BotStates.IDLE, "Heard nothing.")
                    continue
                
                user_text = self.transcribe_audio(audio_file)
                if not user_text:
                    self.set_state(BotStates.IDLE, "Transcription empty.")
                    continue
                
                self.append_to_text(f"YOU: {user_text}")
                self.interrupted.clear()
                self.chat_and_respond(user_text, img_path=None)
                    
        except Exception as e:
            traceback.print_exc()
            self.set_state(BotStates.ERROR, f"Fatal Error: {str(e)[:40]}")

    def warm_up_logic(self):
        self.set_state(BotStates.WARMUP, "Warming up brains...")
        try:
            ollama.generate(model=TEXT_MODEL, prompt="", keep_alive=-1)
        except Exception as e:
            print(f"Failed to load {TEXT_MODEL}: {e}", flush=True)
        self.play_sound(self.get_random_sound(greeting_sounds_dir))
        print("Models loaded.", flush=True)

    def start_pomodoro(self, minutes):
        """Start Pomodoro study mode: tomato face + countdown."""
        self.pomodoro_stop_event.clear()
        self.pomodoro_paused = False
        self.pomodoro_active = True
        self.pomodoro_duration_minutes = minutes
        self.pomodoro_remaining_seconds = minutes * 60
        self.set_state(BotStates.IDLE, f"Study mode: {minutes} min")
        self.append_to_text(f"BOT: Study with me! {minutes} minutes. Let's focus!")
        with self.tts_queue_lock:
            self.tts_queue.append(f"Study with me! {minutes} minutes. Let's focus!")
        self.wait_for_tts()

        def _countdown():
            while self.pomodoro_remaining_seconds > 0 and not self.pomodoro_stop_event.is_set():
                time.sleep(1)
                if self.pomodoro_stop_event.is_set():
                    return
                while self.pomodoro_paused and not self.pomodoro_stop_event.is_set():
                    time.sleep(0.5)
                if self.pomodoro_stop_event.is_set():
                    return
                self.pomodoro_remaining_seconds -= 1
                self.master.after(0, lambda: self.status_var.set(
                    f"Focus: {self.pomodoro_remaining_seconds // 60:02d}:{self.pomodoro_remaining_seconds % 60:02d}" if self.pomodoro_remaining_seconds > 0 else "Time's up!"
                ))
            if self.pomodoro_remaining_seconds <= 0 and self.pomodoro_active:
                self.master.after(0, self._pomodoro_finished)

        threading.Thread(target=_countdown, daemon=True).start()

    def _pomodoro_finished(self):
        """Called when Pomodoro timer ends: play celebration + TTS reminder."""
        self.pomodoro_active = False
        self.pomodoro_paused = False
        self.set_state(BotStates.IDLE, "Time's up!")
        sound = self.get_random_sound(celebration_sounds_dir) or self.get_random_sound(greeting_sounds_dir)
        if sound:
            self.play_sound(sound)
        self.append_to_text("BOT: 辛苦了！喝口水站起來走一走喔！")
        with self.tts_queue_lock:
            self.tts_queue.append("辛苦了！喝口水站起來走一走喔！")
        self.wait_for_tts()
        self.set_state(BotStates.IDLE, "Ready")

    def handle_pomodoro_control(self, text):
        """Use LLM to interpret user intent and execute Pomodoro control. Returns (handled: bool, reply: str)."""
        remaining = self.pomodoro_remaining_seconds // 60
        sec = self.pomodoro_remaining_seconds % 60
        prompt = POMODORO_SYSTEM_PROMPT.format(
            text=text,
            remaining=remaining,
            sec=sec,
            paused=str(self.pomodoro_paused).lower()
        )
        try:
            resp = ollama.chat(
                model=TEXT_MODEL,
                messages=[{"role": "user", "content": prompt}],
                stream=False,
                options=OLLAMA_OPTIONS
            )
            content = resp.get("message", {}).get("content", "")
            print(f"[Pomodoro] LLM raw content: {content[:300]!r}", flush=True)
            action_data = self.extract_json_from_text(content)
            if not action_data or not isinstance(action_data, dict):
                print(f"[Pomodoro] Invalid action_data: {action_data!r}", flush=True)
                return (True, "Sorry, I didn't understand. You can say pause, resume, reset, or change the time.")
            print(f"[Pomodoro] Parsed action_data: {action_data}", flush=True)
            action = action_data.get("action", "").lower().strip()
            value = action_data.get("value")
            reply = self.execute_pomodoro_action(action, value)
            return (True, reply)
        except Exception as e:
            print(f"[Pomodoro] LLM error: {type(e).__name__}: {e}", flush=True)
            traceback.print_exc()
            return (True, "Sorry, I didn't understand. You can say pause, resume, reset, or change the time.")

    def execute_pomodoro_action(self, action, value):
        """Execute Pomodoro tool. Returns reply string for TTS."""
        action = (action or "").lower().strip()
        if action == "pomodoro_pause":
            if self.pomodoro_paused:
                return "Already paused."
            self.pomodoro_paused = True
            return "Timer paused."
        if action == "pomodoro_resume":
            if not self.pomodoro_paused:
                return "Timer is already running."
            self.pomodoro_paused = False
            return "Timer resumed."
        if action == "pomodoro_reset":
            self.pomodoro_stop_event.set()
            time.sleep(0.2)
            self.pomodoro_stop_event.clear()
            mins = getattr(self, "pomodoro_duration_minutes", 25) or 25
            self.start_pomodoro(mins)
            return "Timer reset."
        if action == "pomodoro_set_duration":
            mins = 25
            if value is not None:
                try:
                    mins = int(value) if isinstance(value, (int, float)) else int(str(value).strip())
                except (ValueError, TypeError):
                    pass
            mins = min(60, max(1, mins))
            self.pomodoro_stop_event.set()
            time.sleep(0.2)
            self.pomodoro_stop_event.clear()
            self.start_pomodoro(mins)
            return f"Set to {mins} minutes."
        if action == "pomodoro_chat":
            return str(value) if value else "Okay."
        return "Sorry, I didn't understand. You can say pause, resume, reset, or change the time."

    def detect_wake_word_or_ptt(self):
        self.set_state(BotStates.IDLE, "Waiting...")
        self.ptt_event.clear()
        
        if self.oww_model: self.oww_model.reset()

        if self.oww_model is None:
            self.ptt_event.wait()
            self.ptt_event.clear()
            return "PTT"

        CHUNK_SIZE = 1280
        OWW_SAMPLE_RATE = 16000
        
        try:
            device_info = sd.query_devices(kind='input')
            native_rate = int(device_info['default_samplerate'])
        except: native_rate = 48000
            
        use_resampling = (native_rate != OWW_SAMPLE_RATE)
        input_rate = native_rate if use_resampling else OWW_SAMPLE_RATE
        input_chunk_size = int(CHUNK_SIZE * (input_rate / OWW_SAMPLE_RATE)) if use_resampling else CHUNK_SIZE

        try:
            with sd.InputStream(samplerate=input_rate, channels=1, dtype='int16', 
                                blocksize=input_chunk_size, device=INPUT_DEVICE_NAME) as stream:
                while True:
                    if self.ptt_event.is_set():
                        self.ptt_event.clear()
                        return "PTT"
                    
                    rlist, _, _ = select.select([sys.stdin], [], [], 0.001)
                    if rlist: 
                        sys.stdin.readline()
                        return "CLI" 

                    data, _ = stream.read(input_chunk_size)
                    audio_data = np.frombuffer(data, dtype=np.int16)

                    if use_resampling:
                         audio_data = scipy.signal.resample(audio_data, CHUNK_SIZE).astype(np.int16)

                    prediction = self.oww_model.predict(audio_data)
                    for mdl in self.oww_model.prediction_buffer.keys():
                        if list(self.oww_model.prediction_buffer[mdl])[-1] > WAKE_WORD_THRESHOLD:
                            self.oww_model.reset() 
                            return "WAKE"
        except Exception as e:
            print(f"Wake Word Stream Error: {e}")
            self.ptt_event.wait()
            return "PTT"

    def record_voice_adaptive(self, filename="input.wav"):
        print("Recording (Adaptive)...", flush=True)
        time.sleep(0.5) 
        try:
            device_info = sd.query_devices(kind='input')
            samplerate = int(device_info['default_samplerate'])
        except: samplerate = 44100 

        silence_threshold = 0.006
        silence_duration = 1.5
        max_record_time = 30.0
        buffer = []
        silent_chunks = 0
        chunk_duration = 0.05 
        chunk_size = int(samplerate * chunk_duration)
        
        num_silent_chunks = int(silence_duration / chunk_duration)
        max_chunks = int(max_record_time / chunk_duration)
        recorded_chunks = 0
        silence_started = False

        def callback(indata, frames, time_info, status):
            nonlocal silent_chunks, recorded_chunks, silence_started
            volume_norm = np.linalg.norm(indata) / np.sqrt(len(indata))
            buffer.append(indata.copy())  
            recorded_chunks += 1
            if recorded_chunks < 5: return 
            if volume_norm < silence_threshold:
                silent_chunks += 1
                if silent_chunks >= num_silent_chunks: silence_started = True
            else: silent_chunks = 0

        try:
            with sd.InputStream(samplerate=samplerate, channels=1, callback=callback, 
                                device=INPUT_DEVICE_NAME, blocksize=chunk_size): 
                while not silence_started and recorded_chunks < max_chunks:
                    sd.sleep(int(chunk_duration * 1000))
        except Exception as e: return None 
        
        return self.save_audio_buffer(buffer, filename, samplerate)

    def record_voice_ptt(self, filename="input.wav"):
        print("Recording (PTT)...", flush=True)
        time.sleep(0.5)
        try:
            device_info = sd.query_devices(kind='input')
            samplerate = int(device_info['default_samplerate'])
        except: samplerate = 44100 

        buffer = []
        def callback(indata, frames, time_info, status): buffer.append(indata.copy())
        
        try:
            with sd.InputStream(samplerate=samplerate, channels=1, callback=callback, device=INPUT_DEVICE_NAME):
                while self.recording_active.is_set(): sd.sleep(50)
        except Exception as e: return None
            
        return self.save_audio_buffer(buffer, filename, samplerate)

    def save_audio_buffer(self, buffer, filename, samplerate=16000):
        if not buffer: return None
        audio_data = np.concatenate(buffer, axis=0).flatten()
        audio_data = np.nan_to_num(audio_data, nan=0.0, posinf=0.0, neginf=0.0)
        audio_data = (audio_data * 32767).astype(np.int16)
        with wave.open(filename, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(samplerate)
            wf.writeframes(audio_data.tobytes())
        self.play_sound(self.get_random_sound(ack_sounds_dir))
        return filename

    def transcribe_audio(self, filename):
        print("Transcribing...", flush=True)
        try:
            result = subprocess.run(
                ["./whisper.cpp/build/bin/whisper-cli", "-m", "./whisper.cpp/models/ggml-base.en.bin", "-l", "en", "-t", "4", "-f", filename],
                capture_output=True, text=True
            )
            transcription_lines = result.stdout.strip().split('\n')
            if transcription_lines and transcription_lines[-1].strip():
                last_line = transcription_lines[-1].strip()
                if ']' in last_line: transcription = last_line.split("]")[1].strip()
                else: transcription = last_line
            else: transcription = ""
            print(f"Heard: '{transcription}'", flush=True)
            return transcription.strip()
        except Exception as e:
            print(f"Transcription Error: {e}")
            return ""

    def capture_image(self):
        self.set_state(BotStates.CAPTURING, "Watching...")
        try:
            subprocess.run(["rpicam-still", "-t", "500", "-n", "--width", "640", "--height", "480", "-o", BMO_IMAGE_FILE], check=True)
            rotation = CURRENT_CONFIG.get("camera_rotation", 0)
            if rotation != 0:
                img = Image.open(BMO_IMAGE_FILE)
                img = img.rotate(rotation, expand=True) 
                img.save(BMO_IMAGE_FILE)
            return BMO_IMAGE_FILE
        except Exception as e:
            print(f"Camera Error: {e}")
            return None

    # =========================================================================
    # 5. CHAT & RESPOND
    # =========================================================================

    def chat_and_respond(self, text, img_path=None):
        if "forget everything" in text.lower() or "reset memory" in text.lower():
            self.session_memory = []
            self.permanent_memory = [{"role": "system", "content": SYSTEM_PROMPT}]
            self.save_chat_history()
            with self.tts_queue_lock: 
                self.tts_queue.append("Okay. Memory wiped.")
            self.set_state(BotStates.IDLE, "Memory Wiped")
            return

        # Exit study mode if user says stop
        if self.pomodoro_active and ("stop" in text.lower() or "exit study" in text.lower() or "結束" in text):
            self.pomodoro_stop_event.set()
            self.pomodoro_active = False
            self.pomodoro_paused = False
            self.append_to_text("BOT: Study mode stopped.")
            with self.tts_queue_lock:
                self.tts_queue.append("Study mode stopped.")
            self.wait_for_tts()
            self.set_state(BotStates.IDLE, "Ready")
            return

        # When in study mode, route to Pomodoro controls (LLM-based)
        if self.pomodoro_active:
            handled, reply = self.handle_pomodoro_control(text)
            if handled:
                self.append_to_text(f"BOT: {reply}")
                with self.tts_queue_lock:
                    self.tts_queue.append(reply)
                self.wait_for_tts()
                self.set_state(BotStates.IDLE, "Ready")
                return

        # Start Pomodoro study mode
        if "study with me" in text.lower() or "start working" in text.lower():
            minutes = 25
            match = re.search(r'(?:for|about)\s+(\d+)\s*(?:min|minute|分鐘)?', text.lower())
            if match:
                minutes = min(60, max(1, int(match.group(1))))
            self.start_pomodoro(minutes)
            return

        model_to_use = VISION_MODEL if img_path else TEXT_MODEL
        self.set_state(BotStates.THINKING, "Thinking...", cam_path=img_path)
        
        messages = []
        if img_path:
            messages = [{"role": "user", "content": text, "images": [img_path]}]
        else:
            user_msg = {"role": "user", "content": text}
            messages = self.permanent_memory + self.session_memory + [user_msg]
        
        self.thinking_sound_active.set()
        threading.Thread(target=self._run_thinking_sound_loop, daemon=True).start()
        
        full_response_buffer = ""
        sentence_buffer = "" 
        
        try:
            stream = ollama.chat(model=model_to_use, messages=messages, stream=True, options=OLLAMA_OPTIONS)
            
            is_action_mode = False
            
            for chunk in stream:
                if self.interrupted.is_set(): break
                content = chunk.get("message", {}).get("content", "") or ""
                full_response_buffer += content
                
                if '{"' in content or "action:" in content.lower():
                    is_action_mode = True
                    self.thinking_sound_active.clear()
                    continue 

                if is_action_mode: continue

                self.thinking_sound_active.clear()
                if self.current_state != BotStates.SPEAKING:
                    self.set_state(BotStates.SPEAKING, "Speaking...", cam_path=img_path)
                    self.append_to_text("BOT: ", newline=False)

                self._stream_to_text(content)
                
                sentence_buffer += content
                if any(punct in content for punct in ".!?\n"):
                    clean_sentence = sentence_buffer.strip()
                    if clean_sentence and re.search(r'[a-zA-Z0-9]', clean_sentence):
                        with self.tts_queue_lock: self.tts_queue.append(clean_sentence)
                    sentence_buffer = ""

            if is_action_mode:
                print(f"[Action] LLM full_response_buffer: {full_response_buffer[:400]!r}", flush=True)
                action_data = self.extract_json_from_text(full_response_buffer)
                if not action_data or not isinstance(action_data, dict):
                    print(f"[Action] Invalid action_data (skip): {action_data!r}", flush=True)
                    tool_result = "INVALID_ACTION"
                else:
                    print(f"[Action] Parsed action_data: {action_data}", flush=True)
                    tool_result = self.execute_action_and_get_result(action_data)

                if tool_result and tool_result.startswith("CHAT_FALLBACK::"):
                    chat_text = tool_result.split("::", 1)[1]
                    self.thinking_sound_active.clear()
                    self.set_state(BotStates.SPEAKING, "Speaking...", cam_path=img_path)
                    self.append_to_text("BOT: ", newline=False)
                    self.append_to_text(chat_text, newline=True)
                    with self.tts_queue_lock: self.tts_queue.append(chat_text)
                    self.session_memory.append({"role": "assistant", "content": chat_text})
                    self.wait_for_tts()
                    self.set_state(BotStates.IDLE, "Ready")
                    return

                if tool_result == "IMAGE_CAPTURE_TRIGGERED":
                    new_img_path = self.capture_image()
                    if new_img_path:
                        self.chat_and_respond(text, img_path=new_img_path)
                    return

                elif tool_result == "INVALID_ACTION":
                    fallback_text = "I am not sure how to do that."
                    self.thinking_sound_active.clear()
                    self.set_state(BotStates.SPEAKING, "Speaking...", cam_path=img_path)
                    self.append_to_text("BOT: ", newline=False)
                    self.append_to_text(fallback_text, newline=True)
                    with self.tts_queue_lock: self.tts_queue.append(fallback_text)

                elif tool_result == "SEARCH_EMPTY":
                    fallback_text = "I searched, but I couldn't find any news about that."
                    self.thinking_sound_active.clear()
                    self.set_state(BotStates.SPEAKING, "Speaking...", cam_path=img_path)
                    self.append_to_text("BOT: ", newline=False)
                    self.append_to_text(fallback_text, newline=True)
                    with self.tts_queue_lock: self.tts_queue.append(fallback_text)

                elif tool_result == "SEARCH_ERROR":
                    fallback_text = "I cannot reach the internet right now."
                    self.thinking_sound_active.clear()
                    self.set_state(BotStates.SPEAKING, "Speaking...", cam_path=img_path)
                    self.append_to_text("BOT: ", newline=False)
                    self.append_to_text(fallback_text, newline=True)
                    with self.tts_queue_lock: self.tts_queue.append(fallback_text)

                elif tool_result:
                    summary_prompt = [
                        {"role": "system", "content": "Summarize this result in one short sentence."},
                        {"role": "user", "content": f"RESULT: {tool_result}\nUser Question: {text}"}
                    ]
                    self.set_state(BotStates.THINKING, "Reading...")
                    self.thinking_sound_active.set()
                    final_resp = ollama.chat(model=model_to_use, messages=summary_prompt, stream=False, options=OLLAMA_OPTIONS)
                    final_text = final_resp.get("message", {}).get("content", str(tool_result))
                    self.thinking_sound_active.clear()
                    self.set_state(BotStates.SPEAKING, "Speaking...", cam_path=img_path)
                    self.append_to_text("BOT: ", newline=False)
                    self.append_to_text(final_text, newline=True)
                    with self.tts_queue_lock: self.tts_queue.append(final_text)
                    self.session_memory.append({"role": "assistant", "content": final_text})
            else:
                self.append_to_text("")
                self.session_memory.append({"role": "assistant", "content": full_response_buffer}) 
            
            self.wait_for_tts()
            self.set_state(BotStates.IDLE, "Ready")
                
        except Exception as e:
            print(f"LLM Error: {e}")
            self.set_state(BotStates.ERROR, "Brain Freeze!")

    def wait_for_tts(self):
        while self.tts_queue or self.tts_active.is_set():
            if self.interrupted.is_set(): break
            time.sleep(0.1)

    def _tts_worker(self):
        while True:
            text = None
            with self.tts_queue_lock:
                if self.tts_queue: 
                    text = self.tts_queue.pop(0)
                    self.tts_active.set() 
            if text: 
                self.speak(text)
                self.tts_active.clear() 
            else: time.sleep(0.05)

    def speak(self, text):
        clean = re.sub(r"[^\w\s,.!?:-]", "", text)
        if not clean.strip(): return
        
        print(f"[PIPER SPEAKING] '{clean}'", flush=True)
        voice_model = CURRENT_CONFIG.get("voice_model", "piper/en_GB-semaine-medium.onnx")
        
        try:
            self.current_audio_process = subprocess.Popen(
                ["./piper/piper", "--model", voice_model, "--output-raw"], 
                stdin=subprocess.PIPE, 
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL
            )
            
            self.current_audio_process.stdin.write(clean.encode() + b'\n')
            self.current_audio_process.stdin.close() 

            try:
                device_info = sd.query_devices(kind='output')
                native_rate = int(device_info['default_samplerate'])
            except:
                native_rate = 48000 

            PIPER_RATE = 22050
            use_native_rate = False
            
            try:
                sd.check_output_settings(device=None, samplerate=PIPER_RATE)
            except:
                use_native_rate = True

            with sd.RawOutputStream(samplerate=native_rate if use_native_rate else PIPER_RATE, 
                                    channels=1, dtype='int16', 
                                    device=None, latency='low', blocksize=2048) as stream:
                while True:
                    if self.interrupted.is_set(): break
                    data = self.current_audio_process.stdout.read(4096)
                    if not data: break 
                    
                    audio_chunk = np.frombuffer(data, dtype=np.int16)
                    if len(audio_chunk) > 0:
                        self.current_volume = np.max(np.abs(audio_chunk))
                        if use_native_rate:
                            num_samples = int(len(audio_chunk) * (native_rate / PIPER_RATE))
                            audio_chunk = scipy.signal.resample(audio_chunk, num_samples).astype(np.int16)
                        stream.write(audio_chunk.tobytes())
                    else:
                        self.current_volume = 0
                time.sleep(0.5) 
                    
        except Exception as e:
            print(f"Audio Error: {e}")
        finally:
            self.current_volume = 0 
            if self.current_audio_process:
                if self.current_audio_process.stdout: self.current_audio_process.stdout.close()
                if self.current_audio_process.poll() is None: self.current_audio_process.terminate()
                self.current_audio_process = None

    def _run_thinking_sound_loop(self):
        time.sleep(0.5)
        while self.thinking_sound_active.is_set():
            sound = self.get_random_sound(thinking_sounds_dir)
            if sound: self.play_sound(sound)
            for _ in range(50):
                if not self.thinking_sound_active.is_set(): return
                time.sleep(0.1)

    def get_random_sound(self, directory):
        if os.path.exists(directory):
            files = [f for f in os.listdir(directory) if f.endswith(".wav")]
            return os.path.join(directory, random.choice(files)) if files else None
        return None

    def play_sound(self, file_path):
        if not file_path or not os.path.exists(file_path): return
        try:
            with wave.open(file_path, 'rb') as wf:
                file_sr = wf.getframerate()
                data = wf.readframes(wf.getnframes())
                audio = np.frombuffer(data, dtype=np.int16)

            try:
                device_info = sd.query_devices(kind='output')
                native_rate = int(device_info['default_samplerate'])
            except:
                native_rate = 48000 

            playback_rate = file_sr
            try:
                sd.check_output_settings(device=None, samplerate=file_sr)
            except:
                playback_rate = native_rate
                num_samples = int(len(audio) * (native_rate / file_sr))
                audio = scipy.signal.resample(audio, num_samples).astype(np.int16)

            sd.play(audio, playback_rate)
            sd.wait() 
        except: pass

    def load_chat_history(self):
        if os.path.exists(MEMORY_FILE):
            try:
                with open(MEMORY_FILE, "r") as f: return json.load(f)
            except: pass
        return [{"role": "system", "content": SYSTEM_PROMPT}]

    def save_chat_history(self):
        full = self.permanent_memory + self.session_memory
        conv = full[1:]
        if len(conv) > 10: conv = conv[-10:]
        with open(MEMORY_FILE, "w") as f: 
            json.dump([full[0]] + conv, f, indent=4)

if __name__ == "__main__":
    print("--- SYSTEM STARTING ---", flush=True)
    root = tk.Tk()
    app = BotGUI(root)
    root.mainloop()
