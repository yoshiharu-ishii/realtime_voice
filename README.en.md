# Realtime Voice Chat (Push-to-Talk / Hands-free)

[🇯🇵 日本語](README.md) | 🇬🇧 English

A realtime voice conversation app: Browser ⇄ OpenAI Realtime API.
Two **transports** — WebRTC (direct, default) and WebSocket (FastAPI relay) —
and two **conversation modes** — Push-to-Talk and hands-free (server VAD) —
freely combinable from the UI. The OpenAI API key lives only in the server's
`.env` and never reaches the browser (WebRTC clients only receive ephemeral
keys that expire within minutes).

## Demo

<img src="docs/media/demo.gif" alt="Demo: hands-free conversation and automatic web search (1.75x speed)" width="640" loading="lazy">

An **unscripted, end-to-end recording**: a synthesized voice talks to the app
in hands-free (VAD) mode, the AI answers with voice, and asking about today's
weather in Tokyo triggers the web-search tool automatically.
🔊 With audio (55s, 1.1MB): [docs/media/demo.mp4](docs/media/demo.mp4)

| Hands-free call | Web search firing | Conversation history |
|---|---|---|
| ![call](docs/media/shot_talk.png) | ![search](docs/media/shot_search.png) | ![history](docs/media/shot_history.png) |

The demo is recorded automatically by [tools/record_demo.js](tools/record_demo.js)
(fake microphone + in-page recording — no OS screen-capture permission needed).

## Highlights

- **Two transports**: WebRTC direct (audio/events go browser ⇄ OpenAI; the server
  only mints ephemeral keys, proxies web search, and receives history) and
  WebSocket relay (the server sees every event — natural place for auditing,
  tool execution and history)
- **Two modes**: Push-to-Talk (manual commit) and hands-free with `semantic_vad`
  (automatic turn detection, barge-in). OpenAI-side `noise_reduction: near_field`
  suppresses false triggers from impact noises
- **Careful audio path**: capture at the device's native sample rate, resample to
  24kHz PCM16 in an AudioWorklet (forcing a 24kHz AudioContext corrupts audio on
  virtual microphones — learned the hard way)
- **Function calling**: the model requests `web_search`; the server executes it via
  the OpenAI Responses API and feeds the result back
- **Personas**: character + voice defined in `personas/*.md` (frontmatter + instructions);
  drop in a file and it appears in the UI
- **Production-ready**: Docker, Terraform (two state-separated stacks: Cognito auth
  foundation / disposable runtime), one-command deploy to ECS Fargate behind an ALB
  with ACM TLS — live deployment reachable at a custom domain when running

## Quick start

Requires [uv](https://docs.astral.sh/uv/) and an OpenAI API key.

```bash
cd backend
cp .env.example .env   # set OPENAI_API_KEY
uv sync
uv run uvicorn main:app --port 8000
# open http://localhost:8000 (microphone permission required)
```

Or with Docker:

```bash
docker compose up --build   # same port 8000; history persists in a named volume
```

Optional Cognito authentication turns on when `COGNITO_*` variables are set
(server-side login gate, PKCE, tokens kept in cookies — never in URLs).

## Production deployment

Two Terraform stacks under `infra/` with separated state:

- `infra/auth` — Cognito (user accounts live here; deletion-protected, never destroyed casually)
- `infra/service` — ECS Fargate (ARM64) + ALB + ACM + Route53 + EFS + ECR; safe to
  destroy and rebuild at will (`terraform apply` + `./deploy.sh` brings it back)

```bash
cd infra/auth && terraform init && terraform apply
cd ../service && terraform init
echo 'openai_api_key = "sk-..."' > secrets.auto.tfvars
terraform apply
cd ../.. && ./deploy.sh
```

## Documentation

Detailed docs are in Japanese:

- [docs/architecture.md](docs/architecture.md) — how everything works, in diagrams
- [docs/aws_architecture.md](docs/aws_architecture.md) — AWS architecture and data paths
- [docs/deployment.md](docs/deployment.md) — delivery pipeline, secrets, teardown, troubleshooting

## License

[MIT](LICENSE)
