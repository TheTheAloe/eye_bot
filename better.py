import time
import math
import smbus
import random
import speech_recognition as sr
import sys
import openai
from gtts import gTTS
import pygame
import tempfile
import os
import threading
from datetime import datetime

# ---------- PCA9685 Servo Control ----------
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

    def setServoAngle(self, channel, target_angle, move_time=0.5, step_size=1):
        target_angle = max(0, min(180, target_angle))
        current_angle = getattr(self, f'last_angle_{channel}', target_angle)

        step = step_size if target_angle > current_angle else -step_size
        steps = abs(target_angle - current_angle) // step_size

        if steps == 0:
            return

        for i in range(steps + 1):
            progress = i / steps
            speed_factor = 0.5 - 0.5 * math.cos(progress * math.pi)
            angle = current_angle + (target_angle - current_angle) * progress
            pulse = 500 + (angle / 180.0) * 2000
            pulse_length = int(pulse * 4096 / 20000)
            self.setPWM(channel, 0, pulse_length)
            time.sleep((move_time / steps) * speed_factor)

        setattr(self, f'last_angle_{channel}', target_angle)

    def setServoInstant(self, channel, angle):
        angle = max(0, min(180, angle))
        pulse = 500 + (angle / 180.0) * 2000
        pulse_length = int(pulse * 4096 / 20000)
        self.setPWM(channel, 0, pulse_length)
        setattr(self, f'last_angle_{channel}', angle)

    def setPWM(self, channel, on, off):
        self.write(self.__LED0_ON_L + 4 * channel, on & 0xFF)
        self.write(self.__LED0_ON_H + 4 * channel, on >> 8)
        self.write(self.__LED0_OFF_L + 4 * channel, off & 0xFF)
        self.write(self.__LED0_OFF_H + 4 * channel, off >> 8)

# ---------- Initialize Servos ----------
pwm = PCA9685()
pwm.setPWMFreq(50)

# ---------- Eye Config ----------
center_horizontal = 90
center_vertical = 85
servo0_min, servo0_max = 30, 140
servo1_min, servo1_max = 80, 90
blink_chance = 0.1
eye_thread_running = False
wakeword_detected = False
last_interaction_time = None

def close_eye():
    t1 = threading.Thread(target=pwm.setServoAngle, args=(2, 100, 0.3))
    t2 = threading.Thread(target=pwm.setServoAngle, args=(15, 80, 0.3))
    t1.start()
    t2.start()
    t1.join()
    t2.join()

def open_eye():
    t1 = threading.Thread(target=pwm.setServoAngle, args=(2, 170, 0.3))
    t2 = threading.Thread(target=pwm.setServoAngle, args=(15, 0, 0.3))
    t1.start()
    t2.start()
    t1.join()
    t2.join()

def center_eye():
    pwm.setServoAngle(0, center_horizontal, move_time=0.4)
    pwm.setServoAngle(1, center_vertical, move_time=0.4)

def eye_idle_loop():
    global eye_thread_running
    eye_thread_running = True
    while eye_thread_running:
        angle0 = random.randint(servo0_min, servo0_max)
        angle1 = random.randint(servo1_min, servo1_max)
        pwm.setServoAngle(0, angle0, move_time=0.5)
        pwm.setServoAngle(1, angle1, move_time=0.5)

        if random.random() < blink_chance:
            close_eye()
            time.sleep(0.3)
            open_eye()
            time.sleep(0.2)

        time.sleep(1)

def start_eye_thread():
    if not eye_thread_running:
        thread = threading.Thread(target=eye_idle_loop, daemon=True)
        thread.start()

def stop_eye_thread():
    global eye_thread_running
    eye_thread_running = False
    time.sleep(0.1)

# ---------- OpenAI + TTS ----------
client = openai.OpenAI(api_key="sk-proj-...")  # Replace with your OpenAI key
conversation_history = []

def speak(text):
    global wakeword_detected, last_interaction_time
    tts = gTTS(text=text, lang='en')
    with tempfile.NamedTemporaryFile(delete=False, suffix=".mp3") as fp:
        filename = fp.name
        tts.save(filename)

    pygame.mixer.init()
    pygame.mixer.music.load(filename)
    pygame.mixer.music.play()

    while pygame.mixer.music.get_busy():
        if wakeword_detected:
            print("Wakeword detected during speech. Interrupting...")
            pygame.mixer.music.stop()
            os.remove(filename)
            return
        continue

    os.remove(filename)
    last_interaction_time = datetime.now()

def generate_text(prompt):
    try:
        response = client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=conversation_history + [{"role": "user", "content": prompt}],
            max_tokens=150
        )
        return response.choices[0].message.content
    except Exception as e:
        print(f"Error calling OpenAI API: {e}")
        return "Error generating text."

# ---------- Wakeword Listener ----------
def wakeword_listener(wakeword="hey ai"):
    global wakeword_detected
    recognizer = sr.Recognizer()
    mic = sr.Microphone(device_index=3)

    with mic as source:
        recognizer.adjust_for_ambient_noise(source)
        while True:
            audio = recognizer.listen(source)
            try:
                text = recognizer.recognize_google(audio).lower()
                print(f"Heard: {text}")
                if wakeword in text:
                    wakeword_detected = True
            except sr.UnknownValueError:
                pass
            except sr.RequestError:
                continue

# ---------- Listen & Respond ----------
def listen_and_respond():
    global eye_thread_running

    stop_eye_thread()  # Stop eye movement during listening

    recognizer = sr.Recognizer()
    mic = sr.Microphone(device_index=3)

    with mic as source:
        print("Listening for user input...")
        recognizer.adjust_for_ambient_noise(source)
        audio = recognizer.listen(source)

    start_eye_thread()  # Resume eye movement

    try:
        text = recognizer.recognize_google(audio)
        print(f"You said: {text}")
        return text
    except sr.UnknownValueError:
        print("Sorry, I didn't catch that. Can you repeat?")
        speak("Sorry, I didn't catch that. Can you repeat?")
        return None
    except sr.RequestError:
        print("Speech recognition error.")
        speak("Sorry, there was an error with speech recognition.")
        return None

# ---------- Main Logic ----------
def main():
    global wakeword_detected, last_interaction_time

    threading.Thread(target=wakeword_listener, daemon=True).start()

    # Force servos to start position instantly
    pwm.setServoInstant(0, center_horizontal)
    pwm.setServoInstant(1, center_vertical)
    pwm.setServoInstant(2, 100)
    pwm.setServoInstant(15, 80)

    while True:
        if wakeword_detected:
            wakeword_detected = False
            open_eye()
            start_eye_thread()
            speak("What's up!")
            is_in_conversation = True
            last_interaction_time = datetime.now()

            while is_in_conversation:
                if last_interaction_time and (datetime.now() - last_interaction_time).total_seconds() > 10:
                    speak("I'll go back to standby now.")
                    stop_eye_thread()
                    close_eye()
                    center_eye()
                    is_in_conversation = False
                    break

                if wakeword_detected:
                    wakeword_detected = False
                    speak("Yes?")
                    last_interaction_time = datetime.now()

                user_input = listen_and_respond()
                if user_input:
                    last_interaction_time = datetime.now()
                    if "end chat" in user_input.lower():
                        speak("Goodbye! I'll be listening for the wakeword again.")
                        stop_eye_thread()
                        close_eye()
                        center_eye()
                        is_in_conversation = False
                    elif "kill" in user_input.lower():
                        speak("Terminating the script. Goodbye!")
                        stop_eye_thread()
                        close_eye()
                        center_eye()
                        sys.exit()
                    else:
                        conversation_history.append({"role": "user", "content": user_input})
                        response = generate_text(user_input)
                        print(f"AI says: {response}")
                        conversation_history.append({"role": "assistant", "content": response})
                        speak(response)
                        last_interaction_time = datetime.now()

if __name__ == "__main__":
    main()
