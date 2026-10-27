# Put BhashaRadar online (free) + connect Gmail

You'll end up with a private web address like `https://bhasharadar.onrender.com` that works from your phone or any computer, behind your own password. Total time: about 30 minutes, once.

| Piece | Service | Cost | What it does |
|---|---|---|---|
| App | **Render** (free web service) | ₹0, no card | Runs BhashaRadar |
| Database | **Neon** (free Postgres) | ₹0, no card | Keeps your jobs, tracker, resume and settings |
| Email | **Your Gmail**, via Google sign-in | ₹0 | Sends outreach emails from your address |

**Good to know about the free plan**
- The app **sleeps after 15 minutes** without visits. The first visit after that takes about a minute to wake up. Searches you start keep it awake until they finish.
- While it's asleep nothing runs in the background, so the optional *daily search* and *automatic email sending* only happen while you have it open. Drafts and "Review before sending" work normally.
- The free plan **blocks SMTP**, so a Gmail app password can't be used online. Use **Connect Gmail** instead (step 4). The app password still works on your PC copy.
- The "Open form with my details" helper only works on your PC (a cloud server has no screen).

---

## 1. GitHub repository
1. Go to **github.com/new**.
2. Repository name: `bhasharadar`. Choose **Private**. Leave *Add a README* unticked.
3. Click **Create repository**, then tell Claude it's created. Claude pushes the code for you.

## 2. Neon database
1. Go to **neon.com**, click **Sign up**, and continue with Google or GitHub.
2. Create a project. Name: `bhasharadar`. Region: **AWS Asia Pacific (Singapore)**. Postgres version: the default.
3. On the project dashboard click **Connect**. Copy the connection string; it looks like
   `postgresql://neondb_owner:…@ep-…-pooler.ap-southeast-1.aws.neon.tech/neondb?sslmode=require`
   Keep it private: it's the key to your data.

## 3. Render app
1. Go to **render.com** and **Sign up with GitHub**. Let Render access the `bhasharadar` repository.
2. Click **New → Blueprint** and choose the `bhasharadar` repo. Render reads `render.yaml` from it.
3. It asks for these values:
   - **DATABASE_URL**: paste the Neon string from step 2.
   - **APP_PASSWORD**: pick the password you'll type on the login page (long is good, e.g. 4 random words).
   - **BR_CONTACT_EMAIL**: your email (optional; job APIs see it as a polite contact).
   - *BR_SECRET_KEY is generated for you. Never change it, or saved keys can't be decrypted.*
4. Click **Apply**. The first build takes about 5 minutes.
5. Open the address Render shows (e.g. `https://bhasharadar.onrender.com`), log in, upload your resume in **Settings**, and press **GO**.

Updates: every time new code is pushed to the GitHub repo, Render redeploys automatically. Your data stays in Neon.

## 4. Connect Gmail (Sign in with Google)
You create your own small Google "app" that only you use. Google calls it *unverified*; that's expected for a personal tool.

1. Go to **console.cloud.google.com**, then the project picker → **New project** → name `BhashaRadar` → **Create**. Select the new project.
2. **Enable the Gmail API:** search "Gmail API" in the top bar, open it, and click **Enable**.
3. Open **Google Auth Platform** (search "OAuth consent screen" if you don't see it) → **Get started**:
   - App name `BhashaRadar`, user support email: your Gmail.
   - Audience: **External**.
   - Contact email: your Gmail. Agree and **Create**.
4. **Audience** page → **Publish app** → confirm. The status becomes **In production**.
   *Important:* in *Testing* mode Google disconnects you every 7 days. *In production* it stays connected. You don't need to submit for verification.
5. **Data access** page → **Add or remove scopes** → tick `.../auth/gmail.send` (paste it into the filter if needed), plus `openid` and `.../auth/userinfo.email` → **Update** → **Save**.
6. **Clients** page → **Create client**:
   - Application type: **Web application**, name `BhashaRadar`.
   - **Authorized redirect URIs** → **Add URI** →
     `https://YOUR-APP.onrender.com/api/email/oauth/google/callback`
     (use your real Render address; it must match exactly).
   - **Create**, then copy the **Client ID** and **Client secret**.
7. In BhashaRadar: **Settings → API keys**. Paste `GMAIL_CLIENT_ID` and `GMAIL_CLIENT_SECRET` and click **Save** on each.
8. **Outreach → Connect Gmail**. Choose your account. Google shows *"Google hasn't verified this app"*: click **Advanced → Go to BhashaRadar (unsafe)** → **Continue**. You'll see "Gmail connected".
9. Click **Send test email to myself**. In *Rules*, **Send with** should be **Gmail**.

BhashaRadar only asks for permission to **send** email. It can't read your inbox. You can remove access any time at **myaccount.google.com/permissions**.

---

## Troubleshooting
- **"Set the APP_PASSWORD environment variable"**: add it in Render → your service → **Environment**, then **Save**. Render restarts the app.
- **Data disappears after a restart**: DATABASE_URL is missing or wrong (Render → Environment). Check the Render logs for "WARNING: no DATABASE_URL".
- **Google says `redirect_uri_mismatch`**: the URI in step 4.6 must be exactly your Render address + `/api/email/oauth/google/callback`, with `https` and no trailing slash.
- **Gmail disconnected after a week**: the Google app was still in *Testing*. Do step 4.4, then Connect Gmail again.
- **Slow first page**: the free app was asleep. Wait about a minute.
- **Build failed on Render**: open the deploy log and send Claude the last 30 lines.
