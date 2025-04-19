# --- Your usual imports ---
import time
import math
import smbus
import random
import threading
import speech_recognition as sr
from gtts import gTTS
import pygame
import tempfile
import pyaudio
import openai

# --------- CONFIG ---------
WAKE_WORD = "hey hey"
CONVERSATION_TIMEOUT = 15
LISTENING_TIMEOUT = 10
MAX_PHRASE_LENGTH = 15
openai.api_key = ""
# --------- PCA9685 CLASS ---------
class PCA9685:
    __MODE1 = 0x00
    __PRESCALE = 0xFE
    __LED0_ON_L = 0x06
    __LED0_ON_H = 0x07
    __LED0_OFF_L = 0x08
    __LED0_OFF_H = 0x09

    def __init__(self, address=0x40):
        self.bus = smbus.SMBus(1)
        self.address = address
        self.write(self.__MODE1, 0x00)

    def write(self, reg, value):
        self.bus.write_byte_data(self.address, reg, value)

    def setPWMFreq(self, freq):
        prescaleval = 25000000.0 / 4096.0 / float(freq) - 1
        prescale = math.floor(prescaleval + 0.5)
        oldmode = self.bus.read_byte_data(self.address, self.__MODE1)
        self.write(self.__MODE1, oldmode & 0x7F | 0x10)
        self.write(self.__PRESCALE, int(prescale))
        self.write(self.__MODE1, oldmode)
        time.sleep(0.005)
        self.write(self.__MODE1, oldmode | 0x80)

    def setPWM(self, channel, on, off):
        self.write(self.__LED0_ON_L + 4 * channel, on & 0xFF)
        self.write(self.__LED0_ON_H + 4 * channel, on >> 8)
        self.write(self.__LED0_OFF_L + 4 * channel, off & 0xFF)
        self.write(self.__LED0_OFF_H + 4 * channel, off >> 8)

    def setServoInstant(self, channel, angle):
        angle = max(0, min(180, angle))
        pulse = 500 + (angle / 180.0) * 2000
        pulse_length = int(pulse * 4096 / 20000)
        self.setPWM(channel, 0, pulse_length)
        setattr(self, f'last_angle_{channel}', angle)

    def setServoAngle(self, channel, target_angle, move_time=0.05, step_size=1):
        target_angle = max(0, min(180, target_angle))
        current_angle = getattr(self, f'last_angle_{channel}', target_angle)
        step = step_size if target_angle > current_angle else -step_size
        steps = abs(target_angle - current_angle) // step_size
        if steps == 0: return
        for i in range(steps + 1):
            progress = i / steps
            speed_factor = 0.5 - 0.5 * math.cos(progress * math.pi)
            angle = current_angle + (target_angle - current_angle) * progress
            pulse = 500 + (angle / 180.0) * 2000
            pulse_length = int(pulse * 4096 / 20000)
            self.setPWM(channel, 0, pulse_length)
            time.sleep((move_time / steps) * speed_factor)
        setattr(self, f'last_angle_{channel}', target_angle)

# --------- SERVO SETUP ---------
pwm = PCA9685()
pwm.setPWMFreq(50)
center_horizontal = 90
center_vertical = 85
servo0_min, servo0_max = 30, 140
servo1_min, servo1_max = 80, 90
blink_chance = 0.1
eye_thread_running = False

def close_eye():
    pwm.setServoAngle(2, 100, 0.05)
    pwm.setServoAngle(15, 80, 0.05)

def open_eye():
    pwm.setServoAngle(2, 170, 0.05)
    pwm.setServoAngle(15, 0, 0.05)

def center_eye():
    pwm.setServoAngle(0, center_horizontal, 0.4)
    pwm.setServoAngle(1, center_vertical, 0.4)

def eye_idle_loop():
    global eye_thread_running
    eye_thread_running = True
    while eye_thread_running:
        pwm.setServoAngle(0, random.randint(servo0_min, servo0_max), 0.5)
        pwm.setServoAngle(1, random.randint(servo1_min, servo1_max), 0.5)
        if random.random() < blink_chance:
            close_eye()
            time.sleep(0.05)
            open_eye()
            time.sleep(0.05)
        time.sleep(1)

def start_eye_thread():
    global eye_thread_running
    if not eye_thread_running:
        threading.Thread(target=eye_idle_loop, daemon=True).start()

def stop_eye_thread():
    global eye_thread_running
    eye_thread_running = False

# --------- GOODBYE HANDLER ---------
def handle_goodbye():
    stop_eye_thread()
    center_eye()
    close_eye()
    pygame.mixer.music.stop()
    speak("Goodbye.")
    speak("Okay, I'm listening again.")

# --------- AUDIO + CHAT SETUP ---------
def get_correct_input_device():
    p = pyaudio.PyAudio()
    for i in range(p.get_device_count()):
        dev = p.get_device_info_by_index(i)
        if "TKGOU" in dev["name"] or "USB" in dev["name"]:
            return i
    return None

MIC_DEVICE_INDEX = get_correct_input_device()
if MIC_DEVICE_INDEX is None:
    raise RuntimeError("Mic not found!")

pygame.mixer.init()
stop_talking = threading.Event()
audio_lock = threading.Lock()
interrupt_listening = False

conversation_history = [
    {"role": "system", "content": "You are a helpful assistant. Keep responses concise and natural for voice interaction."}
]
last_interaction_time = time.time()
conversation_active = False

# --------- TTS ---------
def speak(text):
    global interrupt_listening, last_interaction_time
    print("AI says:", text)
    if not text.strip():
        return

    interrupt_listening = True
    interruption_thread = threading.Thread(target=listen_for_interruption)
    interruption_thread.daemon = True
    interruption_thread.start()

    tts = gTTS(text=text, lang='en')
    with tempfile.NamedTemporaryFile(delete=True, suffix=".mp3") as fp:
        tts.save(fp.name)
        pygame.mixer.music.load(fp.name)
        pygame.mixer.music.play()

        while pygame.mixer.music.get_busy():
            if stop_talking.is_set():
                pygame.mixer.music.stop()
                stop_talking.clear()
                break
            time.sleep(0.1)

    interrupt_listening = False
    interruption_thread.join(timeout=0.1)
    last_interaction_time = time.time()

# --------- INTERRUPT HANDLER ---------
def listen_for_interruption():
    global conversation_active
    recognizer = sr.Recognizer()
    mic = sr.Microphone(device_index=MIC_DEVICE_INDEX)

    while interrupt_listening:
        with audio_lock:
            try:
                with mic as source:
                    recognizer.adjust_for_ambient_noise(source, duration=0.2)
                    audio = recognizer.listen(source, timeout=1, phrase_time_limit=1)
                command = recognizer.recognize_google(audio).lower()
                print("Heard during speech:", command)

                if "goodbye" in command:
                    stop_talking.set()
                    conversation_active = False
                    return

                elif WAKE_WORD in command:
                    stop_talking.set()
                    return

            except:
                continue

# --------- CHAT HANDLER ---------
def chat_with_gpt(prompt):
    global conversation_history, last_interaction_time
    conversation_history.append({"role": "user", "content": prompt})
    try:
        client = openai.OpenAI(api_key=openai.api_key)
        chat = client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=conversation_history
        )
        response = chat.choices[0].message.content.strip()
        conversation_history.append({"role": "assistant", "content": response})
        if len(conversation_history) > 10:
            conversation_history = [conversation_history[0]] + conversation_history[-9:]
        last_interaction_time = time.time()
        return response
    except Exception as e:
        print("ChatGPT error:", e)
        return "Sorry, I couldn't process that."

# --------- LISTEN FOR SPEECH ---------
def listen_for_audio():
    recognizer = sr.Recognizer()
    with audio_lock:
        with sr.Microphone(device_index=MIC_DEVICE_INDEX) as source:
            try:
                recognizer.adjust_for_ambient_noise(source, duration=0.5)
                audio = recognizer.listen(source, timeout=LISTENING_TIMEOUT, phrase_time_limit=MAX_PHRASE_LENGTH)
                transcript = recognizer.recognize_google(audio).lower()
                print("You said:", transcript)
                return transcript
            except:
                print("Could not understand or timeout")
                return None

# --------- CHAT LOOP ---------
def handle_chat():
    global conversation_active
    while conversation_active:
        response = listen_for_audio()
        if not response:
            continue
        if "goodbye" in response:
            conversation_active = False
            handle_goodbye()
            return
        reply = chat_with_gpt(response)
        speak(reply)

# --------- WAKEWORD DETECTION ---------
def listen_for_wakeword():
    global conversation_active, last_interaction_time
    recognizer = sr.Recognizer()
    mic = sr.Microphone(device_index=MIC_DEVICE_INDEX)
    print("Listening for wakeword...")

    # ⏸ Reset eye movement + close lids while idle
    stop_eye_thread()
    center_eye()
    close_eye()

    while True:
        try:
            with mic as source:
                recognizer.adjust_for_ambient_noise(source, duration=0.5)
                audio = recognizer.listen(source, timeout=1, phrase_time_limit=2)
            command = recognizer.recognize_google(audio).lower()
            print("Heard:", command)
            if WAKE_WORD in command:
                last_interaction_time = time.time()
                conversation_active = True
                open_eye()

                # 👁 Double blink
                for _ in range(2):
                    close_eye()
                    time.sleep(0.05)
                    open_eye()
                    time.sleep(0.05)

                start_eye_thread()
                handle_chat()
                return
        except:
            continue

# --------- MAIN LOOP ---------
if __name__ == "__main__":
    pwm.setServoInstant(0, center_horizontal)
    pwm.setServoInstant(1, center_vertical)
    pwm.setServoInstant(2, 100)  # eyelid closed
    pwm.setServoInstant(15, 80)  # eyelid closed

    while True:
        listen_for_wakeword()
