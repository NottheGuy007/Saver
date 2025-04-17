import os
import json
import time
from flask import Flask, render_template, redirect, request, url_for, session
from dotenv import load_dotenv
import google_auth_oauthlib.flow
import googleapiclient.discovery
import googleapiclient.errors
import praw
import tempfile

# Load environment variables
load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "your-secret-key")  # Replace with a secure key

# YouTube API setup
YOUTUBE_SCOPES = ["https://www.googleapis.com/auth/youtube.readonly"]
YOUTUBE_API_SERVICE_NAME = "youtube"
YOUTUBE_API_VERSION = "v3"

# Reddit API setup
REDDIT_REDIRECT_URI = "https://saved-hub.vercel.app/reddit-callback"  # Update to your Vercel URL
REDDIT_CLIENT_ID = os.getenv("REDDIT_CLIENT_ID")
REDDIT_CLIENT_SECRET = os.getenv("REDDIT_CLIENT_SECRET")
REDDIT_USER_AGENT = os.getenv("REDDIT_USER_AGENT")

# YouTube client secret JSON from environment variable
YOUTUBE_CLIENT_SECRET_JSON = os.getenv("YOUTUBE_CLIENT_SECRET_JSON")

# Session state simulation (Flask session)
def init_session():
    if "youtube_credentials" not in session:
        session["youtube_credentials"] = None
    if "reddit_access_token" not in session:
        session["reddit_access_token"] = None
    if "youtube_api" not in session:
        session["youtube_api"] = None
    if "reddit" not in session:
        session["reddit"] = None
    if "youtube_videos" not in session:
        session["youtube_videos"] = []
    if "reddit_posts" not in session:
        session["reddit_posts"] = []
    if "last_sync_time" not in session:
        session["last_sync_time"] = 0
    if "sync_interval" not in session:
        session["sync_interval"] = 60  # Sync every 60 seconds
    if "oauth_state" not in session:
        session["oauth_state"] = None
    if "oauth_flow" not in session:
        session["oauth_flow"] = None

# Create temporary file for YouTube client secret JSON
def create_temp_client_secret_file():
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as temp_file:
        temp_file.write(YOUTUBE_CLIENT_SECRET_JSON)
        return temp_file.name

def get_youtube_api(credentials):
    """Initialize YouTube API client with credentials."""
    return googleapiclient.discovery.build(
        YOUTUBE_API_SERVICE_NAME, YOUTUBE_API_VERSION, credentials=credentials
    )

def fetch_youtube_saved_videos(youtube_api):
    """Fetch saved (liked) videos from YouTube."""
    try:
        videos = []
        request = youtube_api.videos().list(
            part="snippet", myRating="like", maxResults=10
        )
        response = request.execute()
        for item in response.get("items", []):
            videos.append({
                "title": item["snippet"]["title"],
                "url": f"https://www.youtube.com/watch?v={item['id']}",
                "thumbnail": item["snippet"]["thumbnails"]["default"]["url"]
            })
        return videos
    except googleapiclient.errors.HttpError as e:
        return []

def fetch_reddit_saved_posts(reddit):
    """Fetch saved posts from Reddit."""
    try:
        saved_posts = []
        for item in reddit.user.me().saved(limit=10):
            if hasattr(item, "title"):  # Submission (post)
                saved_posts.append({
                    "title": item.title,
                    "url": f"https://reddit.com{item.permalink}",
                    "subreddit": item.subreddit.display_name
                })
        return saved_posts
    except Exception as e:
        return []

def sync_content():
    """Sync YouTube and Reddit content if logged in."""
    current_time = time.time()
    if current_time - session["last_sync_time"] >= session["sync_interval"]:
        if session.get("youtube_api"):
            session["youtube_videos"] = fetch_youtube_saved_videos(session["youtube_api"])
        if session.get("reddit"):
            session["reddit_posts"] = fetch_reddit_saved_posts(session["reddit"])
        session["last_sync_time"] = current_time

# Routes
@app.route("/")
def index():
    init_session()
    sync_content()
    if not session.get("youtube_credentials") and not session.get("reddit"):
        return redirect(url_for("login"))
    return render_template("content.html",
                           youtube_videos=session["youtube_videos"],
                           reddit_posts=session["reddit_posts"],
                           last_sync_time=time.ctime(session["last_sync_time"]))

@app.route("/login")
def login():
    init_session()
    return render_template("login.html")

@app.route("/youtube-login")
def youtube_login():
    client_secret_file = create_temp_client_secret_file()
    try:
        flow = google_auth_oauthlib.flow.InstalledAppFlow.from_client_secrets_file(
            client_secret_file, YOUTUBE_SCOPES
        )
        flow.redirect_uri = "https://saved-hub.vercel.app/youtube-callback"  # Update to your Vercel URL
        authorization_url, state = flow.authorization_url(
            access_type="offline", include_granted_scopes="true"
        )
        session["oauth_state"] = state
        session["oauth_flow"] = flow.__dict__  # Serialize flow object (simplified)
        return redirect(authorization_url)
    finally:
        os.unlink(client_secret_file)

@app.route("/reddit-login")
def reddit_login():
    reddit = praw.Reddit(
        client_id=REDDIT_CLIENT_ID,
        client_secret=REDDIT_CLIENT_SECRET,
        redirect_uri=REDDIT_REDIRECT_URI,
        user_agent=REDDIT_USER_AGENT
    )
    auth_url = reddit.auth.url(
        scopes=["identity", "read", "save"],
        state="uniqueKey",
        duration="temporary"
    )
    return redirect(auth_url)

@app.route("/youtube-callback")
def youtube_callback():
    if "code" not in request.args or "state" not in request.args:
        return "Error: Missing code or state parameter", 400
    if session.get("oauth_state") is None or session.get("oauth_state") != request.args["state"]:
        return "Error: OAuth state mismatch", 400

    # Reconstruct the flow object
    client_secret_file = create_temp_client_secret_file()
    try:
        flow = google_auth_oauthlib.flow.InstalledAppFlow.from_client_secrets_file(
            client_secret_file, YOUTUBE_SCOPES
        )
        flow.redirect_uri = "https://saved-hub.vercel.app/youtube-callback"
        flow.fetch_token(code=request.args["code"])
        session["youtube_credentials"] = flow.credentials.to_json()
        session["youtube_api"] = get_youtube_api(flow.credentials)
        sync_content()
        return redirect(url_for("index"))
    finally:
        os.unlink(client_secret_file)

@app.route("/reddit-callback")
def reddit_callback():
    if "code" not in request.args:
        return "Error: Missing code parameter", 400
    try:
        reddit = praw.Reddit(
            client_id=REDDIT_CLIENT_ID,
            client_secret=REDDIT_CLIENT_SECRET,
            redirect_uri=REDDIT_REDIRECT_URI,
            user_agent=REDDIT_USER_AGENT
        )
        reddit.auth.authorize(request.args["code"])
        session["reddit"] = reddit.__dict__  # Serialize reddit object (simplified)
        sync_content()
        return redirect(url_for("index"))
    except Exception as e:
        return f"Reddit login failed: {e}", 400

@app.route("/logout-youtube")
def logout_youtube():
    session["youtube_credentials"] = None
    session["youtube_api"] = None
    session["youtube_videos"] = []
    return redirect(url_for("index"))

@app.route("/logout-reddit")
def logout_reddit():
    session["reddit"] = None
    session["reddit_posts"] = []
    return redirect(url_for("index"))

@app.route("/sync")
def sync():
    session["last_sync_time"] = 0  # Force sync
    sync_content()
    return redirect(url_for("index"))

if __name__ == "__main__":
    app.run(debug=True)
