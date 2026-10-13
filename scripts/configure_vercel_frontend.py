"""Generate the API-only proxy after the actual backend project exists."""
import argparse
import json
from pathlib import Path
import re

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--api-origin", required=True)
parser.add_argument("--output", type=Path, default=Path(__file__).resolve().parents[1] / "frontend/vercel.json")
args = parser.parse_args()
if not re.fullmatch(r"https://[a-zA-Z0-9-]+\.vercel\.app", args.api_origin):
    raise SystemExit("Use the actual Vercel API project's HTTPS origin")
config = {"$schema": "https://openapi.vercel.sh/vercel.json", "framework": "vite",
          "installCommand": "npm ci", "buildCommand": "npm run build", "outputDirectory": "dist",
          "rewrites": [{"source": "/api/:path*", "destination": args.api_origin + "/api/:path*"}],
          "headers": [{"source": "/api/:path*", "headers": [{"key": "Cache-Control", "value": "no-store"}]}]}
args.output.write_text(json.dumps(config, indent=2) + "\n", "utf-8")
if json.loads(args.output.read_text("utf-8")) != config:
    raise SystemExit("Frontend deployment configuration failed readback")
print(json.dumps({"path": str(args.output), "api_origin": args.api_origin, "rewrites": "API only"}))
