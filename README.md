ChampPlugger
<img width="628" height="514" alt="image" src="https://github.com/user-attachments/assets/a17c50a2-e0f2-44f2-aa88-0f15b7d689b6" />

Auto-sends a message into League of Legends champ select (and the post-game lobby) using Riot's own local client API — the same one tools like Blitz and Porofessor use. No memory reading, no input injection, no touching the game process.
By twitch.tv/Dan7heM4n
What it does
Watches for champ select to start
Waits a few seconds so everyone's actually loaded in
Sends your configured message once — nothing appended, nothing spammy
Does the same thing on the post-game screen
Reconnects automatically if League restarts, updates, or closes mid-session
Download
Grab the latest `.exe` from Releases — no Python or install required, just download and run.
> Windows may show an "unknown publisher" warning on first launch since this isn't a signed application from a large company. Click **More info → Run anyway**.
Usage
Open the League client and log in — the app watches for it, it doesn't launch it for you
Type your message in the box, click Save
Click Start and minimize — it runs quietly in the background
Click Stop anytime to pause, or just close the window
Full walkthrough is built into the app under How to Use.
```


Support
If this saves you time, a tip on Ko-fi is always appreciated.
