"""
rca-browser: On-demand browser microservice for AIDA RCA policy checks.

Uses nodriver (stealthy Chrome automation) to solve reCAPTCHA v2 on
https://www.aida.info.ro/polite-rca and return policy data.

Unlike ghiseul-browser/ihidro-browser which keep a persistent browser,
this service starts Chromium on each request and stops it after.
This keeps idle memory usage at zero — suitable for daily polling.

Endpoints:
  GET  /health     - Health check (no browser needed)
  POST /check-rca  - Check RCA policy validity for a vehicle
"""

import asyncio
import base64
import io
import json
import logging
import os
import random
import re
import shutil
import sys
import tempfile
import time
from typing import Optional

import nodriver as nd
import pytesseract
from aiohttp import web
from PIL import Image

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
HOST = os.environ.get("HOST", "0.0.0.0")
PORT = int(os.environ.get("PORT", 8194))
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()
HEADLESS = os.environ.get("HEADLESS", "true").lower() == "true"

AIDA_URL = "https://www.aida.info.ro/polite-rca"
RECAPTCHA_SITE_KEY = "6LelUWMUAAAAAMzX54WzzsPARdGRLLiroZ6N-R0c"

# Timeouts
BROWSER_STARTUP_TIMEOUT = 30  # seconds to wait for browser to start
RECAPTCHA_SOLVE_TIMEOUT = 30  # seconds to wait for reCAPTCHA solve
PAGE_LOAD_TIMEOUT = 15  # seconds to wait for page to load
FORM_SUBMIT_TIMEOUT = 15  # seconds to wait for form response

# Retry settings
MAX_CAPTCHA_RETRIES = 5

logger = logging.getLogger("rca-browser")

# Global lock to prevent concurrent browser sessions
request_lock = asyncio.Lock()
xvfb_display = None


def start_xvfb():
    """Start virtual X display for head-full Chrome in headless environments."""
    global xvfb_display
    if xvfb_display is None and os.name != "nt":
        try:
            from xvfbwrapper import Xvfb
            xvfb_display = Xvfb(width=1920, height=1080, colordepth=24)
            xvfb_display.start()
            logger.info("Virtual display started (1920x1080x24)")
        except ImportError:
            logger.warning("xvfbwrapper not installed, assuming display available")


async def create_browser() -> nd.Browser:
    """Create a new browser instance for a single request."""
    if HEADLESS:
        start_xvfb()

    options = nd.Config()
    options.sandbox = False
    options.add_argument("--disable-software-rasterizer")
    options.add_argument("--disable-gpu")
    options.add_argument("--ignore-certificate-errors")
    options.add_argument("--ignore-ssl-errors")
    options.add_argument("--use-gl=swiftshader")
    options.add_argument("--no-first-run")
    options.add_argument("--no-default-browser-check")
    # Realistic window size — reCAPTCHA checks viewport dimensions
    options.add_argument("--window-size=1920,1080")

    language = os.environ.get("LANG", "ro-RO")
    options.lang = language

    browser = await nd.Browser.create(config=options)
    logger.info("Browser created successfully")
    return browser


def stop_browser(browser: Optional[nd.Browser]):
    """Stop a browser instance, releasing all resources."""
    if browser is not None:
        try:
            browser.stop()
            logger.info("Browser stopped")
        except Exception as e:
            logger.debug("Browser stop failed (non-critical): %s", e)


def _safe_evaluate_result(result) -> str:
    """Safely convert a tab.evaluate() result to a string.

    nodriver returns an ExceptionDetails object (not a string) when JS
    evaluation fails.  Detect that and convert to a readable error string.
    """
    if result is None:
        return ""
    type_name = type(result).__name__
    if type_name == "ExceptionDetails" or "ExceptionDetails" in type_name:
        text = getattr(result, "text", None) or str(result)
        raise RuntimeError(f"JS evaluation error: {text}")
    if isinstance(result, str):
        return result
    return str(result)


async def execute_js(tab: nd.Tab, script: str, await_promise: bool = False) -> str:
    """Execute JavaScript in the tab and return the result."""
    result = await tab.evaluate(script, await_promise=await_promise)
    return _safe_evaluate_result(result)


async def _simulate_human_behavior(tab: nd.Tab):
    """
    Simulate realistic human behavior on the page before interacting
    with reCAPTCHA.  This builds trust with the reCAPTCHA risk engine:
    - Random mouse movements across the page
    - Scroll up and down
    - Hover over form elements
    - Wait a natural amount of time
    """
    logger.debug("Simulating human behavior on page...")

    # Random mouse movements across the page body
    for _ in range(random.randint(3, 6)):
        x = random.randint(100, 900)
        y = random.randint(100, 600)
        await tab.evaluate(
            f"document.elementFromPoint({x}, {y})",
        )
        await tab.send(
            nd.cdp.input_.dispatch_mouse_event(
                type_="mouseMoved", x=x, y=y
            )
        )
        await asyncio.sleep(random.uniform(0.1, 0.4))

    # Scroll down a bit then back up
    await tab.evaluate("window.scrollBy(0, 200)")
    await asyncio.sleep(random.uniform(0.3, 0.8))
    await tab.evaluate("window.scrollBy(0, -100)")
    await asyncio.sleep(random.uniform(0.3, 0.6))

    # Hover over the search form area
    try:
        form_el = await tab.find("input", timeout=3)
        if form_el:
            await form_el.mouse_move()
            await asyncio.sleep(random.uniform(0.2, 0.5))
    except Exception:
        pass

    # Scroll back to top (where the captcha is)
    await tab.evaluate("window.scrollTo(0, 0)")
    await asyncio.sleep(random.uniform(0.5, 1.0))

    logger.debug("Human behavior simulation complete")


async def _solve_audio_challenge(
    bframe_tab: nd.Tab, anchor_tab: nd.Tab, timeout: float = 30
) -> bool:
    """
    Solve reCAPTCHA v2 via the audio challenge.

    When the image challenge appears (bframe iframe), switch to the audio
    challenge, download the MP3, transcribe it with Google Speech-to-Text,
    and submit the answer.

    Returns True if solved, False if failed.
    """
    import speech_recognition as sr
    from pydub import AudioSegment

    logger.info("Attempting audio challenge fallback...")

    # Fix bframe websocket URL
    bframe_tab.websocket_url = bframe_tab.websocket_url.replace(
        "iframe", "page"
    )

    # Step 1: Click the audio challenge button
    try:
        audio_btn = await bframe_tab.find(
            "#recaptcha-audio-button", timeout=5
        )
        if audio_btn is None:
            logger.warning("Audio challenge button not found")
            return False

        await asyncio.sleep(random.uniform(0.5, 1.5))
        await audio_btn.mouse_click()
        logger.info("Clicked audio challenge button")
        await asyncio.sleep(random.uniform(2.0, 3.0))
    except Exception as e:
        logger.error("Failed to click audio button: %s", e)
        return False

    # Step 2: Check if we got rate-limited ("Try again later")
    try:
        error_text = await execute_js(
            bframe_tab,
            "document.querySelector('.rc-audiochallenge-error-message')?.textContent || ''",
        )
        if error_text and "try again later" in error_text.lower():
            logger.warning("reCAPTCHA audio rate-limited: %s", error_text)
            return False
    except Exception:
        pass

    # Step 3: Get the audio source URL
    try:
        audio_src = await execute_js(
            bframe_tab,
            "document.querySelector('#audio-source')?.src || ''",
        )
        if not audio_src:
            # Alternative: look for the download link
            audio_src = await execute_js(
                bframe_tab,
                "document.querySelector('.rc-audiochallenge-tdownload-link')?.href || ''",
            )
        if not audio_src:
            logger.warning("Could not find audio challenge source URL")
            return False

        logger.info("Audio challenge URL: %s", audio_src[:80])
    except Exception as e:
        logger.error("Failed to get audio source: %s", e)
        return False

    # Step 4: Download the MP3 audio
    import aiohttp as ahttp

    mp3_path = "/tmp/recaptcha_audio.mp3"
    wav_path = "/tmp/recaptcha_audio.wav"

    try:
        async with ahttp.ClientSession() as session:
            async with session.get(audio_src) as resp:
                if resp.status != 200:
                    logger.warning("Audio download failed: HTTP %s", resp.status)
                    return False
                audio_data = await resp.read()
                with open(mp3_path, "wb") as f:
                    f.write(audio_data)
        logger.info("Audio downloaded: %d bytes", len(audio_data))
    except Exception as e:
        logger.error("Audio download error: %s", e)
        return False

    # Step 5: Convert MP3 to WAV (Google Speech API needs WAV)
    try:
        sound = AudioSegment.from_mp3(mp3_path)
        sound.export(wav_path, format="wav")
        logger.debug("Converted MP3 to WAV")
    except Exception as e:
        logger.error("Audio conversion error: %s", e)
        return False

    # Step 6: Transcribe with Google Speech-to-Text (free web API)
    try:
        recognizer = sr.Recognizer()
        with sr.AudioFile(wav_path) as source:
            audio = recognizer.record(source)

        transcript = recognizer.recognize_google(audio)
        logger.info("Audio transcription: '%s'", transcript)
    except sr.UnknownValueError:
        logger.warning("Speech recognition could not understand audio")
        return False
    except sr.RequestError as e:
        logger.error("Speech recognition API error: %s", e)
        return False
    except Exception as e:
        logger.error("Transcription error: %s", e)
        return False
    finally:
        # Clean up temp files
        for p in (mp3_path, wav_path):
            try:
                os.remove(p)
            except OSError:
                pass

    if not transcript:
        logger.warning("Empty transcription")
        return False

    # Step 7: Type the answer into the response field
    try:
        response_input = await bframe_tab.find(
            "#audio-response", timeout=5
        )
        if response_input is None:
            logger.warning("Audio response input not found")
            return False

        await response_input.mouse_click()
        await asyncio.sleep(random.uniform(0.3, 0.6))
        await response_input.send_keys(transcript)
        logger.info("Typed transcription into response field")
        await asyncio.sleep(random.uniform(0.5, 1.0))
    except Exception as e:
        logger.error("Failed to type response: %s", e)
        return False

    # Step 8: Click the verify button
    try:
        verify_btn = await bframe_tab.find(
            "#recaptcha-verify-button", timeout=5
        )
        if verify_btn is None:
            logger.warning("Verify button not found")
            return False

        await verify_btn.mouse_click()
        logger.info("Clicked verify button")
    except Exception as e:
        logger.error("Failed to click verify: %s", e)
        return False

    # Step 9: Wait for solve confirmation on the anchor
    start_time = time.time()
    while time.time() - start_time < timeout:
        await asyncio.sleep(1)
        try:
            is_checked = await execute_js(
                anchor_tab,
                "document.getElementById('recaptcha-anchor')?.getAttribute('aria-checked') || 'false'",
            )
            if is_checked == "true":
                logger.info(
                    "reCAPTCHA SOLVED via audio in %.1fs",
                    time.time() - start_time,
                )
                return True
        except Exception:
            pass

        # Check for "incorrect" response — reCAPTCHA may reload audio
        try:
            error_msg = await execute_js(
                bframe_tab,
                "document.querySelector('.rc-audiochallenge-error-message')?.textContent || ''",
            )
            if error_msg and (
                "multiple correct" in error_msg.lower()
                or "try again" in error_msg.lower()
                or "not correct" in error_msg.lower()
            ):
                logger.warning("Audio answer rejected: %s", error_msg.strip())
                return False
        except Exception:
            pass

    logger.warning("Audio challenge verify timed out after %.0fs", timeout)
    return False


async def solve_recaptcha_v2(tab: nd.Tab, timeout: float = RECAPTCHA_SOLVE_TIMEOUT) -> bool:
    """
    Attempt to solve reCAPTCHA v2 by clicking the checkbox.

    Strategy:
    1. Simulate human-like page interaction
    2. Click the checkbox
    3. If checkbox passes directly → done
    4. If image challenge appears → switch to audio challenge,
       transcribe with Google Speech-to-Text, submit answer

    Returns True if solved, False if failed.
    """
    logger.info("Attempting to solve reCAPTCHA v2...")

    # Wait for reCAPTCHA iframe to fully load
    await tab.sleep(random.uniform(2.5, 4.0))

    # Simulate human behavior on the page before touching captcha
    await _simulate_human_behavior(tab)

    # Extra pause — real users don't immediately click captcha
    await asyncio.sleep(random.uniform(1.0, 3.0))

    # Find the reCAPTCHA iframe
    try:
        await tab.browser.update_targets()
        recaptcha_tab = None
        for target in tab.browser.targets:
            if "recaptcha" in target.url and "anchor" in target.url:
                recaptcha_tab = target
                break

        if recaptcha_tab is None:
            logger.warning("reCAPTCHA anchor iframe not found")
            return False

        # Fix the websocket URL for iframe interaction
        recaptcha_tab.websocket_url = recaptcha_tab.websocket_url.replace(
            "iframe", "page"
        )

        # Find the checkbox
        checkbox = await recaptcha_tab.find(
            "#recaptcha-anchor", timeout=5
        )
        if checkbox is None:
            logger.warning("reCAPTCHA checkbox not found")
            return False

        # Move mouse toward checkbox area with a slight wobble
        await tab.send(
            nd.cdp.input_.dispatch_mouse_event(
                type_="mouseMoved",
                x=random.randint(180, 220),
                y=random.randint(380, 420),
            )
        )
        await asyncio.sleep(random.uniform(0.1, 0.3))

        # Click the checkbox
        await checkbox.mouse_click()
        logger.info("reCAPTCHA checkbox clicked")

        # Wait for the reCAPTCHA to be solved or challenge to appear
        start_time = time.time()
        while time.time() - start_time < timeout:
            await asyncio.sleep(1)

            # Check if the checkbox is now checked (aria-checked="true")
            try:
                is_checked = await execute_js(
                    recaptcha_tab,
                    "document.getElementById('recaptcha-anchor')?.getAttribute('aria-checked') || 'false'",
                )
                if is_checked == "true":
                    logger.info(
                        "reCAPTCHA solved (checkbox) in %.1fs",
                        time.time() - start_time,
                    )
                    return True
            except Exception:
                pass

            # Check if image challenge appeared (bframe iframe)
            await tab.browser.update_targets()
            challenge_tab = None
            for target in tab.browser.targets:
                if "recaptcha" in target.url and "bframe" in target.url:
                    challenge_tab = target
                    break

            if challenge_tab is not None:
                logger.info(
                    "Image challenge appeared — switching to audio challenge"
                )
                return await _solve_audio_challenge(
                    challenge_tab, recaptcha_tab, timeout=30
                )

        logger.warning("reCAPTCHA solve timed out after %.0fs", timeout)
        return False

    except Exception as e:
        logger.error("reCAPTCHA solve error: %s", e, exc_info=True)
        return False


async def get_recaptcha_token(tab: nd.Tab) -> str:
    """Extract the solved reCAPTCHA token from the page."""
    token = await execute_js(
        tab,
        "document.getElementById('g-recaptcha-response')?.value || ''",
    )
    if not token:
        # Try alternative: grecaptcha.getResponse()
        try:
            token = await execute_js(
                tab,
                "typeof grecaptcha !== 'undefined' ? grecaptcha.getResponse() : ''",
            )
        except Exception:
            pass
    return token


def _format_date_today() -> str:
    """Return today's date in dd.mm.yyyy format."""
    from datetime import datetime
    return datetime.now().strftime("%d.%m.%Y")


async def submit_rca_form(
    tab: nd.Tab,
    search_value: str,
    search_type: str = "numar",
    date: Optional[str] = None,
) -> dict:
    """
    Submit the RCA search form via the page's own AJAX call, then inject
    the response HTML into the DOM and read the rendered text.

    AIDA returns policy details as 1x1 pixel base64 images (anti-scraping).
    The browser renders these via CSS into visible text.  We capture both
    the raw AJAX JSON and the rendered innerText.

    Args:
        tab: Browser tab (must be on the AIDA page with solved captcha)
        search_value: Plate number or VIN
        search_type: "numar" (registration number) or "serie" (VIN)
        date: Date in dd.mm.yyyy format (defaults to today)

    Returns:
        dict with raw JSON response + rendered text
    """
    if date is None:
        date = _format_date_today()

    # Get the reCAPTCHA token
    captcha_token = await get_recaptcha_token(tab)
    if not captcha_token:
        raise RuntimeError("No reCAPTCHA token available")

    logger.info(
        "Submitting RCA form: type=%s, value=%s, date=%s, token=%s...",
        search_type,
        search_value,
        date,
        captcha_token[:20],
    )

    # Submit via fetch(), inject HTML into DOM, read rendered text
    submit_js = """
    (async () => {
        const formData = new URLSearchParams();
        formData.append('CriteriuCautare', PLACEHOLDER_TYPE);
        formData.append('SerieNumar', PLACEHOLDER_VALUE);
        formData.append('DataReferinta', PLACEHOLDER_DATE);
        formData.append('EsteDeAcordCuConditiile', 'true');
        formData.append('Captcha', PLACEHOLDER_TOKEN);
        formData.append('_TipMesajWarningAfisat', '');

        const resp = await fetch('/politerca/cautare', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/x-www-form-urlencoded',
                'X-Requested-With': 'XMLHttpRequest',
            },
            credentials: 'same-origin',
            body: formData.toString(),
        });

        const text = await resp.text();
        let bodyJson = null;
        let renderedText = '';
        try {
            bodyJson = JSON.parse(text);
            // Inject the HTML into a visible container so the browser renders it
            const html = bodyJson?.Value?.html || '';
            if (html) {
                let container = document.getElementById('rca-render-target');
                if (!container) {
                    container = document.createElement('div');
                    container.id = 'rca-render-target';
                    container.style.cssText = 'position:absolute;left:0;top:0;width:1200px;z-index:99999;background:white;';
                    document.body.appendChild(container);
                }
                container.innerHTML = html;
                // Force layout/paint
                container.offsetHeight;
                // Short delay for any CSS to apply
                await new Promise(r => setTimeout(r, 500));
                renderedText = container.innerText || container.textContent || '';
            }
        } catch(e) {}

        return JSON.stringify({
            status: resp.status,
            body: text,
            renderedText: renderedText,
        });
    })()
    """.replace(
        "PLACEHOLDER_TYPE", json.dumps(search_type)
    ).replace(
        "PLACEHOLDER_VALUE", json.dumps(search_value)
    ).replace(
        "PLACEHOLDER_DATE", json.dumps(date)
    ).replace(
        "PLACEHOLDER_TOKEN", json.dumps(captcha_token)
    )

    raw_result = await tab.evaluate(submit_js, await_promise=True)
    result_str = _safe_evaluate_result(raw_result)

    try:
        wrapper = json.loads(result_str)
    except (json.JSONDecodeError, TypeError):
        raise RuntimeError(f"Failed to parse fetch wrapper: {result_str[:500]}")

    http_status = wrapper.get("status", 0)
    body_text = wrapper.get("body", "")
    rendered_text = wrapper.get("renderedText", "")

    if http_status != 200:
        logger.warning("Form POST returned HTTP %s: %s", http_status, body_text[:200])

    logger.info("Rendered text from DOM: %s", rendered_text[:500])

    # Parse the body as JSON
    try:
        body_json = json.loads(body_text)
    except (json.JSONDecodeError, TypeError):
        raise RuntimeError(
            f"Failed to parse response body (HTTP {http_status}): {body_text[:500]}"
        )

    body_json["_rendered_text"] = rendered_text
    return body_json


def parse_rendered_text(text: str) -> dict:
    """
    Parse the rendered text extracted from the browser DOM after injecting
    the AIDA response HTML.

    AIDA renders policy details as images that display as text in the browser.
    We read the innerText and parse structured data from it.

    Returns a dict with extracted policy details.
    """
    result = {}

    if not text:
        return result

    # Normalize whitespace
    text = re.sub(r'\s+', ' ', text).strip()
    logger.debug("Parsing rendered text: %s", text[:300])

    # Check if policy is valid
    if "polita RCA valida" in text.lower() or "are o polita" in text.lower():
        result["is_valid"] = True
    elif "nu are" in text.lower() or "nu exista" in text.lower():
        result["is_valid"] = False

    # Try to extract dates (dd.mm.yyyy pattern)
    dates = re.findall(r'(\d{2}\.\d{2}\.\d{4})', text)
    if dates:
        # First date is usually the query date, subsequent ones are policy dates
        result["dates_found"] = dates

    # Try to extract insurer name — usually appears after "coordonate:"
    # and before any date patterns
    coord_match = re.search(r'coordonate:\s*(.+?)(?:\d{2}\.\d{2}\.\d{4}|Baza de date|Atentie|$)', text, re.IGNORECASE)
    if coord_match:
        details_text = coord_match.group(1).strip()
        if details_text:
            result["details_raw"] = details_text

    return result


def ocr_base64_images(html: str) -> list[dict]:
    """
    Extract large (non-spacer) base64 images from the AIDA HTML response
    and run Tesseract OCR on them to get the policy detail text.

    AIDA's anti-scraping technique: policy details (dates, insurer name)
    are rendered as base64 images embedded in the HTML.  The small images
    (1x1 pixel, ~83-839 bytes) are spacer/tracking pixels.  The larger
    images contain actual text.

    Returns a list of dicts: [{"index": int, "size": int, "text": str}, ...]
    """
    results = []
    pattern = re.compile(r'src="data:image/(?:jpeg|jpg|png);base64,([^"]+)"')

    for i, match in enumerate(pattern.finditer(html)):
        b64_data = match.group(1)
        try:
            raw_bytes = base64.b64decode(b64_data)
        except Exception:
            continue

        # Skip small spacer images (1x1 pixel spacers are ~83-839 bytes)
        if len(raw_bytes) < 1500:
            continue

        # OCR the image
        try:
            img = Image.open(io.BytesIO(raw_bytes))
            # Convert to grayscale for better OCR accuracy
            img = img.convert("L")
            text = pytesseract.image_to_string(
                img, lang="ron", config="--psm 6"
            ).strip()
            logger.info(
                "OCR image #%d (%dx%d, %d bytes): '%s'",
                i, img.size[0], img.size[1], len(raw_bytes), text,
            )
            results.append({
                "index": i,
                "size": len(raw_bytes),
                "width": img.size[0],
                "height": img.size[1],
                "text": text,
            })
        except Exception as e:
            logger.warning("OCR failed for image #%d: %s", i, e)

    return results


def parse_ocr_results(ocr_images: list[dict]) -> dict:
    """
    Parse OCR text from the extracted AIDA images to get structured
    policy details.

    Known image patterns:
    - Dates image (~438x58): contains "data de inceput valabilitate: DD.MM.YYYY"
      and "data de sfarsit valabilitate: DD.MM.YYYY"
    - Insurer image (~627x29): contains 'emisa de societatea ...'

    Returns a dict with:
      - valid_from: str (DD.MM.YYYY) or None
      - valid_to: str (DD.MM.YYYY) or None
      - insurer: str or None
    """
    result = {
        "valid_from": None,
        "valid_to": None,
        "insurer": None,
    }

    # Combine all OCR text for searching
    all_texts = [img["text"] for img in ocr_images if img.get("text")]

    for text in all_texts:
        # Extract start date
        start_match = re.search(
            r'(?:inceput|început)\s*valabilitate[:\s]*(\d{2}\.\d{2}\.\d{4})',
            text, re.IGNORECASE,
        )
        if start_match:
            result["valid_from"] = start_match.group(1)

        # Extract end date
        end_match = re.search(
            r'(?:sfarsit|sfâr[sș]it)\s*valabilitate[:\s]*(\d{2}\.\d{2}\.\d{4})',
            text, re.IGNORECASE,
        )
        if end_match:
            result["valid_to"] = end_match.group(1)

        # Extract insurer name
        insurer_match = re.search(
            r'emisa\s+de\s+societatea\s+(.+)',
            text, re.IGNORECASE,
        )
        if insurer_match:
            insurer = insurer_match.group(1).strip()
            # Clean up common OCR artifacts
            insurer = re.sub(r'\s+', ' ', insurer)
            result["insurer"] = insurer

        # Fallback: if we find dates in the text but didn't match the
        # structured patterns, try to extract them positionally
        if not result["valid_from"] or not result["valid_to"]:
            dates = re.findall(r'(\d{2}\.\d{2}\.\d{4})', text)
            if len(dates) >= 2:
                if not result["valid_from"]:
                    result["valid_from"] = dates[0]
                if not result["valid_to"]:
                    result["valid_to"] = dates[1]

    logger.info(
        "Parsed OCR results: valid_from=%s, valid_to=%s, insurer=%s",
        result["valid_from"], result["valid_to"], result["insurer"],
    )

    return result


# ---------------------------------------------------------------------------
# API Handlers
# ---------------------------------------------------------------------------

async def handle_health(request: web.Request) -> web.Response:
    """Health check endpoint — no browser needed."""
    return web.json_response({"status": "ok", "service": "rca-browser"})


async def handle_check_rca(request: web.Request) -> web.Response:
    """
    POST /check-rca
    Body: {
        "plate": "B123ABC",           // plate number OR VIN (required)
        "search_type": "numar",       // "numar" (plate) or "serie" (VIN), default "numar"
        "date": "10.03.2026"          // optional, defaults to today (dd.mm.yyyy)
    }

    Starts a browser, navigates to AIDA, solves reCAPTCHA v2,
    submits the form, and returns the parsed result.
    """
    # Serialize requests — only one browser at a time (on-demand)
    async with request_lock:
        browser = None
        try:
            body = await request.json()
            plate = body.get("plate", "").strip().upper()
            search_type = body.get("search_type", "numar")
            date = body.get("date")

            if not plate:
                return web.json_response(
                    {"status": "error", "message": "plate is required"},
                    status=400,
                )

            if search_type not in ("numar", "serie"):
                return web.json_response(
                    {
                        "status": "error",
                        "message": "search_type must be 'numar' or 'serie'",
                    },
                    status=400,
                )

            logger.info(
                "=== Starting RCA check: %s=%s, date=%s ===",
                search_type,
                plate,
                date or "today",
            )

            # Step 1: Create browser
            logger.info("Step 1: Starting browser...")
            browser = await create_browser()

            # Step 2: Navigate to AIDA page
            logger.info("Step 2: Navigating to AIDA...")
            tab = await browser.get(AIDA_URL)
            await tab.sleep(3)  # Wait for page + reCAPTCHA JS to load

            # Verify we're on the right page
            page_url = await execute_js(tab, "location.href")
            logger.info("Current URL: %s", page_url)

            # Step 3: Solve reCAPTCHA (with retries)
            captcha_solved = False
            for attempt in range(1, MAX_CAPTCHA_RETRIES + 1):
                logger.info(
                    "Step 3: Solving reCAPTCHA (attempt %d/%d)...",
                    attempt,
                    MAX_CAPTCHA_RETRIES,
                )

                if attempt > 1:
                    # Reload page for fresh captcha
                    logger.info("Reloading page for fresh captcha...")
                    tab = await browser.get(AIDA_URL)
                    await tab.sleep(3)

                captcha_solved = await solve_recaptcha_v2(tab)
                if captcha_solved:
                    break

                logger.warning(
                    "reCAPTCHA attempt %d failed, %s",
                    attempt,
                    "retrying..." if attempt < MAX_CAPTCHA_RETRIES else "giving up.",
                )

            if not captcha_solved:
                return web.json_response(
                    {
                        "status": "error",
                        "message": f"Failed to solve reCAPTCHA after {MAX_CAPTCHA_RETRIES} attempts",
                    },
                    status=503,
                )

            # Step 4: Submit the form
            logger.info("Step 4: Submitting RCA form...")
            response_json = await submit_rca_form(
                tab, plate, search_type, date
            )

            # Step 5: Parse the response
            logger.info("Step 5: Parsing response...")
            logger.debug("Raw response: %s", json.dumps(response_json)[:1000])

            # Check for errors
            if "ModelState" in response_json:
                # Server returned validation errors
                error_msg = response_json.get("Message", "")
                model_state = response_json.get("ModelState", {})
                return web.json_response(
                    {
                        "status": "error",
                        "message": error_msg,
                        "model_state": model_state,
                        "raw_response": response_json,
                    },
                    status=422,
                )

            # Success response: {"Value": {"html": "...", "Message": "...", "ArePolita": bool}}
            value = response_json.get("Value", {})
            html = value.get("html", "")
            message = value.get("Message", "")
            rendered_text = response_json.pop("_rendered_text", "")

            # Parse the rendered text (browser DOM innerText) for structured data
            parsed = parse_rendered_text(rendered_text)

            # OCR the base64 images embedded in the HTML for policy details
            # (AIDA hides dates and insurer name in images as anti-scraping)
            ocr_images = []
            ocr_details = {}
            if html:
                try:
                    ocr_images = ocr_base64_images(html)
                    if ocr_images:
                        ocr_details = parse_ocr_results(ocr_images)
                except Exception as e:
                    logger.warning("OCR extraction failed: %s", e)

            # Determine has_policy from rendered text (more reliable than ArePolita flag)
            has_policy = parsed.get("is_valid", False)

            logger.info(
                "=== RCA check complete: has_policy=%s, ocr_details=%s ===",
                has_policy,
                ocr_details,
            )

            return web.json_response({
                "status": "ok",
                "has_policy": has_policy,
                "message": message,
                "parsed": parsed,
                "ocr_details": ocr_details,
                "ocr_images": [
                    {"index": img["index"], "width": img["width"],
                     "height": img["height"], "text": img["text"]}
                    for img in ocr_images
                ],
                "rendered_text": rendered_text,
                "html": html,
            })

        except Exception as e:
            logger.error("check-rca error: %s", e, exc_info=True)
            return web.json_response(
                {"status": "error", "message": str(e)}, status=500
            )
        finally:
            # Always stop the browser to release resources
            stop_browser(browser)


# ---------------------------------------------------------------------------
# Application setup
# ---------------------------------------------------------------------------

def create_app() -> web.Application:
    app = web.Application()
    app.router.add_get("/health", handle_health)
    app.router.add_post("/check-rca", handle_check_rca)
    return app


if __name__ == "__main__":
    logging.basicConfig(
        format="%(asctime)s %(levelname)-8s %(message)s",
        level=LOG_LEVEL,
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[logging.StreamHandler(sys.stdout)],
    )
    # Suppress noisy loggers
    logging.getLogger("nodriver.core.browser").setLevel(logging.WARNING)
    logging.getLogger("nodriver.core.tab").setLevel(logging.WARNING)
    logging.getLogger("nodriver.core.connection").setLevel(logging.WARNING)
    logging.getLogger("websockets.client").setLevel(logging.WARNING)

    logger.info("Starting rca-browser service on %s:%d...", HOST, PORT)
    app = create_app()
    web.run_app(app, host=HOST, port=PORT)
