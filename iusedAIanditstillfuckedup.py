import time
import threading
import speech_recognition as sr
from gtts import gTTS
import pygame
import tempfile
import pyaudio
import openai

# --- SETTINGS ---
MIC_DEVICE_INDEX = None  # Will be auto-detected
openai.api_key = "u no take my key how#e"
WAKE_WORD = "hey hey"  # Make this case-insensitive
CONVERSATION_TIMEOUT = 15  # seconds of silence before resetting conversation
LISTENING_TIMEOUT = 10  # seconds to wait for user to start speaking
MAX_PHRASE_LENGTH = 15  # seconds to allow for continuous speech

# --- Detect USB Mic ---
def get_correct_input_device():
    p = pyaudio.PyAudio()
    for i in range(p.get_device_count()):
        dev = p.get_device_info_by_index(i)
        if "TKGOU" in dev["name"] or "USB" in dev["name"]:
            return i
    return None

MIC_DEVICE_INDEX = get_correct_input_device()
if MIC_DEVICE_INDEX is None:
    raise RuntimeError("Correct microphone not found!")

print(f"Using microphone index: {MIC_DEVICE_INDEX}")

# --- Audio Setup ---
pygame.mixer.init()
stop_talking = threading.Event()
audio_lock = threading.Lock()
interrupt_listening = False

# --- Conversation State ---
conversation_history = [
    {"role": "system", "content": "You are a helpful assistant. Keep responses concise and natural for voice interaction."}
]
last_interaction_time = time.time()
conversation_active = False

# --- TTS ---
def speak(text):
    global interrupt_listening, last_interaction_time
    
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
    
    last_interaction_time = time.time()
    interrupt_listening = False
    interruption_thread.join(timeout=0.1)

# --- Interruption Detector ---
def listen_for_interruption():
    recognizer = sr.Recognizer()
    mic = sr.Microphone(device_index=MIC_DEVICE_INDEX)
    
    while interrupt_listening:
        with audio_lock:
            try:
                with mic as source:
                    recognizer.adjust_for_ambient_noise(source, duration=0.5)
                    audio = recognizer.listen(source, timeout=1, phrase_time_limit=1)
                command = recognizer.recognize_google(audio).lower()
                print("Heard during speech:", command)
                if WAKE_WORD in command:
                    stop_talking.set()
                    return
            except (sr.UnknownValueError, sr.WaitTimeoutError):
                continue
            except Exception as e:
                print(f"Interruption listener error: {e}")
                continue

# --- GPT Response ---
def chat_with_gpt(prompt):
    global conversation_history, last_interaction_time
    
    # Add user message to history
    conversation_history.append({"role": "user", "content": prompt})
    
    try:
        client = openai.OpenAI(api_key=openai.api_key)
        chat = client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=conversation_history
        )
        response = chat.choices[0].message.content.strip()
        
        # Add assistant response to history (but keep it from growing too large)
        conversation_history.append({"role": "assistant", "content": response})
        if len(conversation_history) > 10:  # Keep last 5 exchanges (10 messages)
            conversation_history = [conversation_history[0]] + conversation_history[-9:]
            
        last_interaction_time = time.time()
        return response
    except Exception as e:
        print("ChatGPT error:", e)
        return "Sorry, I couldn't process that."

# --- Listen for Speech with More Patience ---
def listen_for_audio(timeout=None):
    recognizer = sr.Recognizer()
    recognizer.pause_threshold = 1.0  # Longer pause before considering speech ended
    recognizer.phrase_threshold = 0.3  # Lower threshold for speech detection
    
    with audio_lock:
        with sr.Microphone(device_index=MIC_DEVICE_INDEX) as source:
            try:
                print("Listening... (waiting for you to start speaking)")
                recognizer.adjust_for_ambient_noise(source, duration=0.5)
                
                # First listen for speech start with a short timeout
                audio = recognizer.listen(source, timeout=LISTENING_TIMEOUT)
                
                print("Got speech start, now listening to full question...")
                # Once speech starts, give more time for the full question
                audio = recognizer.listen(source, phrase_time_limit=MAX_PHRASE_LENGTH)
                
                return recognizer.recognize_google(audio).lower()
            except sr.WaitTimeoutError:
                print("Listening timeout - no speech detected")
                return None
            except sr.UnknownValueError:
                print("Could not understand audio")
                return None
            except Exception as e:
                print(f"Audio error: {e}")
                return None

# --- Check if conversation should timeout ---
def check_conversation_timeout():
    global conversation_active, conversation_history, last_interaction_time
    
    while True:
        time.sleep(5)
        if (conversation_active and 
            time.time() - last_interaction_time > CONVERSATION_TIMEOUT):
            print("Conversation timeout - resetting")
            conversation_active = False
            conversation_history = [
                {"role": "system", "content": "You are a helpful assistant. Keep responses concise and natural for voice interaction."}
            ]

# --- Wakeword Detection ---
def listen_for_wakeword():
    global conversation_active, last_interaction_time
    
    recognizer = sr.Recognizer()
    mic = sr.Microphone(device_index=MIC_DEVICE_INDEX)
    
    print("Listening for wakeword...")
    while True:
        try:
            with mic as source:
                recognizer.adjust_for_ambient_noise(source, duration=0.5)
                audio = recognizer.listen(source, timeout=1, phrase_time_limit=2)
            
            command = recognizer.recognize_google(audio).lower()
            print("Heard:", command)
            
            if WAKE_WORD in command:
                last_interaction_time = time.time()
                if not conversation_active:
                    conversation_active = True
                    handle_chat()
                else:
                    # If already in conversation, just interrupt current speech
                    stop_talking.set()
                    pygame.mixer.music.stop()
                    
        except sr.WaitTimeoutError:
            continue
        except sr.UnknownValueError:
            continue
        except Exception as e:
            print(f"Wakeword listener error: {e}")
            continue

# --- Chat Loop ---
def handle_chat():
    global conversation_active, last_interaction_time
    
    while conversation_active:
        speak("Yes?")  # Acknowledge wake word
        
        response = listen_for_audio()
        if not response:
            if time.time() - last_interaction_time > CONVERSATION_TIMEOUT:
                speak("I didn't hear anything. Goodbye.")
                conversation_active = False
                return
            continue
            
        print("You said:", response)
        if "goodbye" in response or "stop" in response:
            speak("Okay, goodbye!")
            conversation_active = False
            return

        reply = chat_with_gpt(response)
        speak(reply)

# --- Run Main Loop ---
if __name__ == "__main__":
    # Start conversation timeout checker
    timeout_thread = threading.Thread(target=check_conversation_timeout)
    timeout_thread.daemon = True
    timeout_thread.start()
    
    # Start wakeword listener in main thread
    listen_for_wakeword()
