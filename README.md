**Auto-sends your Twitch link into every League of Legends champ select — automatically.**

No more typing your channel link every single game. ChampPlugger runs quietly in the background and drops your message into League chat the moment champ select starts, and again after the game ends.

By [twitch.tv/Dan7heM4n](https://twitch.tv/Dan7heM4n) · Support on [Ko-fi](https://ko-fi.com/dan7hem4n)

---

## Download

**Grab the latest `.exe` from [Releases](../../releases) — no Python or install required. Just download and run.**

> Windows may show an "unknown publisher" warning on first launch since this isn't a signed application from a large company. Click **More info → Run anyway**.

## What it does

- Watches for champ select to start
- Waits a few seconds so everyone's actually loaded in
- Sends your configured message once — nothing appended, nothing spammy
- Does the same thing on the post-game screen
- Reconnects automatically if League restarts, updates, or closes mid-session

Built on Riot's own local client API — the same category of tool as Blitz and Porofessor. No memory reading, no input injection, no touching the game process.

## Usage

1. Open the League client and log in — the app watches for it, it doesn't launch it for you
2. Type your message in the box, click **Save**
3. Click **Start** and minimize — it runs quietly in the background
4. Click **Stop** anytime to pause, or just close the window

A full walkthrough is built into the app under **How to Use**.

## For developers only — running from source

*(Most people don't need this section — the .exe above already includes everything. This is only if you want to edit the Python code yourself.)*
