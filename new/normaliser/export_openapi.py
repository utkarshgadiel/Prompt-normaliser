"""Export matching Orchestrate 3.0 schemas from the running API contract.

Preserves each file's deployment servers. PyYAML is a development dependency.
Run from this directory: python export_openapi.py
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))
from api import app


def to_openapi30(value):
    if isinstance(value, list):
        return [to_openapi30(item) for item in value]
    if not isinstance(value, dict):
        return value
    out = {key: to_openapi30(item) for key, item in value.items()}
    variants = out.get("anyOf", [])
    if any(item.get("type") == "null" for item in variants):
        variants = [item for item in variants if item.get("type") != "null"]
        out.pop("anyOf")
        out["nullable"] = True
        if len(variants) == 1:
            if "$ref" in variants[0]:
                out["allOf"] = variants
            else:
                out.update(variants[0])
        else:
            out["anyOf"] = variants
    if "const" in out:
        out["enum"] = [out.pop("const")]
    return out


def build_spec(servers):
    spec = to_openapi30(app.openapi())
    spec["openapi"] = "3.0.3"
    spec["servers"] = servers
    return spec


if __name__ == "__main__":
    import yaml

    for suffix in ("json", "yaml"):
        path = ROOT / f"openapi_orchestrate.{suffix}"
        previous = (json.loads(path.read_text(encoding="utf-8")) if suffix == "json"
                    else yaml.safe_load(path.read_text(encoding="utf-8")))
        spec = build_spec(previous.get("servers", []))
        content = (json.dumps(spec, ensure_ascii=False, indent=2) + "\n" if suffix == "json"
                   else yaml.safe_dump(spec, allow_unicode=True, sort_keys=False, width=100))
        path.write_text(content, encoding="utf-8")
        print(f"Updated {path.name}; deployment URL preserved.")
