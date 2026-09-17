
<img width="1598" height="885" alt="image" src="https://github.com/user-attachments/assets/9930b5c3-a3ee-4321-8487-b684bb7e1251" />

ChampPlugger
Auto-sends your Twitch link into every League of Legends champ select — automatically.
No more typing your channel link every single game. ChampPlugger runs quietly in the background and drops your message into League chat the moment champ select starts, and again after the game ends.
By twitch.tv/Dan7heM4n · Support on Ko-fi
---
Download
Grab the latest `.exe` from Releases — no Python or install required, just download and run.
> Windows may show an "unknown publisher" warning on first launch since this isn't a signed application from a large company. Click **More info → Run anyway**.
What it does
Watches for champ select to start
Waits a few seconds so everyone's actually loaded in
Sends your configured message once — nothing appended, nothing spammy
Does the same thing on the post-game screen
Reconnects automatically if League restarts, updates, or closes mid-session
Built on Riot's own local client API — the same category of tool as Blitz and Porofessor. No memory reading, no input injection, no touching the game process.
Usage
Open the League client and log in — the app watches for it, it doesn't launch it for you
Type your message in the box, click Save
Click Start and minimize — it runs quietly in the background
Click Stop anytime to pause, or just close the window
A full walkthrough is built into the app under How to Use.
Running from source
```
pip install websockets requests urllib3
python LolAutoPlug.py
```
CLI mode (no GUI):
```
python LolAutoPlug.py --cli --message "hi team"
```
Support
If this saves you time, a tip on Ko-fi is always appreciated.
