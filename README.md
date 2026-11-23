# KNRUHS Pulse — notice-fetching bot (prototype)

Checks the KNRUHS notices page, finds new notice PDFs, reads them, and drafts a post
(title, summary, key points, stream, type, deadline, reference no., urgency).
A person reviews every draft before anything is published. The bot never publishes by itself.

## Run it on your Mac (Terminal app)

1. Unzip `knruhs-bot.zip` (double-click it). You get a folder called `knruhs-bot`.
2. Open **Terminal** (Cmd+Space, type "Terminal", Enter).
3. Go into the folder (type this in Terminal, adjust the path if you unzipped elsewhere):
   `cd ~/Downloads/knruhs-bot`
4. (Optional, better PDF reading) `pip3 install pypdf`
5. Open `config.json` in TextEdit. Replace `set-your-email@example.com` in `user_agent` with your real email,
   so the website can contact you if the bot ever bothers it. Save.
6. First check: `python3 bot.py --once`
7. Open the review page: `open data/review.html`

Run `python3 bot.py --once` again later: it only drafts notices it has not seen before.
`python3 bot.py --watch` keeps checking every 5 minutes (Ctrl+C to stop).
`python3 bot.py --watch --notify` also pops a macOS notification for each new draft.
`python3 bot.py --reset` makes it forget everything seen so far.

## If it finds "0 notice links"

The real KNRUHS site may list notices on a different page than the home page.
Open the website in your browser, go to the page that lists the latest notifications,
copy its address, and put it in `config.json` under `sources` → `url`. Run again.

## Test without the real site

`python3 tests/make_mock.py abcd` builds a fake notice site in /tmp/mock, then
`cd /tmp/mock && python3 -m http.server 8765` serves it, and a config pointing to
`http://localhost:8765/` lets you watch the bot work (this is how it was tested).

## Notes

- Standard library only. `pypdf` is optional. `ANTHROPIC_API_KEY` + `"llm": {"enabled": true}` in config.json
  turns on AI-written drafts (experimental, not yet tested with a live key).
- It obeys the site's robots.txt, waits between requests, and identifies itself.
- Scanned (image-only) PDFs are flagged "read it yourself" — they need OCR, which is not built yet.
- Drafts are in `data/drafts.json`, in the same shape the demo's admin app publishes.


## Live inbox in the admin app
Set `admin_inbox` in config.json to the full path of `inbox.js` inside your pitch folder, for example
`/Users/arun/Downloads/knruhs-pitch 2/inbox.js`. Every run writes the drafts there, and admin.html (Inbox tab)
re-reads that file every 15 seconds. Run `python3 bot.py --watch` to keep checking the KNRUHS site every 5 minutes.

## Channel Desk: turn notices into Instagram / Telegram / WhatsApp posts

The bot finds a notice, the Desk makes a 3-slide carousel (1080x1350) plus captions, and you review it on one screen.
It posts to Telegram and Instagram by itself; WhatsApp is copy and paste (WhatsApp has no official posting API).

### 1. Install once (Terminal app on your Mac)
    pip3 install pillow pypdf certifi

### 2. Start it (Terminal, inside the knruhs-bot folder)
    cd ~/Downloads/knruhs-bot
    python3 bot.py --desk
Then open http://localhost:8800 in Safari or Chrome. Leave the Terminal window open; Ctrl+C stops it.
It checks KNRUHS every 5 minutes. Use `--desk --backfill` the first time to load the latest few notices.
The Desk only listens on your own Mac; nobody else can open it.

### 3. What you do on the Desk
- **New**: each notice with slides, bot notes, a button to open the original on KNRUHS, and captions.
- **Post to all connected / Telegram / Instagram**: sends it now.
- **Copy WhatsApp text** + **Download slides**: paste into your WhatsApp Channel, attach the slides, then press **Mark WhatsApp posted**.
- **Hold**: keeps it out of auto-post. **Dismiss**: hides it.
- **Auto-post switch** (top right): when on, clean notices are posted by themselves after a delay (default 5 min, so you can press Hold).
  Never auto-posted: scanned PDFs, unsure topic, low confidence (below 85%), anything found before you switched it on.
  Limit: 6 posts per hour.

### 4. Branding (config.json)
    "brand": {"name": "Your Page Name", "tagline": "Updates in plain words", "handle": "@yourhandle"}

### 5. Telegram setup (10 minutes, free)
1. In Telegram, open **@BotFather**, send `/newbot`, follow the prompts, copy the token it gives you.
2. Create a Telegram **Channel** (public is easiest, e.g. @knruhs_pulse). Channel info > Administrators > add your bot as admin (allow Post Messages).
3. In config.json add:
       "telegram": {"bot_token": "PASTE_TOKEN", "chat_id": "@knruhs_pulse"}
4. Restart the Desk. The Telegram chip turns green.

### 6. Instagram setup (the long one, about an hour, free)
Instagram only allows automatic posting through Meta's official API, and it needs all of this:
1. Instagram account switched to **Professional** (Settings > Account type; Creator or Business both work).
2. A **Facebook Page** linked to that Instagram account.
3. At developers.facebook.com create an **App** (type Business), add the Instagram API product, and generate an **access token** for your account with permissions `instagram_basic` and `instagram_content_publish`. Exchange it for a long-lived token (about 60 days; you must refresh it before it expires). Note your Instagram **user id**.
4. Instagram downloads pictures from a public web address, so slides must be uploaded somewhere public first. Simplest: make a **public GitHub repo** (e.g. pulse-slides), create a token at github.com/settings/tokens with access to that repo (Contents: read and write).
5. In config.json add:
       "instagram": {"user_id": "YOUR_IG_ID", "access_token": "LONG_LIVED_TOKEN",
                     "host": {"type": "github", "repo": "yourname/pulse-slides", "token": "GITHUB_TOKEN"}}
6. Restart the Desk. The Instagram chip turns green.
Posting to your own account in Meta's developer mode normally needs no app review, but Meta changes rules often; if the token is refused, check the app's status in the developer dashboard.

### 7. Honest limits
- Telegram and Instagram code was tested here only against fake servers, not the real services. Do the first posts by hand (button) and check them before switching Auto-post on.
- The real Instagram/WhatsApp **Broadcast Channel** cannot be posted to by any tool; use normal Instagram posts and copy-paste for WhatsApp.
- Posting to a WhatsApp Channel through unofficial tools can get your number banned, so the Desk does not do it.
- Scanned (image-only) PDFs get a basic card and a warning; read them yourself before posting.
- Posts are labelled as unofficial and link to the original KNRUHS notice. Keep it that way.

## Always-on and free: run it on GitHub (no Mac, no server)

GitHub runs `cloud.py` about every 15 minutes. There is no Desk screen in this mode; you control the bot by messaging it on Telegram.

**What happens on each run**
- Clean notices (no warnings, confidence 85%+) are posted to your channel automatically.
- Doubtful ones (scanned PDF, unsure topic) are sent to YOU privately with the original link. Reply `/post CODE` to publish or `/skip CODE` to drop.
- `/list` shows what is waiting. Only your own Telegram account can command the bot.
- The very first run posts nothing: it only memorises what is already on the KNRUHS site.
- Want to approve everything yourself? In `config.cloud.json` set `"cloud": {"mode": "approve"}`.

**Setup**
1. On github.com create a new **public** repository (public = unlimited free runs; a private repo only gets 2,000 minutes a month, not enough for every 15 minutes).
2. Put all files of this folder in it, including the hidden `.github` folder. Do NOT upload `config.json` if it has tokens in it.
3. In the repo: Settings > Secrets and variables > Actions > New repository secret. Add:
   - `TELEGRAM_BOT_TOKEN` : the token from @BotFather
   - `TELEGRAM_CHAT_ID`   : your channel's username with @ (for example `@knruhsx`)
   - `OWNER_CHAT_ID`      : your own numeric Telegram id (message @userinfobot to see it). Also open your bot and press Start once, or it cannot message you.
4. In the repo: Actions tab > "KNRUHS notice bot" > Run workflow. The bot messages you "KNRUHS bot is live".

**Good to know**
- GitHub's timer is not exact; runs can be 5 to 20 minutes late at busy times.
- GitHub pauses scheduled runs if a repo has had no activity for 60 days. The bot saves its memory to the repo whenever a notice arrives, which counts as activity.
- Brand name and handle on the slides are in `config.cloud.json` under `brand`.
- Use the Mac Desk OR GitHub for posting, not both at once, or a notice can be posted twice.
- Tokens live only in GitHub Secrets. Never type them into a file in the repo.
