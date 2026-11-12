# YouTube Channel Setup & Publishing Operations Guide

This guide provides end-to-end instructions for configuring Google Cloud Platform (GCP) credentials, connecting YouTube channels via OAuth 2.0, understanding quota constraints, and operating the Shorts Engine Publishing Studio.

---

## 1. Architectural Overview

Shorts Engine includes an enterprise-grade YouTube publishing subsystem designed around three core principles:

1. **Identity-First OAuth with Strict Mismatch Protection**: Channel tokens are verified against the authenticated YouTube Channel ID before saving. If a channel mismatch occurs (e.g. logging into the wrong Brand account), the operator is prompted with a resolution dialog (`rebind`, `create_new`, or `cancel`).
2. **Pre-Upload Identity Guard**: Before spending 1,600 quota units on a heavy video upload, Shorts Engine executes a 1-unit `channels.list(mine=true)` pre-flight check. If authorization has expired, the job is paused with status `PAUSED_NEEDS_ATTENTION` without burning quota.
3. **Daily Quota Ledger with Pacific Midnight Reset**: Tracks all YouTube Data API v3 consumption. YouTube enforces a 10,000 units/day default quota resetting at midnight Pacific Time (08:00 UTC). Shorts Engine caps uploads at 6 per day (1,600 units/upload = 9,600 units), leaving 400 units for queries and telemetry.

---

## 2. Google Cloud Platform (GCP) Setup

### Step 2.1: Create or Select a GCP Project
1. Navigate to the [Google Cloud Console](https://console.cloud.google.com/).
2. Select your organization and create a new project (e.g. `shorts-engine-publisher`) or select an existing project.
3. Note your **Project ID**.

### Step 2.2: Enable YouTube Data API v3
1. In the GCP Console, go to **APIs & Services > Library**.
2. Search for `YouTube Data API v3`.
3. Click **Enable**.

### Step 2.3: Configure OAuth Consent Screen
1. Go to **APIs & Services > OAuth consent screen**.
2. Choose **External** user type (unless you have a Google Workspace enterprise organization where all target channels are under the same domain).
3. Fill in the required fields:
   - **App name**: `Shorts Engine Publisher`
   - **User support email**: Your admin email.
   - **Developer contact information**: Your admin email.
4. **Scopes**: Click **Add or Remove Scopes** and select:
   - `https://www.googleapis.com/auth/youtube.upload` (Upload YouTube videos)
   - `https://www.googleapis.com/auth/youtube.readonly` (View YouTube account details and channel IDs)
   - `https://www.googleapis.com/auth/youtube` (Manage YouTube account / full access)
5. Save and continue.

### Step 2.4: Create OAuth 2.0 Client ID Credentials
1. Go to **APIs & Services > Credentials**.
2. Click **Create Credentials > OAuth client ID**.
3. Select **Application type**: `Web application`.
4. Name: `Shorts Engine Web Client`.
5. **Authorized redirect URIs**:
   - Local development: `http://localhost:8000/oauth2callback` and `http://127.0.0.1:8000/oauth2callback`
   - Production / server deployment: `https://your-domain.com/oauth2callback`
6. Click **Create**.
7. Download the credentials JSON or copy the **Client ID** and **Client Secret**.
8. Save credentials to your environment or configuration file:
   - Path: `config/client_secrets.json` or `.env`:
     ```env
     YOUTUBE_CLIENT_ID=your_client_id.apps.googleusercontent.com
     YOUTUBE_CLIENT_SECRET=your_client_secret
     YOUTUBE_REDIRECT_URI=http://localhost:8000/oauth2callback
     ```

---

## 3. OAuth Modes: Testing vs. Production

### Testing Mode Caveats (Important!)
By default, newly created OAuth consent screens are set to **Testing** publishing status.
- **7-Day Token Expiration**: Google automatically expires refresh tokens after **7 days** when an app is in Testing mode. After 7 days, publishing will fail with `invalid_grant` or `token expired`, requiring re-authentication.
- **Test User Whitelist**: Only Google Accounts explicitly listed under **Test users** in the OAuth Consent Screen can authenticate. You must add every email address that manages target channels.

### Transitioning to Production Mode
1. In **APIs & Services > OAuth consent screen**, click **Publish App**.
2. When prompted about app verification:
   - For internal/private channel operations with unverified apps, Google permits up to 100 authorizations with an "unverified app" warning screen (which the operator can bypass by clicking *Advanced > Proceed to Shorts Engine*).
   - If you submit for Google App Verification, Google will require a privacy policy URL, demo video, and justification for sensitive scopes.

### Video Visibility Policy for Unverified Apps
- If your GCP OAuth project is unverified and uses sensitive upload scopes, YouTube may automatically set uploaded videos to **Private** or prevent them from being published as **Public**.
- **Recommended Practice**: Always publish clips as `unlisted` or `private` first. Once uploaded, review the video inside YouTube Studio or automate scheduling via Shorts Engine.

---

## 4. Brand Accounts vs. Personal Channels

Many YouTube channels (such as *Debateks*) are managed as **Brand Accounts** linked to a primary Google Account.

### Authorizing the Correct Brand Channel
When you click **Connect YouTube Account** in Shorts Engine:
1. Google will prompt you to select your Google Account email.
2. If that Google Account manages one or more Brand Accounts, Google will present a **"Choose an account or Brand Account"** picker.
3. **CRITICAL**: Do **NOT** select your personal profile name unless that is where the channel lives. Select the specific **Brand Channel** (e.g. *Debateks*).
4. Google will then display the permission consent screen specifically for that channel.
5. Click **Allow**.

---

## 5. Strict Channel Identity Matching & Resolution

Shorts Engine safeguards against accidental cross-channel publishing. When you connect OAuth for a channel:

1. The OAuth callback queries `https://www.googleapis.com/youtube/v3/channels?part=snippet&mine=true`.
2. Shorts Engine compares the authenticated `youtube_channel_id` against the database record's expected `youtube_channel_id`.
3. If they match: OAuth tokens are safely encrypted and saved. The channel badge turns 🟢 **Authorized**.
4. If they **DO NOT match**:
   - The connection is paused, and a **Pending Mismatch** is recorded in `oauth_mismatches`.
   - The operator is presented with the **YouTube Channel Mismatch Resolution Dialog**:
     - **Option 1: Rebind Channel (`rebind`)**: Updates the existing database channel record to match the newly authenticated YouTube channel (updates channel ID, name, handle, and tokens).
     - **Option 2: Create New Channel (`create_new`)**: Keeps the existing channel record untouched and creates a new channel in Shorts Engine for the authenticated YouTube account.
     - **Option 3: Cancel (`cancel`)**: Cancels the connection attempt, discarding the mismatched tokens.

---

## 6. Daily Quota Ledger & Pre-Upload Identity Guard

### Daily Quota Limits
- **Total Limit**: 10,000 units / day (standard YouTube Data API v3 quota).
- **Reset Timing**: Midnight Pacific Time (08:00 UTC / 00:00 PST / 01:00 PDT).
- **Cost Structure**:
  - `videos.insert` (Video Upload): **1,600 units**
  - `channels.list(mine=true)` (Verification): **1 unit**
  - `videos.list` / `thumbnails.set`: **1 to 50 units**
- **Maximum Daily Uploads**:
  $$\lfloor 10,000 / 1,600 \rfloor = 6 \text{ video uploads per day}$$
  Shorts Engine enforces this 6 uploads/day ceiling to prevent quota exhaustion.

### Pre-Upload Identity Guard
Whenever a background job or instant publish executes:
1. The publisher runs `channels.list(mine=true)` costing only **1 unit**.
2. It verifies that the stored credentials match the channel ID.
3. If the token is revoked, expired, or invalid, the job status changes to `PAUSED_NEEDS_ATTENTION`, an operator alert is logged, and the 1,600-unit upload is aborted.

### Operator Quota Calibration
If you upload videos or run queries outside of Shorts Engine (e.g. via YouTube Studio or external scripts), the local quota ledger can be calibrated:
- **API Endpoint**: `POST /api/quota/calibrate`
- **Payload**: `{"used_units": 3200}`
- This recalibrates the database ledger and updates the quota gauges across both the Jinja2 dashboard and the React 19 SPA.

---

## 7. Operating the Publishing Studio

### Accessing the Studio
- In the **Review Queue** (`/queue` or `/app/queue`), click **🚀 Video Publishing Studio** in the top bar to toggle the docked studio.
- Or click **🚀 Studio** on any active clip card.
- URL state is synchronized: `?clip=<id>&studio=open`.
- On desktop screens ($\ge 1024$px), the studio is docked side-by-side with the clip queue. On smaller screens ($< 1024$px), it slides out as a drawer overlay.

### External Video Ingestion
To publish an externally rendered video:
1. Click **➕ Add External Video** in the top bar.
2. Select an `.mp4`, `.mov`, or `.webm` file.
3. Optionally provide a title and assign a target channel.
4. Click **Upload & Ingest**.
5. The video is ingested with `source="external"`, registered in the clips queue, and immediately opened in the Publishing Studio.

### Publishing Form Features
- **CTR Title Archetypes**: One-click AI hooks (*Curiosity Gap*, *Shock Factor*, *Question*).
- **SEO Tags & Category**: Configurable with channel defaults.
- **Privacy & Scheduling**: Switch between Instant Publish (`public` or `unlisted`) and Peak-Hour Scheduling (`scheduled_at`).
- **Autosave Drafts**: Edits to drafts are debounced (500ms) and saved via `PATCH /api/clips/{id}/publish-draft`.
- **Outbox Jobs**: Monitor real-time upload progress, view YouTube watch links, and retry failed jobs.

---

## 8. Troubleshooting Runbook

| Symptom | Cause | Solution |
| :--- | :--- | :--- |
| `invalid_grant` error during publish | OAuth refresh token expired (7-day Testing mode limit) or revoked. | Go to Channels page (`/channels` or `/app/channels`), click **Connect YouTube Account**, re-authorize, and promote GCP OAuth to Production. |
| YouTube Channel Mismatch Warning dialog | Google OAuth signed into personal profile or wrong Brand Account. | In the dialog, select **Rebind** if you intended to switch channels, or **Cancel** and re-authenticate selecting the proper Brand Account from Google's picker. |
| Pre-Upload Guard: `PAUSED_NEEDS_ATTENTION` | 1-unit pre-flight check detected revoked token or channel suspension before burning 1,600 units. | Open `/channels`, verify channel status, click **Verify Channel**, and re-authenticate if necessary. |
| Quota Exceeded (403 `quotaExceeded`) | Reached 10,000 units or 6 upload limit for the current Pacific day. | Wait until midnight Pacific (08:00 UTC) for automatic reset, or request a quota extension from Google via the GCP Console. |
| Uploaded video marked `Private` by YouTube | Unverified OAuth project visibility restriction. | Verify project in GCP Console or publish as `unlisted` and change visibility manually in YouTube Studio. |
| File Explorer does not highlight video | Path contains special characters or explorer.exe syntax error. | Shorts Engine uses `/select,"<path>"`. If Explorer fails, the UI automatically copies the absolute file path to your clipboard as a fallback. |
