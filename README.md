## Überblick
Dieses Projekt stellt ein **FastAPI-Backend** bereit, welches Chat-Funktionalität ermöglicht:
- **Automatisierte Antworten** via OpenAI (falls entsprechende `.env`-Variablen gesetzt sind),
- **Fallback-Echo**, wenn kein `OPENAI_KEY` und `ASSISTANT_ID` hinterlegt sind (dann antwortet der Bot nur mit einer leichten Verzögerung),
- **Manager-Ansicht** unter `/manager`, um aktive Chats zu sehen und direkt zu interagieren.

## Voraussetzungen
- **Docker** und **docker-compose** (empfohlen, um das Projekt schnell zu starten).
- Eine **`.env`-Datei**, die alle relevanten Variablen enthält, zum Beispiel:

```ini
# Beispiel .env
OPENAI_KEY=...
ASSISTANT_ID=...

DATABASE_URL=postgresql+asyncpg://user:pass@dbhost:5432/dbname

POSTGRES_USER=user
POSTGRES_PASSWORD=pass
POSTGRES_DB=dbname
POSTGRES_HOST=dbhost
POSTGRES_PORT=5432
```
> **Hinweis**: Ohne `OPENAI_KEY` und `ASSISTANT_ID` erhalten die Benutzer nur eine verzögerte Echo-Antwort statt echter KI-Antworten.

## Installation & Start mit Docker
1. `.env` anlegen und entsprechend befüllen (siehe oben).  
2. Docker-Container bauen und starten:
   ```bash
   docker-compose build
   docker-compose up -d
   ```
3. Das Backend läuft nun auf Port **8000** (z. B. erreichbar unter `http://localhost:8000`).

## Manuelle Ausführung (ohne Docker)
1. **Python >= 3.10** ist erforderlich.
2. Abhängigkeiten installieren:
   ```bash
   pip install -r requirements.txt
   ```
3. Anwendung starten:
   ```bash
   uvicorn main:app --host 0.0.0.0 --port 8000
   ```
4. Wichtige Endpunkte:
   - `GET /manager/chats`: Liste aller Chats.
   - `GET /history?chat_id=...`: Chatverlauf für eine bestimmte Chat-ID.
   - `WS /ws?chat_id=...`: WebSocket für Nutzer.
   - `WS /manager/ws`: WebSocket für Manager (Broadcast).
   - `POST /manager/send`: Senden einer Nachricht als Manager.
   - `http://localhost:8000/manager`: Manager-Übersicht (falls ein Frontend genutzt wird).

## Weitere Hinweise
- In diesem Projekt sind bereits ein **Dockerfile** sowie ein **docker-compose.yml** enthalten, um das Deployment zu vereinfachen.
- Man muss lediglich die `.env` konfigurieren und dann mit Docker Compose starten.
- In Zukunft wird es ein ausführlicheres README geben, das detailliert alle Schritte erklärt.
