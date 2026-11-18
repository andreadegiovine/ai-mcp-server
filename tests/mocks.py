"""Server finti per i test: Home Assistant + sito web + API esterne (meteo, news, cambi)."""
import io, json, time, math, struct, wave
from datetime import datetime, timezone, timedelta
from starlette.applications import Starlette
from starlette.responses import JSONResponse, PlainTextResponse, Response, HTMLResponse
from starlette.routing import Route

TOKEN = "test-token"
CALLS = []


def _st(eid, state, **attrs):
    return {"entity_id": eid, "state": state, "attributes": attrs,
            "last_changed": datetime.now(timezone.utc).isoformat(), "last_updated": datetime.now(timezone.utc).isoformat()}


STATES = {s["entity_id"]: s for s in [
    _st("sensor.temperatura_soggiorno", "21.4", friendly_name="Temperatura Soggiorno", unit_of_measurement="°C", device_class="temperature"),
    _st("sensor.mokka_autonomia", "187", friendly_name="Mokka-e Autonomia", unit_of_measurement="km"),
    _st("sensor.consumo_istantaneo", "1340", friendly_name="Consumo istantaneo casa", unit_of_measurement="W", device_class="power"),
    _st("light.cucina", "off", friendly_name="Luce Cucina", supported_color_modes=["brightness"]),
    _st("light.soggiorno", "on", friendly_name="Lampada Soggiorno", brightness=128),
    _st("lock.porta_ingresso", "locked", friendly_name="Serratura Ingresso"),
    _st("cover.garage", "closed", friendly_name="Basculante Garage", device_class="garage"),
    _st("cover.tapparella_camera", "open", friendly_name="Tapparella Camera", current_position=100),
    _st("camera.giardino", "idle", friendly_name="Telecamera giardino", access_token="SEGRETISSIMO", entity_picture="/api/camera_proxy/x?token=SEGRETISSIMO"),
    _st("switch.caldaia", "on", friendly_name="Caldaia"),
    _st("media_player.cucina", "idle", friendly_name="Speaker Cucina", volume_level=0.4),
    _st("weather.casa", "sunny", friendly_name="Meteo casa", temperature=18),
]}
AREAS = {"sensor.temperatura_soggiorno": "Soggiorno", "light.soggiorno": "Soggiorno", "light.cucina": "Cucina",
         "media_player.cucina": "Cucina", "cover.tapparella_camera": "Camera"}
SERVICES = [
    {"domain": "light", "services": {"turn_on": {"fields": {"brightness_pct": {"example": 50}}}, "turn_off": {}, "toggle": {}}},
    {"domain": "lock", "services": {"lock": {}, "unlock": {}}},
    {"domain": "cover", "services": {"open_cover": {}, "close_cover": {}, "set_cover_position": {}}},
    {"domain": "switch", "services": {"turn_on": {}, "turn_off": {}}},
    {"domain": "homeassistant", "services": {"restart": {}, "stop": {}, "turn_on": {}, "turn_off": {}, "reload_all": {}}},
    {"domain": "camera", "services": {"snapshot": {}}},
    {"domain": "weather", "services": {"get_forecasts": {"response": {"optional": False}}}},
    {"domain": "media_player", "services": {"play_media": {}, "media_pause": {}, "media_play": {}, "media_stop": {}, "volume_set": {}}},
    {"domain": "persistent_notification", "services": {"create": {}}},
    {"domain": "notify", "services": {"mobile_app_test": {}}},
    {"domain": "shell_command", "services": {"rm_rf": {}}},
]
MEDIA = {"fine": 0}


def _auth(req):
    return req.headers.get("authorization") == f"Bearer {TOKEN}"


def _media_tick():
    mp = STATES["media_player.cucina"]
    if mp["state"] == "playing" and MEDIA["fine"] and time.time() > MEDIA["fine"]:
        mp["state"] = "idle"


async def states(req):
    if not _auth(req):
        return PlainTextResponse("401: Unauthorized", 401)
    _media_tick()
    return JSONResponse(list(STATES.values()))


async def state(req):
    if not _auth(req):
        return PlainTextResponse("401", 401)
    _media_tick()
    eid = req.path_params["eid"]
    if eid not in STATES:
        return JSONResponse({"message": "Entity not found."}, 404)
    return JSONResponse(STATES[eid])


async def template(req):
    if not _auth(req):
        return PlainTextResponse("401", 401)
    ids = list(STATES)
    return PlainTextResponse(json.dumps({"e": ids, "a": [AREAS.get(i) for i in ids]}))


async def services(req):
    if not _auth(req):
        return PlainTextResponse("401", 401)
    return JSONResponse(SERVICES)


async def call(req):
    if not _auth(req):
        return PlainTextResponse("401", 401)
    d, s = req.path_params["d"], req.path_params["s"]
    data = await req.json()
    CALLS.append((d, s, data))
    ids = data.get("entity_id", [])
    ids = [ids] if isinstance(ids, str) else ids
    changed = []
    for i in ids:
        st = STATES.get(i)
        if not st:
            continue
        if s == "turn_on":
            st["state"] = "on"
            if "brightness_pct" in data:
                st["attributes"]["brightness"] = round(data["brightness_pct"] * 2.55)
        elif s == "turn_off":
            st["state"] = "off"
        elif s == "unlock":
            st["state"] = "unlocked"
        elif s == "open_cover":
            st["state"] = "open"
        elif s == "play_media":
            st["state"] = "playing"
            st["attributes"]["media_content_id"] = data["media_content_id"]
            MEDIA["fine"] = time.time() + 5  # ogni "brano" dura 5 secondi
        elif s == "media_pause":
            st["state"] = "paused"
        elif s == "media_play":
            st["state"] = "playing"
        elif s == "media_stop":
            st["state"] = "idle"
            MEDIA["fine"] = 0
        elif s == "volume_set":
            st["attributes"]["volume_level"] = data["volume_level"]
        changed.append(st)
    if "return_response" in req.query_params:
        return JSONResponse({"changed_states": changed, "service_response": {"weather.casa": {"forecast": [{"t": 20}]}}})
    if s == "get_forecasts":
        return PlainTextResponse("400: Service call requires responses but caller did not ask for responses", 400)
    return JSONResponse(changed)


async def history(req):
    if not _auth(req):
        return PlainTextResponse("401", 401)
    eid = req.query_params["filter_entity_id"]
    base = datetime.now(timezone.utc) - timedelta(hours=5)
    pts = [{"entity_id": eid, "state": str(20 + i * 0.5), "last_changed": (base + timedelta(minutes=30 * i)).isoformat(),
            "attributes": {"unit_of_measurement": "°C"}} for i in range(10)]
    return JSONResponse([pts])


hass_app = Starlette(routes=[
    Route("/api/states", states), Route("/api/states/{eid}", state),
    Route("/api/template", template, methods=["POST"]), Route("/api/services", services),
    Route("/api/services/{d}/{s}", call, methods=["POST"]),
    Route("/api/history/period/{ts:path}", history),
])

# --------------------------------------------------------------------------- web


def _png(colore=(200, 30, 30)):
    from PIL import Image
    b = io.BytesIO()
    Image.new("RGB", (640, 480), colore).save(b, "PNG")
    return b.getvalue()


def _pdf():
    # PDF minimale con testo
    testo = "Relazione tecnica: il fotovoltaico produce 4200 kWh annui."
    stream = f"BT /F1 12 Tf 50 750 Td ({testo}) Tj ET".encode()
    objs = [b"<< /Type /Catalog /Pages 2 0 R >>", b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
            b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    out = io.BytesIO(); out.write(b"%PDF-1.4\n"); offs = []
    for i, o in enumerate(objs, 1):
        offs.append(out.tell()); out.write(b"%d 0 obj\n" % i + o + b"\nendobj\n")
    x = out.tell()
    out.write(b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1))
    for o in offs:
        out.write(b"%010d 00000 n \n" % o)
    out.write(b"trailer << /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF" % (len(objs) + 1, x))
    return out.getvalue()


ARTICOLO = """<html><head><title>Guida alla pasta perfetta</title>
<meta property="og:image" content="http://127.0.0.1:18080/img/pasta.png"></head><body>
<nav>Menu Home Contatti</nav><article><h1>Guida alla pasta perfetta</h1>
""" + "".join(f"<p>Paragrafo {i}: " + " ".join(f"frase {i}-{j} sulla cottura della pasta numero {i*j+7} con acqua salata." for j in range(8)) + "</p>" for i in range(60)) + """
<img src="/img/pasta.png" width="640" height="480"></article><footer>Copyright</footer></body></html>"""


async def articolo(req):
    return HTMLResponse(ARTICOLO)


async def img(req):
    return Response(_png(), media_type="image/png")


async def pdf(req):
    return Response(_pdf(), media_type="application/pdf")


async def big(req):
    return Response(b"x" * 1000, media_type="application/octet-stream")


async def redirect_locale(req):
    return Response(status_code=302, headers={"location": "http://169.254.169.254/latest/meta-data"})


async def geo(req):
    return JSONResponse({"results": [
        {"name": "Priolo Gargallo", "latitude": 37.16, "longitude": 15.18, "country": "Italia", "country_code": "IT",
         "admin1": "Sicilia", "admin2": "Siracusa"}]})


async def forecast(req):
    now = datetime.now()
    hours = [(now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=h - 2)).strftime("%Y-%m-%dT%H:00") for h in range(48)]
    days = [(now + timedelta(days=d)).strftime("%Y-%m-%d") for d in range(int(req.query_params.get("forecast_days", 3)))]
    return JSONResponse({
        "current": {"time": now.strftime("%Y-%m-%dT%H:%M"), "temperature_2m": 19.2, "apparent_temperature": 18.5,
                    "relative_humidity_2m": 70, "precipitation": 0, "weather_code": 2, "cloud_cover": 40,
                    "wind_speed_10m": 12, "wind_direction_10m": 200, "wind_gusts_10m": 25, "is_day": 1, "pressure_msl": 1015},
        "hourly": {"time": hours, "temperature_2m": [18] * 48, "precipitation_probability": [10] * 48,
                   "precipitation": [0] * 48, "weather_code": [1] * 48},
        "daily": {"time": days, "weather_code": [61] * len(days), "temperature_2m_max": [22] * len(days),
                  "temperature_2m_min": [14] * len(days), "precipitation_sum": [3.2] * len(days),
                  "precipitation_probability_max": [80] * len(days), "wind_speed_10m_max": [30] * len(days),
                  "sunrise": [d + "T06:50" for d in days], "sunset": [d + "T16:55" for d in days],
                  "uv_index_max": [3] * len(days)}})


async def news(req):
    rss = """<?xml version="1.0"?><rss><channel>
<item><title>Notizia uno - Fonte A</title><link>https://example.com/1</link><pubDate>Tue, 17 Nov 2026 10:00:00 GMT</pubDate><source>Fonte A</source></item>
<item><title>Notizia due - Fonte B</title><link>https://example.com/2</link><pubDate>Tue, 17 Nov 2026 09:00:00 GMT</pubDate><source>Fonte B</source></item>
</channel></rss>"""
    return Response(rss, media_type="application/rss+xml")


async def fx(req):
    if req.query_params.get("base") == "XXX":
        return JSONResponse({"message": "not found"}, 404)
    return JSONResponse({"date": "2026-11-17", "rates": {req.query_params["symbols"]: 1.0832}})


WA = []  # richieste ricevute dal gateway WhatsApp finto


async def waha(req):
    if req.headers.get("x-api-key") != "waha-key":
        return JSONResponse({"error": "unauthorized"}, 401)
    WA.append((req.url.path, await req.json()))
    return JSONResponse({"id": f"msg{len(WA)}"})


async def callmebot(req):
    WA.append(("callmebot", dict(req.query_params)))
    if req.query_params.get("apikey") != "cmb":
        return HTMLResponse("APIKey is invalid", 200)
    return HTMLResponse("<p>Message queued. You will receive it in a few seconds.</p>")


web_app = Starlette(routes=[
    Route("/articolo", articolo), Route("/img/pasta.png", img), Route("/doc.pdf", pdf), Route("/file.bin", big),
    Route("/redirect-locale", redirect_locale), Route("/geo", geo), Route("/forecast", forecast),
    Route("/rss", news), Route("/fx/latest", fx),
    Route("/api/sendText", waha, methods=["POST"]), Route("/api/sendImage", waha, methods=["POST"]),
    Route("/callmebot", callmebot),
])


def crea_wav(path, secondi=3.0):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(8000)
        w.writeframes(b"".join(struct.pack("<h", int(3000 * math.sin(2 * math.pi * 440 * i / 8000)))
                               for i in range(int(8000 * secondi))))
