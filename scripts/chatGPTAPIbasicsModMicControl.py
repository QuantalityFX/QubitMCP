#!/usr/bin/env python
# coding: utf-8

# In[ ]:


import gradio as gr
import openai
from gtts import gTTS
import os
import pygame
import tempfile
import speech_recognition as sr
import concurrent.futures
import threading
from spotify_play import play_track, stop_track, get_devices, playback_state
from YoutubeDownloade import YDownload
from YoutubeDownloadeViewer import YoutubeDownloadeViewer
from SequenceReformat import SequenceReformat
from openaiWebScraper import GetTemperature
from mp4_to_mp3 import mp4_to_mp3
from mp4_to_sequence import mp4_to_sequence
from speaker_split import split_speakers
from sepformer_split import split_and_separate
import HTTPServerCreate
from BingSearch import search
import subprocess
import time
import re
from pymongo import MongoClient
import webbrowser
import tiktoken
import sys
import uuid, tempfile
import shutil
from pathlib import Path
import tempfile
import threading, time, webbrowser
from youtube_utils import get_playlist_from_mongo, get_video_id, get_youtube_playlist_url

from tools.TanyaVision.tanyavision_ui import run_ocr_tool
from tools.TanyaVision.refbuilder_ui import render_refbuilder_panel
from tools.TanyaVision.gradio_ocr_ui import render_tanyavision_dual_panel

TOOLS_CHOICES = ["None", "TanyaVision — Build Atlas", "TanyaVision — Dota 2 OCR"]

#print(sys.executable)

chat_history = []
#enc = tiktoken.encoding_for_model("gpt-4") #for gpt-4
enc = tiktoken.get_encoding("o200k_base")
openai.api_key = open("key.txt", "r").read().strip("\n")
message_history = [{"role": "user", "content": f"You are a helpful assistant named Tanya. I will specify the subject matter in my messages, and you will reply acordingly as an expert in the field being dicused. I want you to act casual and not so formal I want you to just talk to me, respond with short answers and , prioritize show code or practical steps, avoid giving 10 steps at once, instead try giving one step at the time so that its easier to follow step by step instructions, think hard about my prompt, once done a step move on to the next step. If you understand, say OK."},
                   {"role": "assistant", "content": f"OK"}]
VoiceAssistCount = 0
VoiceSaid = None
stop_audio = threading.Event()
voice_enabled = True 
reply_content = None
trackNum = 0
SongPlaylist = None
device_id = None
response = None
voice_checkbox = voice_enabled  # UI reflects the true initial state
internet_checkbox = False  # Global variable to keep track of internet checkbox state
tanya_checkbox = False
mic_on = False
selected_model = "gpt-4o"  # default

#reply_content = 'Certainly! There are many relaxing songs to choose from, but here are a few popular examples: "Weightless" by Marconi Union "Watermark" by Enya "Clair de Lune" by Debussy "Spiegel im Spiegel" by Arvo Pärt "Gymnopédie No.1" by Erik Satie I hope you find these suggestions helpful!'
#VOICE PROMPT = give me five sci-fi songs
#reply_content = 'Sure, here are five sci-fi songs that you might enjoy: 1. "Starman" by David Bowie 2. "Intergalactic" by Beastie Boys 3. "Space Oddity" by David Bowie 4. "Mr. Roboto" by Styx 5. "The Final Countdown" by Europe'
#reply_content = ' "Unchained Melody" by The Righteous Brothers 2. "My Heart Will Go On" by Celine Dion 3. "I Will Always Love You" by Whitney Houston'
#reply_content = '“Amor Eterno” by Juan Gabriel “Bésame Mucho” by Consuelo Velázquez “La Barca” by Luis Miguel "Por Debajo De La Mesa" "Hasta Que Me Olvides" "La Incondicional"'

enable_split_ui = os.environ.get("ENABLE_SPLIT_UI") == "1" or sys.platform.startswith("linux")
'''
if enable_split_ui:
    from split_by_speaker import split_by_speaker
    from nemo.collections.asr.models import SortformerEncLabelModel


    diar_model = SortformerEncLabelModel.from_pretrained(
        "nvidia/diar_sortformer_4spk-v1"
    )
    diar_model.eval()

    def run_split(audio_path):
        segments = diar_model.diarize(audio=audio_path, batch_size=1)
        return split_by_speaker(audio_path, segments)
else:
    def run_split(audio_path):
        raise RuntimeError("Speaker split not available on this platform/config. Run inside Docker or on Linux with ENABLE_SPLIT_UI=1.")
'''
if enable_split_ui:
    from split_by_speaker import split_by_speaker
    from nemo.collections.asr.models import SortformerEncLabelModel


    diar_model = SortformerEncLabelModel.from_pretrained(
        "nvidia/diar_sortformer_4spk-v1"
    )
    diar_model.eval()

    def run_split(audio_path):
        segments = diar_model.diarize(audio=audio_path, batch_size=1)
        return split_by_speaker(audio_path, segments)
else:
    def run_split(audio_path):
        raise RuntimeError("Speaker split not available on this platform/config. Run inside Docker or on Linux with ENABLE_SPLIT_UI=1.")


def speak(textIn):
    if not voice_enabled:        # don't TTS if disabled
        return
    tts = gTTS(text=textIn, lang="en")
    #tts = gTTS(text=textIn, lang="ja")
    #with tempfile.NamedTemporaryFile(delete=False, suffix=".mp3") as fp:
    #    file_name = fp.name

    tmp = tempfile.gettempdir()
    file_name = os.path.join(tmp, f"tts_{uuid.uuid4().hex}.mp3")
    tts.save(file_name)

    play_audio_file(file_name)

def play_audio_file(file_name):
    global stop_audio
    if os.environ.get("NO_AUDIO") or not voice_enabled:
        return

    import pygame
    if not pygame.mixer.get_init():
        pygame.mixer.init()

    stop_audio.clear()           # <-- CRITICAL: allow new playback
    pygame.mixer.music.load(file_name)
    pygame.mixer.music.play()

    while pygame.mixer.music.get_busy():
        if stop_audio.is_set():
            pygame.mixer.music.stop()
            break
        pygame.time.Clock().tick(10)

    pygame.mixer.music.unload()

def get_audio():
    global VoiceSaid
    r = sr.Recognizer()
    with sr.Microphone() as source:
        #increasing the timeout parameter to a higher value, such as 10 seconds, to allow more time for the user to speak. 
        #increase the phrase_time_limit parameter to a higher value, such as 20 seconds, to allow the listen()
        r.adjust_for_ambient_noise(source, duration=1)
        print("Listening...")
        audio = r.listen(source, timeout=None, phrase_time_limit=20)
        VoiceSaid = ""

        try:
            VoiceSaid = r.recognize_google(audio)
            print(VoiceSaid)
        except Exception as e:
            print("Exception: " + str(e))
    return VoiceSaid

speak("hello, I'm Here")


def predictTest():
    speak("predictTest is running")
    
#To count tokens exactly as OpenAI would, you'd need to use the same tokenization logic. OpenAI provides a Python library called tiktoken
def count_tokens(message_content_text):
    print("Inside count_tokens function")
    #enc = tiktoken.encoding_for_model("gpt-4")
    enc = tiktoken.get_encoding("o200k_base") #new version of tiktoken
    tokens = enc.encode(message_content_text)
    token_count = len(tokens)
    print(f"Current Tokens for text '{message_content_text}' is = {token_count}\n")
    return len(tokens)

# Limit the total tokens in message history to fit within model's limit
#This should give you a more accurate token count and help you better manage your message_history.

def truncate_message_history(message_history, max_tokens=4096):
    total_tokens = 0
    truncated_history = []

    for message in reversed(message_history):
        message_content = message.get("content", "")
        message_tokens = count_tokens(message_content)
        if total_tokens + message_tokens > max_tokens:
            break
        total_tokens += message_tokens
        truncated_history.insert(0, message)
    print(f"Total tokens: {total_tokens}\n")
    return truncated_history

def predict(input):
    global VoiceSaid
    if VoiceSaid is None:
        VoiceSaid = ""

    # …rest of your code uses the global VoiceSaid…
    global reply_content
    global VoiceAssistCount
    global response
    global message_history
    is_ui = chat_history is not None

    print(f"\n internet_checkbox value: {internet_checkbox}\n")

    if internet_checkbox:
        # Perform Bing search and format results
        # Handle None value for VoiceSaid
        VoiceSaid = "" if VoiceSaid is None else VoiceSaid

        search_query = input + VoiceSaid
        search_results = search(search_query)

        max_snippet_length = 200  # set the maximum snippet length
        formatted_search_results = [f"Source:\nTitle: {result['name']}\nURL: {result['url']}\nContent: {result['snippet'][:max_snippet_length]}..." for result in search_results]
        #formatted_search_results = [f"Source:\nTitle: {result['name']}\nURL: {result['url']}\nContent: {result['snippet']}" for result in search_results]
        prompt = "Use these sources to answer the question:\n\n" + \
            "\n\n".join(formatted_search_results) + "\n\nQuestion: " + input + VoiceSaid + "\n\nAnswer:"
   
        print(prompt)
        # In your predict function

        new_message = {"role": "user", "content": f"{prompt}"}
        new_message_tokens = len(new_message['content'].split())
        max_tokens = 32000  - new_message_tokens  # Reserve tokens for the new message

        message_history = truncate_message_history(message_history, max_tokens)
        message_history.append(new_message)
    
    else:
        # speak("predicting")
        # tokenize the new input sentence

        new_message = {"role": "user", "content": f"{input} {VoiceSaid}"}
        new_message_tokens = len(new_message['content'].split())
        max_tokens = 32000 - new_message_tokens  # Reserve tokens for the new message

        message_history = truncate_message_history(message_history, max_tokens)
        message_history.append(new_message)

    temperature = 1 if selected_model.startswith("gpt-5") else 0.6

    completion = openai.ChatCompletion.create(
        model=selected_model,
        messages=message_history,
        temperature=temperature,
        top_p=1,
    )
    
    #Just the reply:
    reply_content = completion.choices[0].message.content#.replace('```python', '<pre>').replace('```', '</pre>')
    print(reply_content)

    '''
    # Check if the reply contains Python code
    if "python code" in reply_content.lower():
        python_code_snippet = "def greet(name):\n    return 'Hello, ' + name"
        reply_content = f"Python code snippet: <code>{python_code_snippet}</code>"
    '''
    # Add the reply to message history
    message_history.append({"role": "assistant", "content": f"{reply_content}"}) 
    
    #update_ui()
    # get pairs of msg["content"] from message history, skipping the pre-prompt:              here.
    #response = [(message_history[i]["content"], message_history[i+1]["content"]) for i in range(2, len(message_history)-1, 2)]  # convert to tuples of list
    #speak("Are We Done Talking?")
    #get_audio()
    #speak("Predicting") 

    # now update history & UI
    chat_history.append((input, reply_content))

    # Only speak if TTS is enabled *and* chatty mode is on
    if voice_enabled and VoiceAssistCount > 0:
        speak(reply_content)

    return chat_history, ""


#Extract Song Name---------------------------------
def extract_song_names(reply_content):
    pattern = r'"([^"]*)"'
    song_names = re.findall(pattern, reply_content)
    print("Extracted song names:", song_names)  # Debug output
    return song_names
#-------------------------------------------------
#Control Loop
#Wrap the get_audio function in a while loop 
def my_function():
    speak("Function executed!")
    # This function will be executed when the button is clicked

def VoiceOff():
    global VoiceAssistCount, voice_enabled, stop_audio
    voice_enabled = False       # block TTS
    stop_audio.set()            # stop current playback
    VoiceAssistCount = 0        # chatty mode OFF
    print("Voice Off button pressed")

def VoiceOn():
    global VoiceAssistCount, voice_enabled, stop_audio
    voice_enabled = True        # allow TTS again
    stop_audio.clear()          # clear any prior stop
    VoiceAssistCount = 1        # chatty mode ON
    print("Voice On button pressed")

def InteruptVoice():
    global stop_audio
    stop_audio.set()            # interrupt current TTS playback only
    print("Stopped current TTS playback.")



#def SetPlaylistData(database_name):   #this is for saving multiple Databases
def SetPlaylistData(database_name):
    #print('Database name ' + database_name)
    #database_name = 'GoodStories'
    #change the database 
    global SongPlaylist 
    # connect to your MongoDB server
    client = MongoClient('mongodb://localhost:27017')
    # select your database
    db = client['my_database']
    # select your collection
    #SongPlaylist = db['Names']
    SongPlaylist = db[database_name]  #this is for saving multiple Databases

    print('SongPlaylist is = '+ str(SongPlaylist))
    return SongPlaylist


# get the current number of songs
def GetPlaylistSize(database_name):
    current_song_count = SetPlaylistData(database_name).count_documents({})
    return current_song_count

def AddSongsToDatabase():
    #change the database 
    MongoDBTitle = 'Songs'
    SetPlaylistData(MongoDBTitle)

    #extract_song_names(reply_content)
    song_list = extract_song_names(reply_content)
    #print(song_list)
    #print(song_list[1])

    # let's say this is your list of names
    #song_list = ['Jhon', 'sexy', 'oma', 'lee']

    # get the current number of songs
    #speak("current song count is"+ str(current_song_count))

    # add each new song to the database with the correct index
    for i, name in enumerate(song_list, start= GetPlaylistSize(MongoDBTitle)):
        SongPlaylist.insert_one({'index': i, 'name': name})
 
    #return (collection)

def SaveConversation():
    MongoDBTitle = 'GoodStories'
    SetPlaylistData(MongoDBTitle)
    #conversation = [ predict()]
    #print('this was the conversation'+ conversation)
    #USE THIS
    #conversationHist = predict()
    conversationHist = ['testing text']
    print('Printing Conversation History')
    #print(song_list)
    #print(song_list[1])

    for i, name in enumerate(response, start= GetPlaylistSize(MongoDBTitle)):
        SongPlaylist.insert_one({'index': i, 'name': name})

#SaveConversation()
def ClearSongList():
    MongoDBTitle = 'Songs'
    # delete all documents in the collection
    SetPlaylistData(MongoDBTitle).delete_many({})  

#AddSongsToDatabase()
#ClearSongList()

# retrieve a name by its index
def get_name(index):
    MongoDBTitle = 'Songs'
    document = SetPlaylistData(MongoDBTitle).find_one({'index': index})
    #speak("get name equals"+ document['name'])
    return document['name']

# test the function
#speak("your Songs are"+ get_name(0) + get_name(1))
#print(get_name(0))  # prints 'Alice'

# Function to check if a song has finished
def has_song_finished(device_id):
    #print(f"Current playback: {playback_state()}")
    remaining_time = playback_state()['item']['duration_ms'] - playback_state()['progress_ms']
    print(f"Remaining time: {remaining_time}")
    if remaining_time <= 2000:
        return True
    else:
        return False


# Function to play a list of songs in sequence
def play_songs_in_sequence(num_songs, device_id):
    #get_name = ['Heartbeat','Sweet Disposition', 'Beautiful']
    global trackNum
    while trackNum < num_songs:
        print(f"Starting song {trackNum}")
        track_name = get_name(trackNum)
        print(f"Playing track: {track_name}")
        play_track(track_name, device_id)
        while not has_song_finished(device_id):
            print("Waiting for song to finish")
            time.sleep(1)
        print(f"Song {trackNum} finished")
        trackNum += 1
    print("Finished playing all songs")

#for spotify Comoputer id
def get_device_by_name(devices, device_name):
    for device in devices['devices']:
        if device['name'] == device_name:
            return device['id']
    return None

#play A created Youtube Playlist 
def launch_youtube_playlist():
    playlist = get_playlist_from_mongo()
    if not playlist:
        return "No songs in playlist!"
    video_ids = [get_video_id(song_name) for song_name in playlist]
    playlist_url = get_youtube_playlist_url(video_ids)
    if playlist_url:
        webbrowser.open(playlist_url)
        return "Opened playlist in YouTube."
    else:
        return "No valid songs in playlist!"

def control_loop():
    global VoiceAssistCount
    global trackNum
    global device_id
    global internet_checkbox
    global tanya_checkbox 

    while True:
        if mic_on:
            textIn = get_audio()
        else:
            time.sleep(0.5)
            continue

        '''
        ---Order 3 Songs---
        1- "Tanya" 
        2- "Give me 3 Romantic Song (and dont tell em why)"
        3- "Play Music"
        4- "Voice Off" - to Prevent From Erasing The Last Songs **** I need to tell Tanya to save them to a file instead
        5- "Play Music" to Play the Next song and so on *** This is Best to be Set to Automatic Play

        ---Ask for 3 more Songs---
        1- "Tanya" 
        2- "Give me 3 more Romantic Song like that last ones but in spanish (and dont tell em why)"
        3- "Play Music"
        '''
        #<<<<--------------VOICE ASSISTENT COMMANDS------------------>>>
        if "voice off" in textIn.lower():
            VoiceOff()                       # stop first
            print("I will be silent now")    # (no TTS here)
                
        elif "voice on" in textIn.lower():
            VoiceOn()                        # enable first
            speak("voice is on")             # now it's safe to TTS

        elif "internet on" in textIn.lower():
            speak("Internet Search Is On")
            internet_checkbox = True

        elif "tanya sleep" in textIn.lower():
            tanya_checkbox = False
            VoiceAssistCount = 0
            speak("I will go sleep")

        elif "stop chat" in textIn.lower():  # Add a condition to stop the loop
            speak("I will stop this chat now")
            break

        #<<<<--------------MUSIC ASSISTENT COMMANDS------------------>>>
        elif "save songs" in textIn.lower():
            speak("Saving songs to playlist")
            AddSongsToDatabase()

        elif "add to playlist" in textIn.lower():
            speak("adding songs to playlist")
            AddSongsToDatabase()

        elif "keep conversation" in textIn.lower():
            speak("Saving Our Conversation")
            SaveConversation()

        #Plays Songs From Last Reponse Every Time I say play music looses its memory if voice is not turned off
        elif "play music reply" in textIn.lower():
            speak("opening spotify")
            subprocess.run(['schtasks', '/Run', '/TN', 'RunSpotifyBatchScript'], check=True)
            time.sleep(5)
            devices = get_devices()
            print("List of ", devices)
            device_id = devices['devices'][1]['id']
            #***Seems to crash if it doesnt find the song 
            #get the songs from the list 

            extract_song_names(reply_content)
            song_list = extract_song_names(reply_content)
            print(song_list)
            #print(song_list[1])

            speak("your song playlist is" + song_list[trackNum])
            track_name = song_list[trackNum]
            #track_name = "Sweet Disposition"  
            play_track(track_name, device_id)

        elif "spotify music" in textIn.lower():
            speak("opening spotify")
            subprocess.run(['schtasks', '/Run', '/TN', 'RunSpotifyBatchScript'], check=True)
            time.sleep(5)
            devices = get_devices()
            
            device_id = get_device_by_name(devices, "GHOSTLYROBE")
            if device_id is not None:
                print("Found device ID:", device_id)
            else:
                print("Device not found")
            
            #device_id = devices['devices'][0]['id']
            #Appends New Songs To Playlist Database
            speak("Playing"+ get_name(0))
            #track_name = get_name(0)
            #track_name = "Sweet Disposition"
            #play_track(track_name, device_id)
            #This Thread Autoplays the Full Playlist
            threading.Thread(target=CheckSpotifyStatus).start()
        
        elif "play youtube playlist" in textIn.lower():
            speak("Opening YouTube playlist in your browser.")
            launch_youtube_playlist()

        elif "skip song" in textIn.lower():
            #!schtasks /Run /TN "RunSpotifyBatchScript"
            #time.sleep(5)
            #devices = get_devices()
            #device_id = devices['devices'][1]['id']
            trackNum += 1
            speak("Playing"+ get_name(trackNum))
            track_name = get_name(trackNum)
            play_track(track_name, device_id)

        elif "previous song" in textIn.lower():
            #!schtasks /Run /TN "RunSpotifyBatchScript"
            #time.sleep(5)
            #devices = get_devices()
            #device_id = devices['devices'][1]['id']
            trackNum -= 1
            speak("Playing"+ get_name(trackNum))
            track_name = get_name(trackNum)
            play_track(track_name, device_id)

        #must Stop Music Before asking for More Songs
        elif "stop music" in textIn.lower():
            speak("stopping music")
            stop_track()

        elif "play song" in textIn.lower():
            speak("playing music")
            track_name = get_name(trackNum)
            #play_track(track_name, device_id)
            threading.Thread(target=CheckSpotifyStatus).start()
        
        elif "clear playlist" in textIn.lower():
            speak("Clearing Playlist")
            #Clears Playlist Database
            ClearSongList()

         #<<<<--------------OPEN PROGRAMS COMMANDS------------------>>>

        elif "open sd" in textIn.lower():
            speak("Opening Stable Diffusion")
            subprocess.run(['schtasks', '/Run', '/TN', 'RunStableSD'], check=True)
            time.sleep(5)

        elif "open gant" in textIn.lower():
            speak("Opening GantProject")
            subprocess.run(['schtasks', '/Run', '/TN', 'RunGantProject'], check=True)
            time.sleep(5)    

        elif "open ui" in textIn.lower():
            speak("Opening Browser UI")
            OpenWebLocalServerUI()   

        elif "what's the temperature" in textIn.lower():
            temperature = GetTemperature()
            speak("the temperature is" + temperature)

        #<<<<----------------GPT ASSISTENT COMMANDS--------------------->>>
        # VoiceAssistCount set to 0 Prevents the Mic from Prompting ChatGPT unless Tanya is called
        # While VoiceAssistCount is > 0 Tanya Will be Sending Prompts to ChatGPT
        elif "tanya" in textIn.lower() or VoiceAssistCount > 0 :
            tanya_checkbox = True           # reflect assistant ON state
            VoiceOn()                       # enable voice/chatty mode
            speak("Got it")
            #predictTest()
            predict(textIn)
            #speak("predict")
            
        elif "hello" in textIn.lower():
            speak("hello, how are you")

        elif "bye" in textIn.lower():
            tanya_checkbox = False
            VoiceAssistCount = 0
            speak("Good Bye Now")
                    
        elif "what's your name" in textIn.lower():
            speak("My Name is Tanya")
            
    
def CheckSpotifyStatus():
    MongoDBTitle = 'Songs'
    print("Playlist Size is " )
    speak("Playlist Size is " + str(GetPlaylistSize(MongoDBTitle)))
    play_songs_in_sequence(GetPlaylistSize(MongoDBTitle), device_id)

#Open Up A Crome Tab Browser
def OpenWebLocalServerUI():
    url = "http://127.0.0.1:7860"

    path_to_chrome = r'C:\Program Files (x86)\Google\Chrome\Application\chrome.exe'
    webbrowser.register('chrome', None, webbrowser.BackgroundBrowser(path_to_chrome))

    # Print the list of browser types
    print("Browser types: ", webbrowser._tryorder)

    b = webbrowser.get('chrome')

    # Print the type of browser used
    print("Browser used: ", type(b))

    success = b.open_new_tab(url)

    # Print whether the URL was opened successfully
    print("Opened URL: ", success)


def CleanChatHistory():
    global message_history
    message_history.clear()
    message_history = [{"role": "user", "content": f"You are a helpful assistant named Tanya. I will specify the subject matter in my messages, and you will reply acordingly as an expert in the field being dicused. If you understand, say OK."},
                   {"role": "assistant", "content": f"OK"}]

def clear_chat():
    CleanChatHistory()  # Clear the chat history
    return [], "", "Chat cleared!"  # Empty list for chatbot, empty string for input box, status message

#os.startfile("C:\\Users\\ErnestoCG\\AppData\\Roaming\\Spotify\\Spotify.exe")
#input('Press enter when external program has completed...')
'''
# Specify the path to the script you want to call
script_path = 'SpotifyControl.py'
'''
# Start the control_loop function in a separate thread
threading.Thread(target=control_loop).start()


'''
with concurrent.futures.ThreadPoolExecutor() as executor:
    audio_thread = executor.submit(get_audio)
    textIn = audio_thread.result()   
'''

def toggle_voice_function():
    global voice_checkbox, voice_enabled
    voice_checkbox = not voice_checkbox
    voice_enabled = voice_checkbox
    if voice_enabled:
        stop_audio.clear()
        print("toggle_function - Voice ON")
    else:
        stop_audio.set()
        print("toggle_function - Voice OFF")

  
def toggle_internet_function():
    global internet_checkbox  # Global variable to keep track of internet_checkbox state
    internet_checkbox = not internet_checkbox

    print(f"Received value: {internet_checkbox}")
    if internet_checkbox:
        print("toggle_function - internet Search ON")
        internet_checkbox = True
    else:
        print("toggle_function - internet Search OFF")
        internet_checkbox = False

def set_tanya(value: bool):
    global tanya_checkbox, VoiceAssistCount
    tanya_checkbox = bool(value)                 # set exactly to what UI sent
    VoiceAssistCount = 1 if tanya_checkbox else 0
    print(f"Tanya assistant {'ON' if tanya_checkbox else 'OFF'} (set by UI)")


def convert_mp4_upload(uploaded):
    if not uploaded:
        return None
    mp4_path = uploaded.name if hasattr(uploaded, "name") else uploaded
    if not mp4_path:
        return None
    mp3_path = mp4_to_mp3(mp4_path)
    return mp3_path  # Return path to mp3 for playback


downloader = YoutubeDownloadeViewer()  # instantiate once

def download_with_status(url):
    if not url.strip():
        return "Please enter a valid URL.", gr.update(visible=False), gr.update(visible=False)
    try:
        video_path, audio_path = downloader.download(url)
        if not video_path or not os.path.isfile(video_path):
            return "Download complete but video file not found.", gr.update(visible=False), gr.update(visible=False)
        if not audio_path or not os.path.isfile(audio_path):
            # If audio missing, just hide audio player
            return "Download complete!", gr.update(value=video_path, visible=True), gr.update(visible=False)
        return (
            "Download complete!",
            gr.update(value=video_path, visible=True),
            gr.update(value=audio_path, visible=True)
        )
    except Exception as e:
        return f"Error: {str(e)}", gr.update(visible=False), gr.update(visible=False)

def convert_mp4_upload(uploaded):
    if not uploaded:
        return None, gr.update(visible=False)
    mp4_path = uploaded.name if hasattr(uploaded, "name") else uploaded
    mp3_path = mp4_to_mp3(mp4_path)
    return mp3_path, gr.update(visible=True)


def fix_mp4_file(mp4_path):
    if not mp4_path:
        return None, gr.update(visible=False)
    
    tmp_dir = Path(tempfile.gettempdir())
    original_path = Path(mp4_path)
    fixed_path = tmp_dir / f"{original_path.stem}_fixed{original_path.suffix}"
    
    try:
        # Remux the file with ffmpeg
        subprocess.run([
            "ffmpeg", "-y", "-i", str(original_path),
            "-c", "copy", "-movflags", "+faststart",
            str(fixed_path)
        ], check=True)
        return str(fixed_path), gr.update(visible=True)
    except subprocess.CalledProcessError as e:
        print("Failed to fix MP4:", e)
        return None, gr.update(visible=False)

def toggle_mic():
    global mic_on
    mic_on = not mic_on
    print(f"Mic On: {mic_on}")

# For Saving To Database
def create_project(project_name):
    if not project_name.strip():
        return gr.update(), "Project name cannot be empty."
    
    client = MongoClient('mongodb://localhost:27017')
    db = client['my_database']
    collection = db['ChatProjects']
    
    # Check for duplicate project name
    if collection.find_one({"name": project_name.strip()}):
        return gr.update(), "Project name already exists. Please choose a different name."
    
    # Start with just the pre-prompt in history
    initial_history = [
        {"role": "user", "content": "You are a helpful assistant named Tanya. ..."},
        {"role": "assistant", "content": "OK"}
    ]
    collection.insert_one({"name": project_name.strip(), "history": initial_history})
    new_choices = get_project_choices()
    return gr.update(value=project_name.strip(), choices=new_choices), f"Project '{project_name.strip()}' created!"

# Function to delete a project from the database
def delete_project(project_name):
    if not project_name or project_name == "Select a project...":
        # Use gr.update to force a UI refresh
        return "Please select a valid project to delete.", gr.update(choices=get_project_choices(), value="Select a project...")

    client = MongoClient('mongodb://localhost:27017')
    db = client['my_database']
    collection = db['ChatProjects']

    # Delete the project with the given name
    result = collection.delete_one({"name": project_name})

    # Always fetch the new choices after deletion
    new_choices = get_project_choices()
    if result.deleted_count == 1:
        return (
            f"Project '{project_name}' deleted successfully.",
            gr.update(choices=new_choices, value="Select a project...")  # Reset dropdown and update choices
        )
    else:
        return (
            "Failed to delete project. It may not exist.",
            gr.update(choices=new_choices, value="Select a project...")  # Still update choices, just in case
        )
    
def list_projects():
    client = MongoClient('mongodb://localhost:27017')
    db = client['my_database']
    collection = db['ChatProjects']
    projects = list(collection.find({}, {"name": 1}))
    return [(str(doc['_id']), doc.get('name', 'Untitled')) for doc in projects]

def get_project_choices():
    return ["Select a project..."] + [name for _id, name in list_projects()]

def SaveChatHistory():
    client = MongoClient('mongodb://localhost:27017')
    db = client['my_database']
    collection = db['ChatHistory']
    collection.delete_many({})  # Clear previous chat if you only want one history
    collection.insert_one({'history': message_history})

def SaveChatHistoryToProject(selected_project_name, chat_history):
    client = MongoClient('mongodb://localhost:27017')
    db = client['my_database']
    collection = db['ChatProjects']
    result = collection.update_one(
        {"name": selected_project_name},
        {"$set": {"history": chat_history}}
    )
    if result.modified_count == 1:
        return "Chat saved to project!"
    else:
        return "Failed to save chat. (Did you select a project?)"
    
def LoadChatHistoryFromProject(selected_project_name):
    client = MongoClient('mongodb://localhost:27017')
    db = client['my_database']
    collection = db['ChatProjects']
    doc = collection.find_one({"name": selected_project_name})
    if doc and 'history' in doc:
        return doc['history']
    else:
        # Default pre-prompt if nothing found
        return [
            {"role": "user", "content": "You are a helpful assistant named Tanya. ..."},
            {"role": "assistant", "content": "OK"}
        ]
    
def on_project_select(project_name):
    global message_history, chat_history
    # If the user hasn't selected a real project, clear the chat
    if not project_name or project_name == "Select a project...":
        message_history = [
            {"role": "user", "content": "You are a helpful assistant named Tanya. ..."},
            {"role": "assistant", "content": "OK"}
        ]
        chat_history = []
        return [], ""  # Clear the chat in the UI

    # Otherwise, load the selected project's chat history
    history = LoadChatHistoryFromProject(project_name)
    message_history = history.copy()
    chat_pairs = []
    for i in range(2, len(history), 2):
        user_msg = history[i]["content"]
        assistant_msg = history[i+1]["content"] if i+1 < len(history) else ""
        chat_pairs.append((user_msg, assistant_msg))
    chat_history = chat_pairs
    return chat_pairs, ""

def SaveChatHistoryWithMsg():
    SaveChatHistory()
    return "Chat saved!"  
  
def LoadChatHistory():
    client = MongoClient('mongodb://localhost:27017')
    db = client['my_database']
    collection = db['ChatHistory']
    doc = collection.find_one()
    if doc and 'history' in doc:
        return doc['history']
    else:
        return [
            {"role": "user", "content": "You are a helpful assistant named Tanya. ..."},
            {"role": "assistant", "content": "OK"}
        ]  # Default pre-prompt if nothing found


def load_chat_button_fn():
    global message_history
    global chat_history
    message_history = LoadChatHistory()
    # Build chat history for the chatbot UI
    chat_pairs = []
    for i in range(2, len(message_history), 2):
        user_msg = message_history[i]["content"]
        assistant_msg = message_history[i+1]["content"] if i+1 < len(message_history) else ""
        chat_pairs.append((user_msg, assistant_msg))
    chat_history = chat_pairs  # <-- make sure chat_history is up to date!
    return chat_pairs, ""

def convert_chatbot_to_message_history(chatbot_pairs):
    history = [
        {"role": "user", "content": "You are a helpful assistant named Tanya. ..."},
        {"role": "assistant", "content": "OK"}
    ]
    for user, assistant in chatbot_pairs:
        history.append({"role": "user", "content": user})
        history.append({"role": "assistant", "content": assistant})
    return history

def set_model(model_choice):
    global selected_model
    selected_model = model_choice
    print(f"Model changed to: {selected_model}")
    return gr.update(value=selected_model)   

def convert_mp4_to_images(mp4_file, output_dir, ext):
    if not mp4_file or not output_dir:
        return gr.update(visible=False)
    try:
        images = mp4_to_sequence(mp4_file, output_dir, ext)
        if not images:
            return gr.update(visible=False)
        preview = images[:5]
        return gr.update(visible=True, value=preview)
    except Exception as e:
        return gr.update(visible=False)
    
# Toggle handler for the Tools dropdown (not a UI widget)
def on_tool_change_multi(selected: str):
    return (
        gr.update(visible=(selected == "TanyaVision — Build Atlas")),
        gr.update(visible=(selected == "TanyaVision — Dota 2 OCR")),
    )

def _sync_toggles():
    # push current backend state into the UI checkboxes
    return gr.update(value=voice_enabled), gr.update(value=tanya_checkbox)


#<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>><
#------------------------------GRADIO-UI--------------------------------#
#<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>

css = '''
#This is for the main ui wrapper 
#custom-box {
    width: 50px;
    height: 30px;
    background-size: cover;
}

#Remove Gradio’s Default Padding From the Wrapper
body, .gradio-container, #root {
    padding-top: 0px !important;
    margin-top: 0px !important;
}

#Remove Gradio’s Default Padding From the Wrapper
.svelte-1ipelgc, .block.svelte-1ipelgc {
    margin-top: 0px !important;
    padding-top: 0px !important;
}

#chatbot {
    margin-top: 0px;
}



/* Tab bar/header background */
.tab-nav, .svelte-17ws2ji, .svelte-1ipelgc, div.svelte-tabs {
    background: #2e3753 !important;
    padding-top: 10px;
    padding-bottom: 6px;
    border-radius: 12px 12px 0 0;
    margin-bottom: 0;
}
/* Tab buttons style */
div.svelte-tabitem,
.svelte-17ws2ji > button,
.svelte-1ipelgc > button,
.svelte-1ipelgc button, 
.svelte-17ws2ji button {
    background: #222e39;
    color: #fff;
    border-radius: 8px 8px 0 0;
    border: 1.5px solid #36415a;
    margin-right: 4px;
    margin-top: 8px;
    font-weight: bold;
    padding: 10px 24px 10px 24px;
    box-shadow: 0 3px 6px rgba(20,30,50,0.11);
    transition: background 0.12s, box-shadow 0.12s;
}
/* Active tab "button" look */
div.svelte-tabitem[aria-selected="true"],
.svelte-17ws2ji > button[aria-selected="true"],
.svelte-1ipelgc > button[aria-selected="true"],
.svelte-1ipelgc button[aria-selected="true"],
.svelte-17ws2ji button[aria-selected="true"] {
    background: #6071b3;
    color: #fff;
    box-shadow: 0 2px 10px rgba(50,60,90,0.20);
    border-bottom: 2px solid #6071b3;
}
/* Remove underline from active tab */
div.svelte-tabitem[aria-selected="true"] {
    box-shadow: 0 2px 10px rgba(60,90,180,0.09);
    border-bottom: none;
}

/* Your other CSS here... */

/* Override bright blue focus glow */
input:focus, textarea:focus, select:focus {
    outline: 2px solid #6071b3 !important;
    box-shadow: none !important;
    background-color: #1f1f2f !important;
    color: #eee !important;
}

#model-dropdown {
    width: 150px;
    margin-left: 0px;
}

#custom-row {
    background-color: #f0f0f0;  /* Light gray color */
    padding: 10px;  /* Optional: Add some padding */
    border-radius: 5px;  /* Optional: Add rounded corners */
}

#save-chat-button {
    background-color: lightblue !important;
    color: black !important;
    border: none;
    padding: 10px 20px;
    border-radius: 8px;
    cursor: pointer;
}

/* Remove specific styles for #delete-project-button if using classes */
#delete-project-button {
    /* Remove background-color and color here to use classes instead */
    border: none;
    padding: 10px 20px;
    border-radius: 8px;
    cursor: pointer;
}

/* Update styles in your existing CSS section */
.dark-gray-button {
    background-color: #4a4a4a !important;  /* Darker gray */
    color: white !important;
}

.red-button {
    background-color: #7a0e0e !important;  /* Darker red */
    color: white !important;
}

/* Add any additional hover effects if desired */
#save-chat-button:hover, #delete-project-button:hover {
    opacity: 0.9;
}

/* Youtube Player TAB*/
#yt-controls-row button, #yt-controls-row .gr-button {
    min-width: 90px !important;
    max-width: 180px !important;
    padding: 6px 14px !important;
    font-size: 15px !important;
    margin-right: 10px !important;
}

'''

#Opens Up an Image Server
# HTTPServerCreate.StartImgServer()

# creates a new Blocks app and assigns it to the variable interface.
with gr.Blocks(theme='SebastianBravo/simci_css', css=css) as interface:
    gr.HTML("""<div id="custom-box"></div>""")
    
    with gr.Tabs():

        # --- Tab 1: Assistant ---
        with gr.TabItem("Assistant"):
            gr.Markdown("## Tanya")
            with gr.Row():
                with gr.Column(scale=1, min_width=200):
                    project_list = gr.Dropdown(
                        label="Projects",
                        choices=get_project_choices(),
                        value="Select a project...",
                        interactive=True
                    )
                    project_name_box = gr.Textbox(label="New Project Name", placeholder="Enter project name…")
                    new_project_btn = gr.Button("Create Project")

                    # Moved here under Create
                    save_btn = gr.Button("Save Chat", elem_id="save-chat-button")

                     # ONE shared status for all actions
                    action_status = gr.Textbox(label="Status", interactive=False)

                    delete_btn = gr.Button("Delete Project", elem_id="delete-project-button", elem_classes="dark-gray-button")

                    # State to track deletion confirmation
                    delete_confirmation = gr.State(False)

                    # --- Tools dropdown + placeholder panel (left column) ---
                    gr.Markdown("### Tools")
                    tools_dropdown = gr.Dropdown(
                        label="Select Tool",
                        choices=TOOLS_CHOICES,
                        value="None",
                        interactive=True
                    )

                    # A) Create the builder panel (hidden by default)
                    rb_panel = render_refbuilder_panel()

                    # B)OCR panel (kept inline, just adding the two new controls + tidier layout)
                    with gr.Group(visible=False) as tv_panel:
                        gr.Markdown("**TanyaVision — Dota 2 OCR**")

                        tv_image_in = gr.Image(type="filepath", label="Hero Screenshot")

                        # NEW: manual tiles + auto toggle
                        with gr.Row():
                            tv_tiles = gr.Slider(1, 10, value=4, step=1, label="# of heroes (manual)")
                            tv_auto  = gr.Checkbox(value=False, label="Auto-detect # of heroes")

                        # Stack these so they don’t feel cramped
                        tv_threshold = gr.Slider(0.0, 1.0, value=0.85, step=0.01, label="Band threshold")
                        tv_topk      = gr.Slider(1, 10, value=3, step=1, label="Top-K (debug files)")

                        with gr.Row():
                            tv_fallback   = gr.Checkbox(value=True,  label="Use portrait fallback")
                            tv_savedebug  = gr.Checkbox(value=False, label="Save debug crops")

                        tv_run_btn  = gr.Button("Run OCR")
                        tv_out      = gr.Textbox(label="Vision Result", lines=8)
                        tv_send_btn = gr.Button("Send to Chat (Analyze)")

                        # NOTE: we pass tiles + auto flags (new). The rest stays the same.
                        tv_run_btn.click(
                            fn=run_ocr_tool,
                            inputs=[tv_image_in, tv_threshold, tv_topk, tv_fallback, tv_savedebug, tv_tiles, tv_auto],
                            outputs=tv_out
                        )


                    tools_dropdown.change(fn=on_tool_change_multi,inputs=[tools_dropdown], outputs=[rb_panel, tv_panel])
                    
                with gr.Column(scale=4):  # Main chat UI on the right
                    with gr.Row(elem_id="custom-row"):
                        with gr.Column(scale=1, min_width=150):
                            model_dropdown = gr.Dropdown(
                                choices=["gpt-5","gpt-5-mini","gpt-5-nano","gpt-4.1", "gpt-4o"],
                                value=selected_model,
                                label="OpenAI Model",
                                elem_id="model-dropdown"
                            )
                            model_dropdown.change(fn=set_model, inputs=[model_dropdown], outputs=[model_dropdown])

                    chatbot = gr.Chatbot([], elem_id="chatbot", height=690)
                    txt = gr.Textbox(show_label=False, placeholder="Enter prompt for ai and press enter", lines=1)

                    def _analyze_tool_output(tool_text: str):
                        if not tool_text or not tool_text.strip():
                            return chat_history, ""  # no change
                        prefixed = f"[Tool: Dota2_OCR]\n{tool_text}"
                        return predict(prefixed)     # predict returns (chat_history, "")

                    # Wire now that chatbot/txt exist
                    tv_send_btn.click(
                        fn=_analyze_tool_output,
                        inputs=[tv_out],
                        outputs=[chatbot, txt]
                    )


                    # --- Toggles Row (leave here as you requested) ---
                    with gr.Row():
                        with gr.Column(scale=2, min_width=50):
                            btn = gr.Button(value="Stop Voice")
                            btn.click(InteruptVoice)
                        with gr.Column(scale=2, min_width=50):
                            mic_toggle = gr.Checkbox(label="Mic", value=mic_on)
                            mic_toggle.change(fn=toggle_mic, inputs=[], outputs=[])
                        with gr.Column(scale=2, min_width=50):
                            tanya_toggle = gr.Checkbox(label="Tanya", value=tanya_checkbox)
                            tanya_toggle.change(fn=set_tanya, inputs=[tanya_toggle], outputs=[])
                        with gr.Column(scale=2, min_width=50):
                            voice_toggle = gr.Checkbox(label="Voice", value=voice_checkbox)
                            voice_toggle.change(fn=toggle_voice_function, inputs=[], outputs=[])
                        with gr.Column(scale=2, min_width=50):
                            internet_toggle = gr.Checkbox(label="Internet", value=internet_checkbox)
                            internet_toggle.change(fn=toggle_internet_function, inputs=[], outputs=[])
                        with gr.Column(scale=2, min_width=50):
                            clear_btn = gr.Button("Clear Chat")  # Create the button here
                            clear_btn.click(
                                fn=clear_chat,
                                inputs=[],
                                outputs=[chatbot, txt, action_status]
                            )

                    # keep UI toggles synced with backend flags
                    ui_timer = gr.Timer(0.5)  # updates every 0.5s
                    ui_timer.tick(fn=_sync_toggles, outputs=[voice_toggle, tanya_toggle])             

                    txt.submit(predict, [txt], [chatbot, txt])

                # Wire actions to the shared status
                new_project_btn.click(
                    fn=create_project,
                    inputs=[project_name_box],
                    outputs=[project_list, action_status]
                )
                save_btn.click(
                    fn=lambda project, chat: SaveChatHistoryToProject(project, convert_chatbot_to_message_history(chat)),
                    inputs=[project_list, chatbot],
                    outputs=[action_status]
                )

                def toggle_delete_button(confirmed, project_name):
                    if not confirmed:
                        # Include the project name in the confirmation message
                        return (
                            f"Are you sure you want to delete the project '{project_name}'?", 
                            True, 
                            gr.update(value="Yes! Delete Project", elem_classes="red-button"),
                            gr.update()  # No change to dropdown yet, just to match expected outputs
                        )
                    else:
                        # Perform the deletion and update dropdown
                        msg, dropdown_update = delete_project(project_name)
                        return (
                            msg, 
                            False, 
                            gr.update(value="Delete Project", elem_classes="dark-gray-button"),
                            dropdown_update
                        )

                delete_btn.click(
                    fn=toggle_delete_button,
                    inputs=[delete_confirmation, project_list],
                    outputs=[action_status, delete_confirmation, delete_btn, project_list]
                )


                # --- Project select triggers chat loading! ---
                project_list.change(
                    fn=on_project_select,
                    inputs=[project_list],
                    outputs=[chatbot, txt]
                )

        # --- Tab 2: Tana Vision --- ---
        with gr.TabItem("TanyaVision (Pro)"):
            render_tanyavision_dual_panel()


        # --- Tab 3: YouTube Player ---
        with gr.TabItem("YouTube Downloader"):
            gr.Markdown("## YouTube Downloader")
            txtYoutubeUrl = gr.Textbox(show_label=False, placeholder="Enter a Youtube URL here & Press Enter")
            download_status = gr.Textbox(label="Download Status", interactive=False)
            video_player = gr.Video(label="Downloaded Video", visible=False)
            audio_player = gr.Audio(label="Downloaded Audio", visible=False)

            txtYoutubeUrl.submit(download_with_status, inputs=[txtYoutubeUrl], outputs=[download_status, video_player, audio_player])


      # --- Tab 4: YouTube Legacy ---
        with gr.TabItem("YouTube Legacy"):
                gr.Markdown("## YouTube Legacy")
                txtYoutubeUrl = gr.Textbox(show_label=False, placeholder="Enter a Youtube URL here & Press Enter")
                download_status = gr.Textbox(label="Download Status", interactive=False)

                def download_legacy(url):
                    if not url.strip():
                        return "Please enter a valid URL."
                    try:
                        YDownload(url)  # Your existing function, no changes needed
                        return "Download complete!"
                    except Exception as e:
                        return f"Error: {str(e)}"

                txtYoutubeUrl.submit(download_legacy, inputs=[txtYoutubeUrl], outputs=[download_status])

        # --- Tab 5: YouTube Playlist Player ---
        with gr.TabItem("YouTube Playlist Player"):
            gr.Markdown("## YouTube Playlist Player (Browser Playlist)")

            playlist_state = gr.State(get_playlist_from_mongo())
            index_state = gr.State(0)

            song_label = gr.Textbox(label="Now Playing", interactive=False)
            status_box = gr.Textbox(label="Status", interactive=False)

            # Compact controls row
            with gr.Row(elem_id="yt-controls-row"):
                prev_btn = gr.Button("Previous")
                next_btn = gr.Button("Next")
                play_btn = gr.Button("Play in Browser")

            with gr.Row():
                play_playlist_btn = gr.Button("Play Playlist in YouTube (one tab)")
                reload_btn = gr.Button("Reload Playlist")

            # --- Per-song controls ---

            def show_song(idx, playlist):
                if not playlist:
                    return "No songs in playlist.", idx
                idx = max(0, min(idx, len(playlist)-1))
                return f"{idx+1} / {len(playlist)}: {playlist[idx]}", idx

            def play_in_browser(idx, playlist):
                if not playlist:
                    return "No songs in playlist.", idx
                idx = max(0, min(idx, len(playlist)-1))
                song_name = playlist[idx]
                try:
                    video_id = get_video_id(song_name)
                    url = f"https://www.youtube.com/watch?v={video_id}"
                    webbrowser.open(url)
                    return f"Opened in browser: {song_name}", idx
                except Exception as e:
                    return f"Error: {e}", idx

            def reload_playlist(_):
                playlist = get_playlist_from_mongo()
                label, idx = show_song(0, playlist)
                return label, playlist, 0

            # --- Initial load ---
            playlist_init = get_playlist_from_mongo()
            song_label.value, idx_init = show_song(0, playlist_init)

            prev_btn.click(lambda idx, playlist: show_song(idx-1, playlist), [index_state, playlist_state], [song_label, index_state])
            next_btn.click(lambda idx, playlist: show_song(idx+1, playlist), [index_state, playlist_state], [song_label, index_state])
            play_btn.click(play_in_browser, [index_state, playlist_state], [status_box, index_state])
            play_playlist_btn.click(launch_youtube_playlist, [], [status_box])
            reload_btn.click(reload_playlist, None, [song_label, playlist_state, index_state])

        # --- Tab 6: Sequence Reformat ---
        with gr.TabItem("Sequence Reformat"):
            gr.Markdown("## Sequence Reformat (EXR → PNG)")
            with gr.Row():
                seq_folder_txt = gr.Textbox(
                    label="Sequence Folder Path",
                    placeholder=r"e.g. V:\Source\Repos\ChatGPT-API-Basics\Images\Sequence\exr",
                    interactive=True
                )
                example_file_txt = gr.Textbox(
                    label="Example Filename (e.g. KineticRush_Sequence.0000.exr)",
                    placeholder="Enter one filename from that sequence folder…",
                    interactive=True
                )
            with gr.Row():
                outputDir_txt = gr.Textbox(
                    label="Output Folder (PNG goes here)",
                    placeholder=r"e.g. V:\Source\Repos\ChatGPT-API-Basics\Images\Sequence\png",
                    interactive=True
                )
            with gr.Row():
                run_btn = gr.Button("Run Reformat")
            with gr.Row():
                output_log = gr.Textbox(
                    label="Reformat Output Log",
                    interactive=False,
                    lines=15
                )

            # When "Run Reformat" is clicked, call our function and show the full log
            run_btn.click(
                fn=SequenceReformat,
                inputs=[seq_folder_txt, example_file_txt, outputDir_txt],
                outputs=[output_log]
            )

        # --- Tab 7: MP4 to Image Sequence ---
        with gr.TabItem("MP4 to Image Sequence"):
            gr.Markdown("## MP4 to Image Sequence")
            mp4_seq_input = gr.File(label="Upload MP4", file_types=[".mp4"], type="filepath")
            output_dir_txt = gr.Textbox(label="Output Folder", placeholder="e.g. C:/Users/yourname/Videos/Frames")
            ext_dropdown = gr.Dropdown(choices=[".png", ".jpg"], value=".png", label="Image Format")
            convert_seq_btn = gr.Button("Extract Frames")
            #output_log = gr.Textbox(label="Extraction Log", interactive=False, lines=8)
            preview_gallery = gr.Gallery(label="Preview (first 5 frames)", visible=False)

            def convert_mp4_to_images(mp4_file, output_dir, ext):
                if not mp4_file:
                    return "No MP4 file uploaded.", gr.update(visible=False)
                if not output_dir:
                    return "Please specify an output directory.", gr.update(visible=False)
                try:
                    images = mp4_to_sequence(mp4_file, output_dir, ext)
                    if not images:
                        return "No frames extracted.", gr.update(visible=False)
                    preview = images[:5]
                    msg = f"Extracted {len(images)} frames to {output_dir}"
                    return msg, gr.update(visible=True, value=preview)
                except Exception as e:
                    return f"Error: {str(e)}", gr.update(visible=False)

            convert_seq_btn.click(
                fn=convert_mp4_to_images,
                inputs=[mp4_seq_input, output_dir_txt, ext_dropdown],
                outputs=[preview_gallery]
            )

        # --- Tab 8: MP4 to MP3 Converter ---
        with gr.TabItem("MP4 to MP3 Converter"):
            gr.Markdown("## MP4 to MP3 Converter")
            mp4_input = gr.File(label="Upload MP4", file_types=[".mp4"], type="filepath")
            
            fix_button = gr.Button("Fix MP4 File")
            fixed_mp4_output = gr.File(label="Download Fixed MP4", visible=False, file_types=[".mp4"], type="filepath")
            fix_status = gr.Textbox(label="Fix Status", interactive=False)
            
            convert_button = gr.Button("Convert MP4 to MP3")
            mp3_output = gr.File(label="Download MP3", visible=False, file_types=[".mp3"], type="filepath")
            mp3_player = gr.Audio(label="Play MP3", visible=False, type="filepath")
            convert_status = gr.Textbox(label="Convert Status", interactive=False)

            def fix_file(file_path):
                if not file_path:
                    return None, "No file uploaded", gr.update(visible=False)
                fixed_path, _ = fix_mp4_file(file_path)  # use your fix function here
                if fixed_path:
                    return fixed_path, "File fixed successfully!", gr.update(visible=True)
                else:
                    return None, "Failed to fix file.", gr.update(visible=False)

            fix_button.click(
                fn=fix_file,
                inputs=mp4_input,
                outputs=[fixed_mp4_output, fix_status, fixed_mp4_output]
            )

            def convert_file(original_path, fixed_path):
                mp4_path = fixed_path if fixed_path else original_path
                if not mp4_path:
                    return None, None, "No file to convert", gr.update(visible=False), gr.update(visible=False)
                try:
                    mp3_path = mp4_to_mp3(mp4_path)  # your convert function
                    return mp3_path, mp3_path, "MP3 conversion successful!", gr.update(visible=True), gr.update(visible=True)
                except Exception as e:
                    return None, None, f"Conversion failed: {str(e)}", gr.update(visible=False), gr.update(visible=False)

            convert_button.click(
                fn=convert_file,
                inputs=[mp4_input, fixed_mp4_output],
                outputs=[mp3_output, mp3_player, convert_status, mp3_output, mp3_player]
            )


        # --- Tab 9: Speaker Split ---
        with gr.TabItem("Speaker Spliter"):
            gr.Markdown("## Speaker Spliter")
            diar_input = gr.File(label="Select MP3/WAV for speaker split", file_types=[".mp3", ".wav"], file_count="single", type="filepath")
            spk1_output = gr.File(label="Speaker 1 audio", file_count="single", type="filepath")
            spk2_output = gr.File(label="Speaker 2 audio", file_count="single", type="filepath")
            diar_input.change(fn=split_speakers, inputs=[diar_input], outputs=[spk1_output, spk2_output])

        # --- Tab 10: Sepformer Split ---
        with gr.TabItem("Vocal Spliter"):
            gr.Markdown("## Vocal Spliter")
            sepformer_audio = gr.Audio(label="Upload WAV for Sepformer split", type="filepath")
            sepformer_btn = gr.Button("Run Sepformer Split")
            sepformer_spk1 = gr.File(label="Speaker 1 (Sepformer)", file_count="single", type="filepath")
            sepformer_spk2 = gr.File(label="Speaker 2 (Sepformer)", file_count="single", type="filepath")
            sepformer_btn.click(fn=split_and_separate, inputs=[sepformer_audio], outputs=[sepformer_spk1, sepformer_spk2])

        # --- Tab 10: Sepformer Split ---
        with gr.TabItem("Vocal Spliter"):
            gr.Markdown("## Vocal Spliter")
            sepformer_audio = gr.Audio(label="Upload WAV for Sepformer split", type="filepath")
            sepformer_btn = gr.Button("Run Sepformer Split")
            sepformer_spk1 = gr.File(label="Speaker 1 (Sepformer)", file_count="single", type="filepath")
            sepformer_spk2 = gr.File(label="Speaker 2 (Sepformer)", file_count="single", type="filepath")
            sepformer_btn.click(fn=split_and_separate, inputs=[sepformer_audio], outputs=[sepformer_spk1, sepformer_spk2])

'''
    gr.HTML('<div id="custom-box"></div>')

    # creates a new Chatbot instance and assigns it to the variable chatbot.
    chatbot = gr.Chatbot([], elem_id="chatbot", height=690)
 
    # creates a new Row component, which is a container for other components.
    #with gr.Row(): 
    # the button will be positioned to the right of the textbox (just like in your 
    with gr.Row():
        txt = gr.Textbox(show_label=False, placeholder="Enter prompt for ai and press enter", lines=1)


    with gr.Row():

        with gr.Column(scale=2, min_width=50):  # Small column

         #here are some buttons If i need it
            btn = gr.Button(value="Stop Voice")
            #btn2 = gr.Button(value="ON Voic")
            #btn.style(full_width=False, size='sm')  # Adjust the size of the button
            #btn2.style(full_width=False, size='sm')  # Adjust the size of the button
            btn.click(InteruptVoice)
            #voice_checkbox = False
            #btn2.click(VoiceOn)
            
        with gr.Column(scale=2, min_width=50):  # Small column
            tanya_toggle = gr.Checkbox(label="Tanya", value=tanya_checkbox)
            tanya_toggle.change(fn=toggle_tanya_function, inputs=[], outputs=[])
           

        with gr.Column(scale=2, min_width=50):  # Small column
            voice_toggle = gr.Checkbox(label="Voice", value=voice_checkbox)
            voice_toggle.change(fn=toggle_voice_function, inputs=[], outputs=[])
        
        with gr.Column(scale=2, min_width=50):  # Small column
            internet_toggle = gr.Checkbox(label="Internet", value=internet_checkbox)
            internet_toggle.change(fn=toggle_internet_function, inputs=[], outputs=[])
  

    with gr.Row():
        
        with gr.Column(scale=13, min_width=270):  # Large column
            #creates a new Textbox component, which is used to collect user input. 
            #The show_label parameter is set to False to hide the label, 
            #and the placeholder parameter is set
            
            txtYoutubeUrl = gr.Textbox(show_label=False, placeholder="Enter a Youtube URL here & Press Enter")

    # New row for the output directory
    with gr.Row():
        with gr.Column(scale=13, min_width=270):  # Large column
            outputDirPath = gr.Textbox(show_label=True, placeholder="Enter Output Directory", label="Image Sequence Output Directory")

    with gr.Row():
        with gr.Column(scale=13, min_width=270):  # Large column
            fileImgSequencePath = gr.File(label="Select an Image Sequence File To Format - > .PNG")
            output_text = gr.Textbox(label="Reformat Output Log")  # Added output Textbox
            
    # --- MP4 to MP3 Row ---
    with gr.Row():
        mp4_input = gr.File(
            label="Select an MP4 to convert",
            file_types=[".mp4"],        # ← include the leading dot
            file_count="single",
            type="filepath"
        )
        mp3_output = gr.File(
            label="Download the MP3",
            file_count="single",
            type="filepath"
        )
        mp4_input.change(
            fn=convert_mp4_upload,
            inputs=[mp4_input],
            outputs=[mp3_output]
        )

    # --- Speaker Split Row ---
    with gr.Row():
        diar_input = gr.File(
            label="Select MP3/WAV for speaker split",
            file_types=[".mp3", ".wav"],    # include the leading dots
            file_count="single",
            type="filepath"
        )
        spk1_output = gr.File(
            label="Speaker 1 audio",
            file_count="single",
            type="filepath"
        )
        spk2_output = gr.File(
            label="Speaker 2 audio",
            file_count="single",
            type="filepath"
        )
        diar_input.change(
            fn=split_speakers,
            inputs=[diar_input],
            outputs=[spk1_output, spk2_output]
        )


    # --- Sepformer-based Speaker Split Row ---
    with gr.Row():
        sepformer_audio = gr.Audio(
            label="Upload WAV for Sepformer split",
            type="filepath"
        )
        sepformer_btn = gr.Button("Run Sepformer Split")
        sepformer_spk1 = gr.File(
            label="Speaker 1 (Sepformer)",
            file_count="single",
            type="filepath"
        )
        sepformer_spk2 = gr.File(
            label="Speaker 2 (Sepformer)",
            file_count="single",
            type="filepath"
        )
        sepformer_btn.click(
            fn=split_and_separate,
            inputs=[sepformer_audio],
            outputs=[sepformer_spk1, sepformer_spk2]
        )

    # --- Your existing chat + utilities row ---
    with gr.Row():
        with gr.Column():
            clear = gr.ClearButton([txt, chatbot])
            clear.click(fn=CleanChatHistory, inputs=[], outputs=[])

        txt.submit(predict, [txt], [chatbot, txt])

        txtYoutubeUrl.submit(
            fn=YDownload, 
            inputs=[txtYoutubeUrl], 
            outputs=[]
        )
        fileImgSequencePath.change(
            SequenceReformat,
            inputs=[fileImgSequencePath, outputDirPath],
            outputs=output_text
        )
        mp4_input.change(
            convert_mp4_upload,
            inputs=mp4_input,
            outputs=mp3_output
        )

'''

'''  
    # --- NEW Speaker Split by Button ---     
    with gr.Row():
        audio_in     = gr.Audio(label="Upload WAV", type="filepath")
        split_btn    = gr.Button("Split by Speaker")
        output_files = gr.File(label="Speaker WAVs", file_count="multiple")
        split_btn.click(fn=run_split, inputs=audio_in, outputs=output_files)
'''


'''
interface.launch(
    server_name="0.0.0.0",
    server_port=7860,
    #show_error=True,
    #inbrowser=True
)
'''
#interface.launch(share=True)

interface.launch()


# From here, we can open the app:
# 
# ```
# $ python3 gradio-joke.py 
# Running on local URL:  http://127.0.0.1:7860
# 
# To create a public link, set `share=True` in `launch()`.
# ```
