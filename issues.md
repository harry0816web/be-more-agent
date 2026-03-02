# Issues

## 1. OpenWeather API 401 Unauthorized

### Problem

`weather_svc` fetch fails with `401 Client Error: Unauthorized` even when API key is pasted correctly and shows "Active" on OpenWeather dashboard.

### Possible causes

1. **Email confirmation** – Green banner "We have sent the confirmation link to your email" means account is not fully activated. Click the link in the email.
2. **API version mismatch** – Free tier may only support Current Weather 2.5, not One Call 3.0.
3. **Key activation delay** – New keys can take 10 min–2 hours to propagate.
4. **Try Default key** – Use the first key (Default) instead of a newly created one.

### Implementation

- `weather_svc.py` tries One Call API 3.0 first, falls back to Current Weather 2.5 on 401
- API key: `.env` → `openweather_api_key` (via python-dotenv), fallback `config.json`

---

## 2. Idle display: time overlay (implemented)

### Behaviour

- Always show `faces/idle/idle 01.png` (single image, no cycling)
- Overlay compact time string at bottom: format `M/D HH:MM` (e.g. `02/27 23:59`)
- Update every second

### Implementation

- `idle_base_images` stores PIL copies of idle image(s) for overlay
- `idle_images_by_name` maps filename → PIL image for weather-based selection
- `load_animations` loads all PNGs from `faces/idle/` for weather variants
- `_generate_idle_with_time_overlay` uses `weather_svc.get_current_idle_image()` to pick base image
- `_generate_idle_with_time_overlay(frame_index)` draws time on base image, positioned at bottom center
- `update_animation` calls overlay with fixed frame 0, no frame cycling

---

## 3. Weather-based idle face (implemented)

### Behaviour

- `weather_svc` fetches OpenWeather API at the top of each hour (整點) in background
- Idle face switches by weather: `idle_sunny`, `idle_windy`, `idle_rainy`, `idle_cloudy`, `idle_default`
- Wind > 8 m/s → `idle_windy.png` (九降風)
- Agent action `get_weather` returns current weather for voice queries

### Required assets

Place in `faces/idle/`: `idle_default.png`, `idle_sunny.png`, `idle_windy.png`, `idle_rainy.png`, `idle_cloudy.png`. Falls back to first available PNG if missing.

### API key (dotenv)

- Uses `python-dotenv` to load `.env` at startup
- Read `openweather_api_key` or `OPENWEATHER_API_KEY` from env
- Optional: `weather_lat`, `weather_lon` in `.env` for coordinates
- Fallback: `config.json` if env vars not set
- `.env` is in `.gitignore` (do not commit API keys)

### Update schedule (整點更新)

- `_background_loop` sleeps until the next whole hour (e.g. 15:00, 16:00)
- `_seconds_until_next_hour()` computes seconds until next 整點
- After fetch, logs `[weather_svc] Next update at HH:MM`

---

## 4. Pomodoro Study Mode (implemented)

### Behaviour

- Say "start working" or "study with me for X minutes" to start
- Face switches to `faces/idle/idle_tomato.png`
- Clock overlay replaced with countdown (MM:SS), default 25 min
- When timer ends: celebration sound + TTS "辛苦了！喝口水站起來走一走喔！"
- Say "stop" or "exit study" to end early

### Implementation

- `chat_and_respond` pre-check for "study with me" / "start working"
- `start_pomodoro(minutes)` starts daemon countdown thread
- `_generate_idle_with_time_overlay` uses `idle_tomato.png` + countdown when `pomodoro_active`
- `sounds/celebration_sounds/` for completion WAV (fallback: `greeting_sounds`)

---

## 5. Pomodoro Study Mode Controls (implemented)

### Behaviour

When in study mode (`pomodoro_active`), user input is routed to LLM for semantic control:

- **Pause**: pause countdown (e.g. "pause", "hold on")
- **Resume**: resume countdown (e.g. "resume", "continue")
- **Reset**: restart timer with same duration (e.g. "reset", "restart")
- **Change duration**: set new minutes 1–60 (e.g. "change to 20 minutes")
- **Chat**: questions like "how much time left" → LLM replies with remaining time

### Implementation

- `handle_pomodoro_control(text)` → calls Ollama with `POMODORO_SYSTEM_PROMPT`, parses JSON
- `execute_pomodoro_action(action, value)` runs the tool (no string matching)
- Countdown thread skips decrement when `pomodoro_paused`
- `pomodoro_duration_minutes` stored for reset

### State

- `pomodoro_active`, `pomodoro_remaining_seconds`, `pomodoro_paused`, `pomodoro_duration_minutes`, `pomodoro_stop_event`

---

## 6. Bluetooth microphone not detected (PortAudio / PipeWire)

### Problem

When using a Bluetooth headset (e.g. AirPods Pro) with PipeWire, the agent fails with:

```
Wake Word Stream Error: Error querying device -1
```

- `sounddevice` (PortAudio) only sees ALSA devices (e.g. HDMI with 0 input channels)
- Default input device is `-1` (no default capture)
- Bluetooth microphone is managed by PipeWire and works with `pw-cat`, but PortAudio does not see it

### Root cause

- **PortAudio** uses **ALSA** on Linux and only enumerates ALSA devices
- **Bluetooth** is exposed through **PipeWire**, not directly through ALSA
- Without a bridge, PortAudio cannot access the PipeWire/Bluetooth microphone

### Solution

1. Install ALSA Pulse plugin:

   ```bash
   sudo apt install libasound2-plugins
   ```

2. Create `~/.asoundrc` to route ALSA default through Pulse/PipeWire:

   ```bash
   cat << 'EOF' > ~/.asoundrc
pcm.!default {
    type pulse
}
ctl.!default {
    type pulse
}
EOF
   ```
   ``` python
   import pyaudio
p = pyaudio.PyAudio()
for i in range(p.get_device_count()):
    dev = p.get_device_info_by_index(i)
    if dev.get('maxInputChannels') > 0:
        print(f"Index {i}: {dev.get('name')}")
p.terminate()
   ```

3. Ensure the Bluetooth headset is set as the default capture source in PipeWire (e.g. via `wpctl status` and system audio settings).

### Result

After applying the fix:

- ALSA default → Pulse plugin → PipeWire → Bluetooth microphone
- PortAudio (sounddevice) can now use the Bluetooth microphone via the ALSA default device
- Wake word and voice recording work with the Bluetooth headset

---

## 7. 在 SSH 環境中讓 GUI 顯示在 Raspberry Pi 螢幕上

### 方法一：設定 DISPLAY

在 SSH 裡執行前先設定顯示：

```bash
export DISPLAY=:0
python agent.py
```