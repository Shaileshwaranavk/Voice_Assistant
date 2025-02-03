import speech_recognition as sr
import pyttsx3
import datetime
import wikipedia
import os
from pygoogle_image import image
import pywhatkit
import pyaudio
import time
import tkinter as tk
from tkinter import PhotoImage

def write_to_file(content):
    file_name = "example.txt"
    with open(file_name, 'w') as file:
        file.write(content)

def read_from_file():
    file_name = "example.txt"
    if os.path.exists(file_name):
        with open(file_name, 'r') as file:
            content = file.read()
            return content
    else:
        return "The file does not exist."

def download_images(query):
    image.download(keywords=query, limit=30)

def speak(text):
    engine = pyttsx3.init()
    engine.say(text)
    engine.runAndWait()

def shutdown():
    os.system("shutdown /s /t 1")

def get_date():
    now = datetime.datetime.now()
    return now.strftime("%Y-%m-%d")

def get_time():
    now = datetime.datetime.now()
    return now.strftime("%H:%M:%S")

def recognize_speech():
    recognizer = sr.Recognizer()
    with sr.Microphone() as source:
        recognizer.adjust_for_ambient_noise(source)
        try:
            audio = recognizer.listen(source, timeout=10, phrase_time_limit=15)
            text = recognizer.recognize_google(audio)
            return text.lower()
        except sr.WaitTimeoutError:
            return None
        except sr.UnknownValueError:
            return None
        except sr.RequestError:
            return None
        except Exception:
            return None

def main():
    speak("Hi, I am Cypher. How can I help you today?")

    while True:
        command = recognize_speech()
        if command:
            if "shutdown" in command:
                shutdown()
                break
            elif "date" in command or "what is the date" in command:
                speak(f"The current date is {get_date()}")
            elif "time" in command or "what is the time" in command:
                speak(f"The current time is {get_time()}")
            elif "stop" in command or "goodbye" in command:
                speak("Goodbye!")
                break
            elif "play" in command:
                query = command.replace("play", "").strip()
                speak(f"Searching for '{query}' on YouTube...")
                pywhatkit.playonyt(query)
            elif "download images of" in command:
                query = command.replace("download images of", "").strip()
                speak(f"Downloading images of '{query}'")
                download_images(query)
            elif "search" in command:
                search_query = command.replace("search", "").strip()
                result = wikipedia.summary(search_query, sentences=2)
                speak(f"According to Wikipedia: {result}")
            elif "screenshot" in command:
                pywhatkit.take_screenshot(file_name="ss", delay=5, show=True)
            elif "edit" in command:
                speak("What would you like to write in the file?")
                content = recognize_speech()
                if content:
                    write_to_file(content)
                    speak("Content has been written to the file.")
                else:
                    speak("Sorry, I couldn't understand you.")
            elif "read" in command:
                speak("Reading content from the file.")
                content = read_from_file()
                speak(content)
            else:
                speak("Sorry, I didn't understand that command.")
        else:
            time.sleep(1)

def start_assistant():
    main()

root = tk.Tk()
root.title("Cypher Voice Assistant")
root.geometry("600x400")

img = PhotoImage(file="cypher.png")

button = tk.Button(root, image=img, command=start_assistant)
button.pack()

root.mainloop()
