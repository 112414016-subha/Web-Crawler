
import streamlit as st
import requests
import pandas as pd
import time
import threading
import queue
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse
from collections import deque

# Page configuration
st.set_page_config(page_title="Web Crawler Dashboard", page_icon="🌐", layout="wide")
st.set_option("client.toolbarMode", "viewer")

st.markdown("""
<style>
[data-testid="stAppDeployButton"] {display: none !important;}
</style>
""", unsafe_allow_html=True)

# Session state
defaults = {
    "results": [], "status": "Ready", "crawl_running": False,
    "crawl_updates": None, "stop_event": None
}
for key, value in defaults.items():
    if key not in st.session_state:
        st.session_state[key] = value


# Detect navigation
def detect_navigation(response, url):
    soup = BeautifulSoup(response.text, "html.parser")
    if soup.title and soup.title.string:
        return soup.title.string.strip()
    heading = soup.find("h1")
    return heading.get_text(strip=True) if heading else urlparse(url).path.strip("/") or "Home"


# Detect HTTP methods
def detect_method(response):
    methods = set(response.headers.get("Allow", "").upper().split(","))
    if "text/html" in response.headers.get("Content-Type", "").lower():
        soup = BeautifulSoup(response.text, "html.parser")
        methods.update((f.get("method") or "GET").upper() for f in soup.find_all("form"))
    methods.discard("")
    return ", ".join(sorted(methods - {"GET"})) or "GET"


# Detect session
def detect_session(session, response):
    return "Yes" if session.cookies or response.headers.get("Set-Cookie") else "No"


# Background crawler
def crawl_website(start_url, max_pages, stop_event, updates):
    visited, url_queue = set(), deque([start_url])
    session = requests.Session()
    session.headers.update({"User-Agent": "Mozilla/5.0 WebsiteCrawler"})

    original = urlparse(start_url)
    host = original.hostname.lower()
    port = original.port or (443 if original.scheme == "https" else 80)

    try:
        while url_queue and len(visited) < max_pages:
            if stop_event.is_set():
                break

            url = url_queue.popleft().split("#")[0]
            if url in visited:
                continue
            visited.add(url)
            start = time.perf_counter()

            try:
                response = session.get(url, timeout=3, allow_redirects=True)
                elapsed = (time.perf_counter() - start) * 1000

                row = {
                    "Navigation": detect_navigation(response, url),
                    "URL": url,
                    "Method": detect_method(response),
                    "Session": detect_session(session, response),
                    "Response": response.status_code,
                    "Response Time": f"{elapsed:.2f} ms"
                }
                updates.put(("row", row))

                if response.status_code == 200 and "text/html" in response.headers.get("Content-Type", "").lower():
                    soup = BeautifulSoup(response.text, "html.parser")

                    for link in soup.find_all("a", href=True):
                        if stop_event.is_set():
                            break

                        full_url = urljoin(response.url, link["href"]).split("#")[0]
                        parsed = urlparse(full_url)

                        try:
                            link_port = parsed.port or (443 if parsed.scheme == "https" else 80)
                        except ValueError:
                            continue

                        if (parsed.scheme in ("http", "https") and parsed.hostname
                                and parsed.hostname.lower() == host and link_port == port
                                and full_url not in visited and full_url not in url_queue):
                            url_queue.append(full_url)

            except requests.exceptions.Timeout:
                updates.put(("row", {
                    "Navigation": "Unknown", "URL": url, "Method": "GET",
                    "Session": "Unknown", "Response": "Timeout", "Response Time": "-"
                }))

            except requests.exceptions.RequestException as error:
                updates.put(("row", {
                    "Navigation": "Unknown", "URL": url, "Method": "GET",
                    "Session": "Unknown", "Response": f"Error: {error}", "Response Time": "-"
                }))

        status = ("Crawling stopped" if stop_event.is_set()
                  else f"Stopped at maximum limit of {max_pages} pages" if url_queue
                  else "Crawling completed")
        updates.put(("done", status))

    except Exception as error:
        updates.put(("done", f"Crawling error: {error}"))
    finally:
        session.close()


# Dashboard
st.title("🌐 Web Crawler Dashboard")
st.write("Explore website navigation, HTTP methods, sessions, response codes, and response times.")
st.info("Only crawl websites you own or have permission to test.")

# Sidebar settings
st.sidebar.header("Crawler Settings")
max_pages = st.sidebar.number_input("Maximum pages to crawl", 1, 500, 20)
st.sidebar.caption("The crawler follows links on the same host and port.")

url = st.text_input("Enter Website URL", placeholder="https://example.com")


# Live dashboard
@st.fragment(run_every=0.5)
def crawler_dashboard(url, max_pages):
    col1, col2, col3 = st.columns(3)
    start_button = col1.button("🚀 Crawl Website", use_container_width=True,
                               disabled=st.session_state.crawl_running)
    stop_button = col2.button("⏹ Stop Crawling", use_container_width=True,
                              disabled=not st.session_state.crawl_running)
    clear_button = col3.button("🗑 Clear Results", use_container_width=True,
                               disabled=st.session_state.crawl_running)

    # Start crawling
    if start_button:
        if not url.strip():
            st.error("Please enter a website URL.")
        else:
            start_url = url if url.startswith(("http://", "https://")) else "https://" + url
            parsed = urlparse(start_url)

            if (parsed.scheme not in ("http", "https") or not parsed.hostname
                    or parsed.username or parsed.password):
                st.error("Please enter a valid HTTP or HTTPS URL.")
            else:
                st.session_state.results = []
                st.session_state.status = "Crawling..."
                st.session_state.stop_event = threading.Event()
                st.session_state.crawl_updates = queue.Queue()
                st.session_state.crawl_running = True

                threading.Thread(
                    target=crawl_website,
                    args=(start_url, int(max_pages), st.session_state.stop_event,
                          st.session_state.crawl_updates),
                    daemon=True
                ).start()

    # Stop and clear
    if stop_button and st.session_state.stop_event:
        st.session_state.stop_event.set()
        st.session_state.status = "Stopping crawler..."

    if clear_button:
        st.session_state.results = []
        st.session_state.status = "Ready"

    # Receive live updates
    if st.session_state.crawl_updates is not None:
        while True:
            try:
                event, data = st.session_state.crawl_updates.get_nowait()
                if event == "row":
                    st.session_state.results.append(data)
                elif event == "done":
                    st.session_state.status = data
                    st.session_state.crawl_running = False
            except queue.Empty:
                break

    # Status and progress
    st.write(f"**Status:** {st.session_state.status}")
    if st.session_state.crawl_running:
        if st.session_state.stop_event and st.session_state.stop_event.is_set():
            st.warning("⏹ Stopping crawler...")
        else:
            st.info("🔄 Crawling website...")

    total = len(st.session_state.results)
    st.progress(min(total / int(max_pages), 1.0))

    # Results
    if st.session_state.results:
        df = pd.DataFrame(st.session_state.results)
        successful = sum(isinstance(r, int) and 200 <= r < 400 for r in df["Response"])

        c1, c2, c3 = st.columns(3)
        c1.metric("Total Pages Checked", len(df))
        c2.metric("Successful Responses", successful)
        c3.metric("Errors / Other Responses", len(df) - successful)

        st.subheader("📊 Crawl Results")
        st.dataframe(
            df,
            column_config={"URL": st.column_config.LinkColumn("URL", display_text=None)},
            use_container_width=True,
            hide_index=True
        )
    else:
        st.info("No crawl results available yet.")

    # CSV download
    columns = ["Navigation", "URL", "Method", "Session", "Response", "Response Time"]
    csv_df = pd.DataFrame(st.session_state.results, columns=columns)

    st.download_button(
        "⬇️ Download Results as CSV file",
        csv_df.to_csv(index=False).encode("utf-8"),
        "web_crawler_results.csv",
        "text/csv",
        use_container_width=True,
        key="download_crawler_csv"
    )


# Run dashboard
crawler_dashboard(url, int(max_pages))
