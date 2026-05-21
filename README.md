# cc2-dash

Local, mobile-first dashboard for the Elegoo Centauri Carbon 2.

## Quick start

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn cc2_dash.main:app --host 0.0.0.0 --port 8088
```

Open: `http://<server-ip>:8088/`

## Notes

- Discovery uses UDP method 7000 on port 52700.
- Control and status use MQTT (`elegoo` / printer access code).
- Camera stream is direct MJPEG on `http://<printer-ip>:8080/`.
